
import asyncio
import json
import logging
import sys
from abc import ABC, abstractmethod
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
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
        self.autoReq: bool = args.autoReq
        self.disable: bool = args.disable
        self.enable: bool = args.enable
        self.restoreLimit: bool = args.restoreLimit
        self.specifyReq: int | None = int(args.specifyReq[1]) if args.specifyReq else None
        self.specifyReqName: str | None = args.specifyReq[0] if args.specifyReq else None
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
                                        " and to set request currents",
                            epilog="Just displays status when no option is specified")
        group = ap.add_mutually_exclusive_group()
        group.add_argument("-a", "--autoReq", action="store_true",
                           help="set request currents based on cars' charging needs")
        group.add_argument("-d", "--disable", action="store_true",
                           help="disable charging")
        group.add_argument("-e", "--enable", action="store_true",
                           help="enable charging restoring each car's limit if 50%%,"
                                " setting request currents based on need")
        group.add_argument("-r", "--restoreLimit", action="store_true",
                           help="restore each car's charge limit if 50%%,"
                                " setting request currents based on need")
        group.add_argument("-s", "--specifyReq", nargs=2, metavar=("name", "amps"),
                           help="name prefix of car and request current to set (amps)"
                                " (other gets remaining current)")

        return ap.parse_args()
    # end parseArgs()

    def getSpecifiedProcessor(self) -> "TessieProc":
        """Get the processor indicated on the command line
        :return: Processor corresponding to command line arguments
        """
        processor: TessieProc

        match True:
            case _ if self.specifyReq is not None:
                processor = ReqCurrentControl(self)
            case _ if self.autoReq:
                processor = AutoCurrentControl(self)
            case _ if self.restoreLimit:
                processor = ChargeLimitRestore(self)
            case _ if self.enable:
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
                # Create TessieInterface registered so it cleans up when cStack closes
                tsIntrfc = await cStack.enter_async_context(TessieInterface(self.totalCurrent))
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
    vehicles: Sequence[CarDetails]

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
    juiceBoxes: Sequence[JbDetails]
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


class ReqCurrentControl(TessieProc):
    """Processor to set a specified car to a specified request current (amps)
       (the other car gets the remaining current)"""

    async def process(self) -> None:
        await self.specifyReqCurrent(self.chargeCtl.specifyReqName, self.chargeCtl.specifyReq)
    # end process()

    async def specifyReqCurrent(self, specifiedName: str, specifiedReqAmps: int) -> None:
        """Set the specified vehicle's request current to a given value
           (the other vehicle gets the remaining current)
        :param specifiedName: Prefix of the vehicle name being specified
        :param specifiedReqAmps: The request current (amps) to set for the specified vehicle
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

        await self.tsIntrfc.setReqCurrents((specifiedCar, otherCar), (specifiedReqAmps, ))
    # end specifyReqCurrent(str, int)

# end class ReqCurrentControl


class AutoCurrentControl(TessieProc):
    """Processor to automatically set request currents based on cars' charging needs"""

    async def process(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                tg.create_task(self.tsIntrfc.addBatteryHealth(dtls))
            # end for
        # end async with (tasks are awaited)

        await self.automaticallySetReqCurrent()
    # end process()

    def getPriorLimit(self, dtls: CarDetails) -> int:
        """Get the persisted charge limit for the specified car
           - using an average value if no limit is persisted
        :param dtls: Details of the specified vehicle
        :return: Persisted charge limit
        """
        percent: int | None = self.chargeCtl.persistentData.getVal(
            ChargeControl.PRIOR_CHARGE_LIMIT, dtls.vin)

        if percent is None:
            # use an average value if no limit is persisted
            percent = (dtls.limitMinPercent + dtls.limitMaxPercent) // 2

        return dtls.limitChargeLimit(percent)
    # end getPriorLimit(CarDetails)

    async def automaticallySetReqCurrent(self, waitForCompletion=False) -> None:
        """Automatically set cars' request currents based on each cars' charging needs
           - depends on having battery health details
        :param waitForCompletion: Flag indicating to wait for final request current to be set
        """
        energiesNeeded: list[float] = []
        totalEnergyNeeded = 0.0

        for dtls in self.vehicles:
            energyNeeded = dtls.energyNeededC(None if not dtls.chargeLimitIsMin()
                                              else self.getPriorLimit(dtls))
            summary = dtls.chargingStatusSummary()

            if energyNeeded:
                summary += f" ({energyNeeded:.1f} kWh < limit)"

            if summary.updatedSinceLastSummary or energyNeeded:
                logging.info(summary)
            energiesNeeded.append(energyNeeded)
            totalEnergyNeeded += energyNeeded
        # end for

        if totalEnergyNeeded:
            reqCurrents = [self.chargeCtl.totalCurrent * (
                    energy / totalEnergyNeeded) for energy in energiesNeeded]

            await self.tsIntrfc.setReqCurrents(self.vehicles, reqCurrents, waitForCompletion)
    # end automaticallySetReqCurrent(bool)

# end class AutoCurrentControl


class ChargeLimitRestore(AutoCurrentControl):
    """Processor to restore each car to a persisted charge limit if 50%,
       setting request currents based on cars' charging needs"""

    async def process(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                logging.info(dtls.chargingStatusSummary())
                tg.create_task(self.tsIntrfc.addBatteryHealth(dtls))
            # end for
        # end async with (tasks are awaited)

        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                tg.create_task(self.restoreChargeLimit(dtls))
            # end for
            tg.create_task(self.automaticallySetReqCurrent())
        # end async with (tasks are awaited)
    # end process()

    async def restoreChargeLimit(self, dtls: CarDetails) -> None:
        """If the specified vehicle's charge limit is minimum,
           ensure the vehicle is awake and restore its charge limit to a persisted percent
        :param dtls: Details of the vehicle to restore
        """
        if dtls.chargeLimitIsMin():
            # this vehicle is set to charge limit minimum
            percent = self.getPriorLimit(dtls)

            if percent != dtls.chargeLimit:
                if not dtls.awake():
                    # try to wake up this car
                    await self.tsIntrfc.getWakeTask(dtls)

                await self.tsIntrfc.setChargeLimit(dtls, percent)
            else:
                logging.info(f"No change made to {dtls.displayName} charge limit")
    # end restoreChargeLimit(CarDetails)

# end class ChargeLimitRestore


class CarChargingEnabler(ChargeLimitRestore):
    """Processor to enable charging with each car restored to a persisted charge limit if 50%,
       setting request currents based on cars' charging needs"""

    async def process(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                logging.info(dtls.chargingStatusSummary())
                tg.create_task(self.tsIntrfc.addBatteryHealth(dtls))
            # end for
        # end async with (tasks are awaited)

        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                if dtls.pluggedInAtHome() and not dtls.awake():
                    # schedule a task to wake up this vehicle but don't wait for it yet
                    self.tsIntrfc.getWakeTask(dtls)
                tg.create_task(self.restoreChargeLimit(dtls))
            # end for
            tg.create_task(self.automaticallySetReqCurrent(waitForCompletion=True))
        # end async with (tasks are awaited)

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
