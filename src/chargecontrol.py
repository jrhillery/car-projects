
import asyncio
import json
import logging
import sys
from abc import ABC, abstractmethod
from argparse import ArgumentParser, Namespace
from contextlib import AsyncExitStack

from juicebox import JbDetails, JbInterface
from tessie import CarDetails, TessieInterface
from util import Configure, ExceptionGroupHandler


class ChargeControl(object):
    """Controls vehicles charging activity"""

    def __init__(self, args: Namespace):
        """Initialize this instance and allocate resources
        :param args: A Namespace instance with parsed command line arguments
        """
        self.autoMax: bool = args.autoMax
        self.disable: bool = args.disable
        self.enableLimit: int | None = args.enableLimit
        self.justEqualAmps: bool = args.justEqualAmps
        self.setLimit: int | None = args.setLimit
        self.maxAmps: int | None = int(args.maxAmps[1]) if args.maxAmps else None
        self.maxAmpsName: str | None = args.maxAmps[0] if args.maxAmps else None

        with open(Configure.findParmPath().joinpath("carjuiceboxmapping.json"),
                  "r", encoding="utf-8") as mappingFile:
            carJuiceBoxMapping: dict = json.load(mappingFile)

        self.jbAttachMap: dict = carJuiceBoxMapping["attachedJuiceBoxes"]
        self.minPluggedCurrent: int = carJuiceBoxMapping["minPluggedCurrent"]
        self.totalCurrent: int = carJuiceBoxMapping["totalCurrent"]
    # end __init__(Namespace)

    @staticmethod
    def parseArgs() -> Namespace:
        """Parse command line arguments
        :return: A Namespace instance with parsed command line arguments
        """
        ap = ArgumentParser(description="Module to control charging all authorized cars"
                                        " and to set maximum JuiceBox charge currents",
                            epilog="Just displays status when no option is specified")
        group = ap.add_mutually_exclusive_group()
        group.add_argument("-a", "--autoMax", action="store_true",
                           help="set maximum currents based on cars' charging needs")
        group.add_argument("-d", "--disable", action="store_true",
                           help="disable charging")
        group.add_argument("-e", "--enableLimit", type=int, metavar="percent",
                           help="enable charging with limit if 50%%,"
                                " setting maximum currents based on cars' charging needs")
        group.add_argument("-j", "--justEqualAmps", action="store_true",
                           help="just share current equally")
        group.add_argument("-s", "--setLimit", type=int, metavar="percent",
                           help="set charge limits if 50%%,"
                                " setting maximum currents based on cars' charging needs")
        group.add_argument("-m", "--maxAmps", nargs=2, metavar=("name", "amps"),
                           help="name prefix of JuiceBox and maximum current to set (Amps)"
                                " (other gets remaining current)")

        return ap.parse_args()
    # end parseArgs()

    def getSpecifiedProcessor(self) -> "ParallelProc":
        """Get the processor indicated on the command line
        :return: Processor corresponding to command line arguments
        """
        processor: ParallelProc

        match True:
            case _ if self.maxAmps is not None:
                processor = SpecifyMaxCurrent(self)
            case _ if self.justEqualAmps:
                processor = EqualCurrent(self)
            case _ if self.autoMax:
                processor = AutoMaxCurrent(self)
            case _ if self.setLimit is not None:
                processor = SetChargeLimit(self)
            case _ if self.enableLimit is not None:
                processor = EnableCarCharging(self)
            case _ if self.disable:
                processor = DisableCarCharging(self)
            case _:
                processor = DisplayStatus(self)
        # end match

        return processor
    # end getSpecifiedProcessor()

    async def main(self) -> None:
        logging.debug(f"Starting {' '.join(sys.argv)}")
        processor = self.getSpecifiedProcessor()

        async with AsyncExitStack() as cStack:
            async with asyncio.TaskGroup() as tg:
                if isinstance(processor, TessieProc):
                    # Create TessieInterface registered so it cleans up when cStack closes
                    tsIntrfc = await cStack.enter_async_context(TessieInterface())
                    tg.create_task(processor.addTs(tsIntrfc))

                if isinstance(processor, JuiceBoxProc):
                    # Create JbInterface registered so it cleans up when cStack closes
                    jbIntrfc = await cStack.enter_async_context(
                        JbInterface(self.minPluggedCurrent, self.totalCurrent))
                    tg.create_task(processor.addJb(jbIntrfc))
            # end async with (tasks are awaited)

            await processor.process()
        # end async with (interfaces are closed)
    # end main()

# end class ChargeControl


class ParallelProc(ABC):
    """Abstract base class for all processors"""

    def __init__(self, chargeCtl: ChargeControl):
        """Sole constructor - store a charge control reference
        :param chargeCtl: Charge control reference
        """
        self.chargeCtl = chargeCtl
    # end __init__(ChargeControl)

    @abstractmethod
    async def process(self) -> None:
        """Method that will accomplish the goal of this processor"""
        pass
    # end process()

# end class ParallelProc


class TessieProc(ParallelProc, ABC):
    """Abstract base class for processors that use a Tessie interface"""
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
    """Abstract base class for processors that use a JuiceBox interface"""
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


class SpecifyMaxCurrent(JuiceBoxProc):
    """Processor to set a specified JuiceBox to a specified maximum current (Amps)
       (the other JuiceBox gets the remaining current)"""

    async def process(self) -> None:
        await self.specifyMaxCurrent(self.chargeCtl.maxAmpsName, self.chargeCtl.maxAmps)
    # end process()

    async def specifyMaxCurrent(self, specifiedName: str, specifiedMaxAmps: int) -> None:
        """Set the specified JuiceBox maximum current to a given value
           (the other JuiceBox gets the remaining current)
        :param specifiedName: Prefix of the JuiceBox name being specified
        :param specifiedMaxAmps: The maximum current (Amps) to set for the specified JuiceBox
        """
        specifiedJuiceBox: JbDetails | None = None
        otherJuiceBox: JbDetails | None = None

        for juiceBox in self.juiceBoxes:
            if juiceBox.name.startswith(specifiedName):
                specifiedJuiceBox = juiceBox
            else:
                otherJuiceBox = juiceBox
        # end for

        if not specifiedJuiceBox:
            raise Exception(f"Unable to locate JuiceBox starting with {specifiedName},"
                            f" found {[jb.name for jb in self.juiceBoxes]}")

        if not otherJuiceBox:
            raise Exception(f"Unable to locate both JuiceBoxes,"
                            f" found {[jb.name for jb in self.juiceBoxes]}")

        await self.jbIntrfc.setNewMaximums(specifiedJuiceBox, specifiedMaxAmps, otherJuiceBox)
    # end specifyMaxCurrent(str, int)

# end class SpecifyMaxCurrent


class EqualCurrent(JuiceBoxProc):
    """Processor to just share current equally"""

    async def process(self) -> None:
        await self.shareCurrentEqually()
    # end process()

    async def shareCurrentEqually(self) -> None:
        """Share current equally between all JuiceBoxes"""
        await self.jbIntrfc.setNewMaximums(
            self.juiceBoxes[0], self.chargeCtl.totalCurrent // 2, self.juiceBoxes[1])
    # end shareCurrentEqually()

# end class EqualCurrent


class AutoMaxCurrent(TessieProc, EqualCurrent):
    """Processor to automatically set maximum currents based on cars' charging needs"""

    async def process(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                tg.create_task(self.tsIntrfc.addBatteryHealth(dtls))
            # end for
        # end async with (tasks are awaited)

        await self.automaticallySetMaxCurrent()
    # end process()

    async def automaticallySetMaxCurrent(self) -> None:
        """Automatically set JuiceBox maximum currents based on each cars' charging needs
           - depends on having battery health details"""
        totalEnergyNeeded = 0.0

        for carDetails in self.vehicles:
            energyNeeded = carDetails.energyNeededC()
            statusChanged = carDetails.updatedSinceSummary
            summary = carDetails.chargingStatusSummary()

            if energyNeeded:
                summary += f" ({energyNeeded:.1f} kWh < limit)"

            if statusChanged or energyNeeded:
                logging.info(summary)
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
    # end automaticallySetMaxCurrent()

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

# end class AutoMaxCurrent


class SetChargeLimit(AutoMaxCurrent):
    """Processor to set charge limits if 50%,
       setting maximum currents based on cars' charging needs"""

    async def process(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                logging.info(dtls.chargingStatusSummary())
                tg.create_task(self.setChargeLimit(dtls, self.chargeCtl.setLimit,
                                                   waitForCompletion=False))
                tg.create_task(self.tsIntrfc.addBatteryHealth(dtls))
            # end for
        # end async with (tasks are awaited)

        await self.automaticallySetMaxCurrent()
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


class EnableCarCharging(SetChargeLimit):
    """Processor to enable charging with limit if 50%,
       setting maximum currents based on cars' charging needs"""

    async def process(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                logging.info(dtls.chargingStatusSummary())
                tg.create_task(self.setChargeLimit(dtls, self.chargeCtl.enableLimit))
                tg.create_task(self.tsIntrfc.addBatteryHealth(dtls))
            # end for
        # end async with (tasks are awaited)

        await self.automaticallySetMaxCurrent()

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
    """Processor to disable charging"""

    async def process(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                logging.info(dtls.chargingStatusSummary())
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


class DisplayStatus(TessieProc, JuiceBoxProc):
    """Processor to just display status"""

    async def process(self) -> None:
        for dtls in self.vehicles:
            logging.info(dtls.chargingStatusSummary())

        for juiceBox in self.juiceBoxes:
            logging.info(juiceBox.statusStr())
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
