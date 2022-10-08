
import json
import logging
from argparse import ArgumentParser, Namespace

import sys

from juicebox.jbdetails import JbDetails
from juicebox.jbinterface import JbInterface, JuiceBoxException
from tessie.cardetails import CarDetails
from tessie.tessieinterface import TessieInterface
from util.configure import Configure


class JuiceBoxCtl(object):
    """Controls JuiceBox devices"""

    def __init__(self, args: Namespace | None = None):
        self.autoMax: bool = False if args is None else args.autoMax
        self.specifiedMaxAmps: int | None = None if args is None else args.maxAmps
        self.specifiedJuiceBoxName: str | None = None if args is None else args.juiceBoxName

        if self.specifiedMaxAmps is not None and self.specifiedJuiceBoxName is None:
            logging.error("Missing required JuiceBox name prefix when max current is specified")
            sys.exit(2)

        with open(Configure.findParmPath().joinpath("carjuiceboxmapping.json"),
                  "r", encoding="utf-8") as mappingFile:
            carJuiceBoxMapping: dict = json.load(mappingFile)

        self.jbAttachMap: dict = carJuiceBoxMapping["attachedJuiceBoxes"]
        self.totalCurrent: int = carJuiceBoxMapping["totalCurrent"]
        self.jbIntrfc = JbInterface(self.totalCurrent)

        if self.specifiedMaxAmps is not None:
            if self.specifiedMaxAmps < 0:
                self.specifiedMaxAmps = 0

            if self.specifiedMaxAmps > self.jbIntrfc.totalCurrent:
                self.specifiedMaxAmps = self.jbIntrfc.totalCurrent
    # end __init__(Namespace)

    @staticmethod
    def parseArgs() -> Namespace:
        """Parse command line arguments"""
        ap = ArgumentParser(description="Module to set maximum JuiceBox charge currents",
                            epilog="Just displays status when no arguments are specified")
        group = ap.add_mutually_exclusive_group()
        group.add_argument("-a", "--autoMax", action="store_true",
                           help="automatically set maximums based on cars' charging needs")
        group.add_argument("-m", "--maxAmps", type=int, nargs="?", const=40, metavar="amps",
                           help="maximum current to set (Amps)")
        ap.add_argument("juiceBoxName", nargs="?", metavar="name",
                        help="name prefix of JuiceBox to set (other gets remaining current)")

        return ap.parse_args()
    # end parseArgs()

    def getJuiceBoxForCar(self, vehicle: CarDetails, juiceBoxMap: dict) -> JbDetails:
        juiceBoxName: str = self.jbAttachMap[vehicle.displayName]
        juiceBox: JbDetails = juiceBoxMap[juiceBoxName]

        return juiceBox
    # end getJuiceBoxForCar(CarDetails, dict)

    def automaticallySetMax(self, juiceBoxes: list[JbDetails]) -> None:
        """Automatically set JuiceBox maximum currents based on each cars' charging needs"""
        vehicles = TessieInterface().getStateOfActiveVehicles()
        totalChargeNeeded = 0.0

        for carDetails in vehicles:
            logging.info(carDetails.currentChargingStatus())
            totalChargeNeeded += carDetails.chargeNeeded()
        # end for

        if len(vehicles) < 2:
            raise JuiceBoxException(f"Unable to locate both cars,"
                                    f" found {[car.displayName for car in vehicles]}")

        if totalChargeNeeded:
            juiceBoxMap = {jb.name: jb for jb in juiceBoxes}
            vehicles.sort(key=lambda car: car.chargeNeeded(), reverse=True)
            carA = vehicles[0]
            juiceBoxA = self.getJuiceBoxForCar(carA, juiceBoxMap)
            juiceBoxB = self.getJuiceBoxForCar(vehicles[1], juiceBoxMap)
            fairShareA = (self.totalCurrent * carA.chargeNeeded()) / totalChargeNeeded
            self.jbIntrfc.setNewMaximums(juiceBoxA, int(fairShareA + 0.5), juiceBoxB)
        # end if
    # end automaticallySetMax(list[JbDetails])

    def specifyMaxCurrent(self, juiceBoxes: list[JbDetails]) -> None:
        specifiedJuiceBox: JbDetails | None = None
        otherJuiceBox: JbDetails | None = None

        for juiceBox in juiceBoxes:
            if juiceBox.name.startswith(self.specifiedJuiceBoxName):
                specifiedJuiceBox = juiceBox
            else:
                otherJuiceBox = juiceBox
        # end for

        if not specifiedJuiceBox or not otherJuiceBox:
            raise JuiceBoxException(f"Unable to locate both JuiceBoxes,"
                                    f" found {[jb.name for jb in juiceBoxes]}")

        self.jbIntrfc.setNewMaximums(specifiedJuiceBox, self.specifiedMaxAmps, otherJuiceBox)
    # end specifyMaxCurrent(list[JbDetails])

    def main(self) -> None:
        logging.debug(f"Starting {' '.join(sys.argv)}")

        with self.jbIntrfc.session, self.jbIntrfc:
            self.jbIntrfc.logIn()
            juiceBoxes = self.jbIntrfc.getStateOfJuiceBoxes()
            juiceBoxes[:] = [jb for jb in juiceBoxes if not jb.isOffline]

            for juiceBox in juiceBoxes:
                logging.info(juiceBox.statusStr())
            # end for

            if self.autoMax:
                self.automaticallySetMax(juiceBoxes)
            elif self.specifiedMaxAmps is not None:
                self.specifyMaxCurrent(juiceBoxes)
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
