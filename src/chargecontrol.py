
import asyncio
import json
import logging
import sys
from abc import ABC, abstractmethod
from argparse import ArgumentParser, Namespace
from contextlib import aclosing

from juicebox import JbDetails, JbInterface
from tessie import CarDetails, TessieInterface
from util import Configure, ExceptionGroupHandler


class ChargeControl(object):
    """Controls vehicles charging activity"""

    def __init__(self, args: Namespace):
        self.autoMax: bool = args.autoMax
        self.disable: bool = args.disable
        self.enableLimit: int | None = args.enableLimit
        self.justEqualAmps: bool = args.justEqualAmps
        self.setLimit: int | None = args.setLimit

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
        ap = ArgumentParser(description="Module to control charging all authorized cars",
                            epilog="Just displays status when no option is specified")
        group = ap.add_mutually_exclusive_group()
        group.add_argument("-a", "--autoMax", action="store_true",
                           help="automatically set maximums based on cars' charging needs")
        group.add_argument("-d", "--disable", action="store_true",
                           help="disable charging")
        group.add_argument("-e", "--enableLimit", type=int, metavar="percent",
                           help="enable charging with limit if 50%%")
        group.add_argument("-j", "--justEqualAmps", action="store_true",
                           help="just share current equally")
        group.add_argument("-s", "--setLimit", type=int, metavar="percent",
                           help="set charge limits if 50%%")

        return ap.parse_args()
    # end parseArgs()

    async def main(self) -> None:
        logging.debug(f"Starting {' '.join(sys.argv)}")
        processor: ParallelProc

        match True:
            case _ if self.setLimit:
                processor = SetChargeLimit(self)
            case _ if self.justEqualAmps:
                processor = ShareCurrentEqually(self)
            case _ if self.autoMax:
                processor = AutomaticallySetMax(self)
            case _ if self.enableLimit:
                processor = EnableCarCharging(self)
            case _ if self.disable:
                processor = DisableCarCharging(self)
            case _:
                processor = DisplayStatus(self)
        # end match

        async with aclosing(TessieInterface()) as tsIntrfc, aclosing(
                JbInterface(self.minPluggedCurrent, self.totalCurrent)) as jbIntrfc:
            tsIntrfc: TessieInterface
            jbIntrfc: JbInterface

            if isinstance(processor, TessieProc):
                await processor.addTs(tsIntrfc)

            if isinstance(processor, JuiceBoxProc):
                await processor.addJb(jbIntrfc)

            await processor.process()
        # end async with (interfaces are closed)
    # end main()

# end class ChargeControl


class ParallelProc(ABC):

    def __init__(self, chargeCtl: ChargeControl):
        """Sole constructor - store a charge control reference
        :param chargeCtl: Charge control reference
        """
        self.chargeCtl = chargeCtl
    # end __init__(ChargeControl)

    @abstractmethod
    async def process(self) -> None:
        """Abstract method that accomplishes the goal of this class"""
        pass
    # end process()

# end class ParallelProc


class TessieProc(ParallelProc, ABC):
    # fields set by addTs
    tsIntrfc: TessieInterface
    vehicles: list[CarDetails]

    async def addTs(self, tsIntrfc: TessieInterface) -> None:
        """Store an interface to Tessie and a list of vehicles
        :param tsIntrfc: Interface to Tessie
        """
        self.tsIntrfc = tsIntrfc
        self.vehicles = await tsIntrfc.getStateOfActiveVehicles()
    # end addTs(TessieInterface)

# end class TessieProc


class JuiceBoxProc(ParallelProc, ABC):
    # fields set by addJb
    jbIntrfc: JbInterface
    juiceBoxes: list[JbDetails]

    async def addJb(self, jbIntrfc: JbInterface) -> None:
        """Store an interface to, and a list of, JuiceBoxes
        :param jbIntrfc: Interface to JuiceBoxes
        """
        self.jbIntrfc = jbIntrfc
        await jbIntrfc.logIn()
        self.juiceBoxes = await jbIntrfc.getStateOfJuiceBoxes()
    # end addJb(JbInterface)

# end class JuiceBoxProc


class SetChargeLimit(TessieProc):

    async def process(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                logging.info(dtls.currentChargingStatus())
                tg.create_task(self.setChargeLimit(dtls, self.chargeCtl.setLimit,
                                                   waitForCompletion=False))
            # end for
        # end async with (tasks are awaited)
    # end process()

    async def setChargeLimit(self, dtls: CarDetails, percent: int, *,
                             waitForCompletion=True) -> None:
        """If the specified vehicle's charge limit is minimum,
           ensure the vehicle is awake and set a specified charge limit percent
        :param dtls: Details of the vehicle to set
        :param percent: Charging limit percent
        :param waitForCompletion: Flag indicating to wait for limit to be set
        """
        if dtls.chargeLimitIsMin():
            # this vehicle is set to charge limit minimum
            percent = dtls.limitToCapabilities(percent)

            if percent != dtls.chargeLimit:
                if not dtls.awake():
                    # try to wake up this car
                    await self.tsIntrfc.wake(dtls)

                await self.tsIntrfc.setChargeLimit(dtls, percent,
                                                   waitForCompletion=waitForCompletion)
            else:
                logging.info(f"No change made to {dtls.displayName} charge limit")
    # end setChargeLimit(CarDetails, int, bool)

# end class SetChargeLimit


class ShareCurrentEqually(JuiceBoxProc):

    async def process(self) -> None:
        await self.shareCurrentEqually()
    # end process()

    async def shareCurrentEqually(self) -> None:
        """Share current equally between all JuiceBoxes"""
        await self.jbIntrfc.setNewMaximums(
            self.juiceBoxes[0], self.chargeCtl.totalCurrent // 2, self.juiceBoxes[1])
    # end shareCurrentEqually()

# end class ShareCurrentEqually


class AutomaticallySetMax(TessieProc, ShareCurrentEqually):

    async def process(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                tg.create_task(self.tsIntrfc.addBatteryHealth(dtls))
            # end for
        # end async with (tasks are awaited)

        await self.automaticallySetMax()
    # end process()

    async def automaticallySetMax(self) -> None:
        """Automatically set JuiceBox maximum currents based on each cars' charging needs
           - depends on having battery health details"""
        totalEnergyNeeded = 0.0

        for carDetails in self.vehicles:
            energyNeeded = carDetails.energyNeededC()
            msg = carDetails.currentChargingStatus()

            if energyNeeded:
                msg += f" ({energyNeeded:.1f} kWh < limit)"
            logging.info(msg)
            totalEnergyNeeded += energyNeeded
        # end for

        if len(self.vehicles) < 2:
            raise Exception(f"Unable to locate both cars,"
                            f" found {[car.displayName for car in self.vehicles]}")

        if totalEnergyNeeded:
            juiceBoxMap = {jb.name: jb for jb in self.juiceBoxes}
            self.vehicles.sort(key=lambda car: car.energyNeededC(), reverse=True)
            jbs = [self.getJuiceBoxForCar(car, juiceBoxMap) for car in self.vehicles]
            fairShare0 = self.chargeCtl.totalCurrent * (
                    self.vehicles[0].energyNeededC() / totalEnergyNeeded)

            await self.jbIntrfc.setNewMaximums(jbs[0], int(fairShare0 + 0.5), jbs[1])
        else:
            # Share current equally when no car needs energy
            await self.shareCurrentEqually()
    # end automaticallySetMax()

    def getJuiceBoxForCar(self, vehicle: CarDetails, juiceBoxMap: dict) -> JbDetails:
        """Retrieve JuiceBox details corresponding to a given car
        :param vehicle: Details of the vehicle in question
        :param juiceBoxMap: Mapping from JuiceBox names to JuiceBox details
        :return: Details of the corresponding JuiceBox
        """
        juiceBoxName: str = self.chargeCtl.jbAttachMap[vehicle.displayName]
        juiceBox: JbDetails = juiceBoxMap[juiceBoxName]

        if vehicle.pluggedInAtHome() and vehicle.chargeAmps != juiceBox.maxCurrent:
            logging.warning(f"Suspicious car-JuiceBox mapping;"
                            f" {vehicle.displayName} shows {vehicle.chargeAmps} amps offered"
                            f" but {juiceBox.name} has {juiceBox.maxCurrent} amps max")

        return juiceBox
    # end getJuiceBoxForCar(CarDetails, dict)

# end class AutomaticallySetMax


class EnableCarCharging(SetChargeLimit, AutomaticallySetMax):

    async def process(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                tg.create_task(self.setChargeLimit(dtls, self.chargeCtl.enableLimit))
                tg.create_task(self.tsIntrfc.addBatteryHealth(dtls))
            # end for
        # end async with (tasks are awaited)

        await self.automaticallySetMax()

        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                tg.create_task(self.startChargingWhenReady(dtls))
            # end for
        # end async with (tasks are awaited)
    # end process()

    async def startChargingWhenReady(self, dtls: CarDetails) -> None:
        """Start charging if plugged in at home, not charging and could use a charge
        :param dtls: Details of the vehicle to start charging
        """
        if dtls.pluggedInAtHome():
            if not dtls.awake():
                await self.tsIntrfc.wake(dtls)

            # make sure we have the current battery level and charge limit
            await self.tsIntrfc.getCurrentState(dtls)

        if dtls.pluggedInAtHome() and dtls.chargingState != "Charging" and dtls.chargeNeeded():
            # this vehicle is plugged in at home, not charging and could use a charge
            retries = 6

            while dtls.chargingState == "Complete" and dtls.chargeNeeded() and retries:
                # wait for charging state to change from Complete
                await asyncio.sleep(3.2)
                await self.tsIntrfc.getCurrentState(dtls, attempts=1)
                retries -= 1
            # end while

            await self.tsIntrfc.startCharging(dtls)
    # end startChargingWhenReady(CarDetails)

# end class EnableCarCharging


class DisableCarCharging(TessieProc):

    async def process(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                logging.info(dtls.currentChargingStatus())
                tg.create_task(self.disableCarCharging(dtls))
            # end for
        # end async with (tasks are awaited)
    # end process()

    async def disableCarCharging(self, dtls: CarDetails) -> None:
        """Stop charging and lower the charge limit to minimum
           if plugged in at home and not minimum already
        :param dtls: Details of the vehicle to stop charging
        """
        if dtls.pluggedInAtHome():
            # this vehicle is plugged in at home

            if not dtls.awake():
                await self.tsIntrfc.wake(dtls)
            await self.tsIntrfc.stopCharging(dtls)

            if not dtls.chargeLimitIsMin():
                # this vehicle is not set to minimum limit already
                await self.tsIntrfc.setChargeLimit(dtls, dtls.limitMinPercent,
                                                   waitForCompletion=False)
    # end disableCarCharging(CarDetails)

# end class DisableCarCharging


class DisplayStatus(TessieProc):

    async def process(self) -> None:
        for dtls in self.vehicles:
            logging.info(dtls.currentChargingStatus())
    # end process()

# end class DisplayStatus


if __name__ == "__main__":
    clArgs = ChargeControl.parseArgs()
    Configure.logToFile()
    try:
        chrgCtl = ChargeControl(clArgs)
        asyncio.run(chrgCtl.main())
    except Exception as xcption:
        for xcpt in ExceptionGroupHandler.iterGroup(xcption):
            logging.error(xcpt)
            logging.debug(f"{xcpt.__class__.__name__} suppressed:", exc_info=xcpt)
