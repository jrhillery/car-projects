
import json
import logging
from argparse import ArgumentParser, Namespace
from contextlib import closing

import sys

from juicebox import JbDetails, JbInterface
from tessie import CarDetails, TessieInterface
from util import Configure


class JuiceBoxCtl(object):
    """Controls JuiceBox devices"""

    def __init__(self, args: Namespace | None = None):
        self.autoMax: bool = args.autoMax
        self.equalAmps: bool = args.equalAmps
        self.specifiedMaxAmps: int | None = args.maxAmps
        self.specifiedJuiceBoxName: str | None = args.juiceBoxName

        if self.specifiedMaxAmps is not None and self.specifiedJuiceBoxName is None:
            logging.error("Missing required JuiceBox name prefix when specifying max current")
            sys.exit(2)

        if self.specifiedMaxAmps is not None:
            if self.specifiedMaxAmps < 0:
                self.specifiedMaxAmps = 0

        with open(Configure.findParmPath().joinpath("carjuiceboxmapping.json"),
                  "r", encoding="utf-8") as mappingFile:
            carJuiceBoxMapping: dict = json.load(mappingFile)

        self.jbAttachMap: dict = carJuiceBoxMapping["attachedJuiceBoxes"]
        self.minPluggedCurrent: int = carJuiceBoxMapping["minPluggedCurrent"]
        self.totalCurrent: int = carJuiceBoxMapping["totalCurrent"]
    # end __init__(Namespace)

    @staticmethod
    def parseArgs() -> Namespace:
        """Parse command line arguments"""
        ap = ArgumentParser(description="Module to set maximum JuiceBox charge currents",
                            epilog="Just displays status when no arguments are specified")
        group = ap.add_mutually_exclusive_group()
        group.add_argument("-a", "--autoMax", action="store_true",
                           help="automatically set maximums based on cars' charging needs")
        group.add_argument("-e", "--equalAmps", action="store_true",
                           help="share current equally")
        group.add_argument("-m", "--maxAmps", type=int, nargs="?", const=99, metavar="amps",
                           help="maximum current to set (Amps)")
        ap.add_argument("juiceBoxName", nargs="?", metavar="name",
                        help="name prefix of JuiceBox to set (other gets remaining current)")

        return ap.parse_args()
    # end parseArgs()

    def getJuiceBoxForCar(self, vehicle: CarDetails, juiceBoxMap: dict) -> JbDetails:
        """Retrieve JuiceBox details corresponding to a given car"""
        juiceBoxName: str = self.jbAttachMap[vehicle.displayName]
        juiceBox: JbDetails = juiceBoxMap[juiceBoxName]

        if vehicle.pluggedInAtHome() and vehicle.chargeAmps != juiceBox.maxCurrent:
            logging.warning(f"Suspicious car-JuiceBox mapping;"
                            f" {vehicle.displayName} shows {vehicle.chargeAmps} amps offered"
                            f" but {juiceBox.name} has {juiceBox.maxCurrent} amps max")

        return juiceBox
    # end getJuiceBoxForCar(CarDetails, dict)

    def automaticallySetMax(self, jbIntrfc: JbInterface, juiceBoxes: list[JbDetails]) -> None:
        """Automatically set JuiceBox maximum currents based on each cars' charging needs"""
        tsIntrfc = TessieInterface()
        vehicles = tsIntrfc.getStateOfActiveVehicles()
        totalEnergyNeeded = 0.0

        for carDetails in vehicles:
            tsIntrfc.addBatteryHealth(carDetails)
            energyNeeded = carDetails.energyNeededC()
            msg = carDetails.currentChargingStatus()

            if energyNeeded:
                msg += f" ({energyNeeded:.1f} kWh < limit)"
            logging.info(msg)
            totalEnergyNeeded += energyNeeded
        # end for

        if len(vehicles) < 2:
            raise Exception(f"Unable to locate both cars,"
                            f" found {[car.displayName for car in vehicles]}")

        if totalEnergyNeeded:
            juiceBoxMap = {jb.name: jb for jb in juiceBoxes}
            vehicles.sort(key=lambda car: car.energyNeededC(), reverse=True)
            carA = vehicles[0]
            juiceBoxA = self.getJuiceBoxForCar(carA, juiceBoxMap)
            juiceBoxB = self.getJuiceBoxForCar(vehicles[1], juiceBoxMap)
            fairShareA = self.totalCurrent * (carA.energyNeededC() / totalEnergyNeeded)
            jbIntrfc.setNewMaximums(juiceBoxA, int(fairShareA + 0.5), juiceBoxB)
        else:
            # Share current equally when no car needs energy
            self.shareCurrentEqually(jbIntrfc, juiceBoxes)
    # end automaticallySetMax(JbInterface, list[JbDetails])

    def shareCurrentEqually(self, jbIntrfc: JbInterface, juiceBoxes: list[JbDetails]) -> None:
        """Share current equally between all JuiceBoxes"""
        jbIntrfc.setNewMaximums(juiceBoxes[0], self.totalCurrent // 2, juiceBoxes[1])
    # end shareCurrentEqually(JbInterface, list[JbDetails])

    def specifyMaxCurrent(self, jbIntrfc: JbInterface, juiceBoxes: list[JbDetails]) -> None:
        """Set the specified JuiceBox maximum current to a given value
           (the other JuiceBox gets remaining current)"""
        specifiedJuiceBox: JbDetails | None = None
        otherJuiceBox: JbDetails | None = None

        for juiceBox in juiceBoxes:
            if juiceBox.name.startswith(self.specifiedJuiceBoxName):
                specifiedJuiceBox = juiceBox
            else:
                otherJuiceBox = juiceBox
        # end for

        if not specifiedJuiceBox:
            raise Exception(
                f"Unable to locate JuiceBox starting with {self.specifiedJuiceBoxName},"
                f" found {[jb.name for jb in juiceBoxes]}")
        if not otherJuiceBox:
            raise Exception(f"Unable to locate both JuiceBoxes,"
                            f" found {[jb.name for jb in juiceBoxes]}")

        jbIntrfc.setNewMaximums(specifiedJuiceBox, self.specifiedMaxAmps, otherJuiceBox)
    # end specifyMaxCurrent(JbInterface, list[JbDetails])

    def main(self) -> None:
        logging.debug(f"Starting {' '.join(sys.argv)}")

        with closing(JbInterface(self.minPluggedCurrent, self.totalCurrent)) as jbIntrfc:
            jbIntrfc.logIn()
            juiceBoxes = jbIntrfc.getStateOfJuiceBoxes()
            juiceBoxes[:] = [jb for jb in juiceBoxes if not jb.isOffline]

            if self.autoMax:
                self.automaticallySetMax(jbIntrfc, juiceBoxes)
            elif self.equalAmps:
                self.shareCurrentEqually(jbIntrfc, juiceBoxes)
            elif self.specifiedMaxAmps is not None:
                self.specifyMaxCurrent(jbIntrfc, juiceBoxes)
            else:
                for juiceBox in juiceBoxes:
                    logging.info(juiceBox.statusStr())
                # end for
        # end with
    # end main()

# end class JuiceBoxCtl


if __name__ == "__main__":
    clArgs = JuiceBoxCtl.parseArgs()
    Configure.logToFile()
    try:
        juiceCtl = JuiceBoxCtl(clArgs)
        juiceCtl.main()
    except Exception as xcpt:
        logging.error(xcpt)
        logging.debug(f"{xcpt.__class__.__name__} suppressed:", exc_info=xcpt)
