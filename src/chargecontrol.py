
import asyncio
import json
import logging
import sys
from abc import ABC, abstractmethod
from argparse import ArgumentParser, Namespace
from contextlib import AsyncExitStack

from juicebox import JbDetails, JbInterface, LgDetails
from tessie import CarDetails, TessieInterface
from util import Configure, ExceptionGroupHandler, PersistentData


class ChargeControl(object):
    """Controls vehicles charging activity"""
    PRIOR_CHARGE_LIMIT = "priorChargeLimit"

    def __init__(self, args: Namespace):
        """Initialize this instance and allocate resources
        :param args: A Namespace instance with parsed command line arguments
        """
        self.autoMax: bool = args.autoMax
        self.disable: bool = args.disable
        self.enableLimit: int | None = args.enableLimit
        self.justEqualAmps: bool = args.justEqualAmps
        self.setLimit: int | None = args.setLimit
        self.group: bool = args.group
        self.maxAmps: int | None = int(args.maxAmps[1]) if args.maxAmps else None
        self.maxAmpsName: str | None = args.maxAmps[0] if args.maxAmps else None
        self.persistentData = PersistentData()

        with open(Configure.findParmPath().joinpath("carjuiceboxmapping.json"),
                  "r", encoding="utf-8") as mappingFile:
            carJuiceBoxMapping: dict = json.load(mappingFile)

        self.minPluggedCurrent: int = carJuiceBoxMapping["minPluggedCurrent"]
        self.totalCurrent: int = carJuiceBoxMapping["totalCurrent"]
    # end __init__(Namespace)

    @staticmethod
    def parseArgs() -> Namespace:
        """Parse command line arguments
        :return: A Namespace instance with parsed command line arguments
        """
        ap = ArgumentParser(description="Module to control charging all authorized cars"
                                        " and to set maximum request currents",
                            epilog="Just displays status when no option is specified")
        group = ap.add_mutually_exclusive_group()
        group.add_argument("-a", "--autoMax", action="store_true",
                           help="set maximum currents based on cars' charging needs")
        group.add_argument("-d", "--disable", action="store_true",
                           help="disable charging")
        group.add_argument("-e", "--enableLimit", type=int, metavar="percent",
                           help="enable charging with each car at limit 'percent' if 50%%,"
                                " setting currents based on cars' needs")
        group.add_argument("-j", "--justEqualAmps", action="store_true",
                           help="just share current equally")
        group.add_argument("-s", "--setLimit", type=int, metavar="percent",
                           help="set each car to charge limit 'percent' if 50%%,"
                                " setting currents based on cars' needs")
        group.add_argument("-g", "--group", action="store_true",
                           help="add all JuiceBoxes to a load group")
        group.add_argument("-m", "--maxAmps", nargs=2, metavar=("name", "amps"),
                           help="name prefix of car and maximum current to set (amps)"
                                " (other gets remaining current)")

        return ap.parse_args()
    # end parseArgs()

    def getSpecifiedProcessor(self) -> "ParallelProc":
        """Get the processor indicated on the command line
        :return: Processor corresponding to command line arguments
        """
        processor: ParallelProc

        match True:
            case _ if self.group:
                processor = LoadGrouper(self)
            case _ if self.maxAmps is not None:
                processor = MaxCurrentControl(self)
            case _ if self.justEqualAmps:
                processor = EqualCurrentControl(self)
            case _ if self.autoMax:
                processor = AutoCurrentControl(self)
            case _ if self.setLimit is not None:
                processor = ChargeLimitControl(self)
            case _ if self.enableLimit is not None:
                processor = CarChargingEnabler(self)
            case _ if self.disable:
                processor = CarChargingDisabler(self)
            case _:
                processor = StatusPresenter(self)
        # end match

        return processor
    # end getSpecifiedProcessor()

    async def main(self) -> None:
        logging.debug(f"Starting {' '.join(sys.argv)}")
        processor = self.getSpecifiedProcessor()

        async with AsyncExitStack() as cStack:
            # Register persistent data to save when cStack closes
            cStack.callback(self.persistentData.save)

            async with asyncio.TaskGroup() as tg:
                if isinstance(processor, TessieProc):
                    # Create TessieInterface registered so it cleans up when cStack closes
                    tsIntrfc = await cStack.enter_async_context(
                        TessieInterface(self.totalCurrent))
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
    loadGroup: LgDetails

    async def addJb(self, jbIntrfc: JbInterface) -> None:
        """Store an interface to, and a list of, JuiceBoxes
        :param jbIntrfc: Interface to JuiceBoxes
        """
        self.jbIntrfc = jbIntrfc
        await jbIntrfc.logIn()

        async with asyncio.TaskGroup() as tg:
            juiceBoxesTask = tg.create_task(jbIntrfc.getStateOfJuiceBoxes())
            loadGroupTask = tg.create_task(jbIntrfc.getLoadGroup())
        # end async with (tasks are awaited)
        self.juiceBoxes = juiceBoxesTask.result()
        self.loadGroup = loadGroupTask.result()
    # end addJb(JbInterface)

# end class JuiceBoxProc


class LoadGrouper(JuiceBoxProc):
    """Processor to add all JuiceBoxes to a load group"""

    async def process(self) -> None:
        await self.jbIntrfc.addToLoadGroup(self.loadGroup, self.juiceBoxes)
    # end process()

# end class LoadGrouper


class MaxCurrentControl(TessieProc):
    """Processor to set a specified car to a specified maximum request current (amps)
       (the other car gets the remaining current)"""

    async def process(self) -> None:
        await self.specifyMaxCurrent(self.chargeCtl.maxAmpsName, self.chargeCtl.maxAmps)
    # end process()

    async def specifyMaxCurrent(self, specifiedName: str, specifiedMaxAmps: int) -> None:
        """Set the specified vehicle's maximum request current to a given value
           (the other vehicle gets the remaining current)
        :param specifiedName: Prefix of the vehicle name being specified
        :param specifiedMaxAmps: The maximum current (amps) to set for the specified vehicle
        """
        specifiedCar: CarDetails | None = None
        otherCar: CarDetails | None = None

        for dtls in self.vehicles:
            if dtls.displayName.startswith(specifiedName):
                specifiedCar = dtls
            else:
                otherCar = dtls
        # end for

        if not specifiedCar:
            raise Exception(f"Unable to locate car starting with {specifiedName},"
                            f" found {[car.displayName for car in self.vehicles]}")

        if not otherCar:
            raise Exception(f"Unable to locate both cars,"
                            f" found {[car.displayName for car in self.vehicles]}")

        await self.tsIntrfc.setMaximums(specifiedCar, specifiedMaxAmps, otherCar)
    # end specifyMaxCurrent(str, int)

# end class MaxCurrentControl


class EqualCurrentControl(TessieProc, JuiceBoxProc):
    """Processor to just share current equally"""

    async def process(self) -> None:
        await self.shareCurrentEqually()
    # end process()

    async def shareCurrentEqually(self, waitForCompletion=False) -> None:
        """Share current equally among all vehicles
        :param waitForCompletion: Flag indicating to wait for final request current to be set
        """
        halfCurrent = self.chargeCtl.totalCurrent // 2

        if len(self.juiceBoxes) >= 2:
            await self.jbIntrfc.setNewMaximums(
                self.juiceBoxes[0], halfCurrent, self.juiceBoxes[1])
        else:
            logging.error(f"Unable to locate both JuiceBoxes to share current equally,"
                          f" found {[jb.name for jb in self.juiceBoxes]}")

            if len(self.vehicles) >= 2:
                await self.tsIntrfc.setMaximums(self.vehicles[0], halfCurrent,
                                                self.vehicles[1], waitForCompletion)
            else:
                logging.error(f"Unable to locate both cars to share current equally,"
                              f" found {[car.displayName for car in self.vehicles]}")
    # end shareCurrentEqually()

# end class EqualCurrentControl


class AutoCurrentControl(TessieProc):
    """Processor to automatically set maximum currents based on cars' charging needs"""

    async def process(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                tg.create_task(self.tsIntrfc.addBatteryHealth(dtls))
            # end for
        # end async with (tasks are awaited)

        await self.automaticallySetMaxCurrent()
    # end process()

    async def automaticallySetMaxCurrent(self, waitForCompletion=False) -> None:
        """Automatically set cars' maximum currents based on each cars' charging needs
           - depends on having battery health details
        :param waitForCompletion: Flag indicating to wait for final request current to be set
        """
        totalEnergyNeeded = 0.0

        for carDetails in self.vehicles:
            energyNeeded = carDetails.energyNeededC()
            summary = carDetails.chargingStatusSummary()

            if energyNeeded:
                summary += f" ({energyNeeded:.1f} kWh < limit)"

            if summary.updatedSinceLastSummary or energyNeeded:
                logging.info(summary)
            totalEnergyNeeded += energyNeeded
        # end for

        if len(self.vehicles) < 2:
            raise Exception(f"Unable to locate both cars,"
                            f" found {[car.displayName for car in self.vehicles]}")

        if totalEnergyNeeded:
            self.vehicles.sort(key=lambda car: car.energyNeededC(), reverse=True)
            fairShare0 = self.chargeCtl.totalCurrent * (
                    self.vehicles[0].energyNeededC() / totalEnergyNeeded)

            await self.tsIntrfc.setMaximums(self.vehicles[0], int(fairShare0 + 0.5),
                                            self.vehicles[1], waitForCompletion)
    # end automaticallySetMaxCurrent()

# end class AutoCurrentControl


class ChargeLimitControl(AutoCurrentControl):
    """Processor to set each car to a specified charge limit if 50%,
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

    async def setChargeLimit(self, dtls: CarDetails, percent: int,
                             waitForCompletion=True) -> None:
        """If the specified vehicle's charge limit is minimum,
           ensure the vehicle is awake and set a specified charge limit percent
        :param dtls: Details of the vehicle to set
        :param percent: Charging limit percent to use if none persisted
        :param waitForCompletion: Flag indicating to wait for limit to be set
        """
        if dtls.chargeLimitIsMin():
            # this vehicle is set to charge limit minimum
            persistedLimit: int | None = self.chargeCtl.persistentData.getVal(
                ChargeControl.PRIOR_CHARGE_LIMIT, dtls.vin)

            if persistedLimit is not None:
                # use persisted limit instead of parameter value
                percent = persistedLimit
            percent = dtls.limitToCapabilities(percent)

            if percent != dtls.chargeLimit:
                if not dtls.awake():
                    # try to wake up this car
                    await self.tsIntrfc.getWakeTask(dtls)

                await self.tsIntrfc.setChargeLimit(dtls, percent, waitForCompletion)
            else:
                logging.info(f"No change made to {dtls.displayName} charge limit")
    # end setChargeLimit(CarDetails, int, bool)

# end class ChargeLimitControl


class CarChargingEnabler(ChargeLimitControl):
    """Processor to enable charging with each car set to a specified charge limit if 50%,
       setting maximum currents based on cars' charging needs"""

    async def process(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                logging.info(dtls.chargingStatusSummary())
                tg.create_task(self.setChargeLimit(dtls, self.chargeCtl.enableLimit))
                tg.create_task(self.tsIntrfc.addBatteryHealth(dtls))
            # end for
        # end async with (tasks are awaited)

        await self.automaticallySetMaxCurrent(waitForCompletion=True)

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
                await self.tsIntrfc.getWakeTask(dtls)

            if dtls.dataAge() > 10:
                # make sure we have the current battery level and charge limit
                await self.tsIntrfc.getCurrentState(dtls)

        if dtls.pluggedInAtHome() and dtls.chargingState != "Charging" and dtls.chargeNeeded():
            # this vehicle is plugged in at home, not charging and could use a charge
            retries = 6

            while (dtls.chargingState == "Complete" or dtls.chargingState == "NoPower") \
                    and dtls.chargeNeeded() and retries:
                # wait for charging state to change from Complete or NoPower
                await asyncio.sleep(9)
                await self.tsIntrfc.getCurrentState(dtls, attempts=1)
                retries -= 1
            # end while

            await self.tsIntrfc.startCharging(dtls)
    # end startChargingWhenReady(CarDetails)

# end class CarChargingEnabler


class CarChargingDisabler(TessieProc):
    """Processor to disable charging, sharing current equally"""

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
                await self.tsIntrfc.getWakeTask(dtls)

            await self.tsIntrfc.stopCharging(dtls, waitForCompletion=True)

            if not dtls.chargeLimitIsMin():
                # this vehicle is not set to minimum limit already
                self.chargeCtl.persistentData.setVal(
                    ChargeControl.PRIOR_CHARGE_LIMIT, dtls.vin, dtls.chargeLimit)
                await self.tsIntrfc.setChargeLimit(dtls, dtls.limitMinPercent)
    # end disableCarCharging(CarDetails)

# end class CarChargingDisabler


class StatusPresenter(TessieProc, JuiceBoxProc):
    """Processor to just display status"""

    async def process(self) -> None:
        for dtls in self.vehicles:
            logging.info(dtls.chargingStatusSummary())

        for juiceBox in self.juiceBoxes:
            logging.info(juiceBox.statusStr())
    # end process()

# end class StatusPresenter


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
