
import asyncio
import json
import logging
import sys
from abc import ABC, abstractmethod
from argparse import ArgumentParser, Namespace
from collections.abc import Sequence
from contextlib import AsyncExitStack

from wakepy import keep

from tessie import CarDetails, TessieInterface
from util import Configure, ExceptionGroupHandler, Interpret, PersistentData


class ChargeControl(object):
    """Controls vehicles charging activity"""

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

        with open(Configure.findParmPath().joinpath("circuitmapping.json"),
                  "r", encoding="utf-8") as mappingFile:
            circuitMapping: dict = json.load(mappingFile)

        self.totalCurrent: int = circuitMapping["totalCurrent"]
        self.deratePercentPerDegC: float = circuitMapping["deratePercentPerDegreeC"]
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

    def getSpecifiedProcessor(self, persistData: PersistentData) -> "TessieProc":
        """Get the processor indicated on the command line
        :return: Processor corresponding to command line arguments
        :param persistData: Persistent data reference
        """
        processor: TessieProc

        match True:
            case _ if self.specifyReq is not None:
                processor = ReqCurrentControl(self, persistData)
            case _ if self.autoReq:
                processor = AutoCurrentControl(self, persistData)
            case _ if self.restoreLimit:
                processor = ChargeLimitRestore(self, persistData)
            case _ if self.enable:
                processor = CarChargingEnabler(self, persistData)
            case _ if self.disable:
                processor = CarChargingDisabler(self, persistData)
            case _:
                processor = StatusPresenter(self, persistData)
        # end match

        return processor
    # end getSpecifiedProcessor(PersistentData)

    async def main(self) -> None:
        logging.debug(f"Starting {' '.join(sys.argv)}")

        async with AsyncExitStack() as cStack:
            # Prevent the computer from going to sleep until cStack closes
            if not cStack.enter_context(keep.running()).success:
                logging.info(f"Unable to prevent sleep using {keep.__name__}")

            # Register persistent data to save when cStack closes
            persistentData = cStack.enter_context(PersistentData())
            processor = self.getSpecifiedProcessor(persistentData)

            # Create TessieInterface registered so it cleans up when cStack closes
            tsIntrfc = await cStack.enter_async_context(TessieInterface())

            await processor.addTs(tsIntrfc)
            await processor.process()
        # end async with, callbacks are invoked in the reverse order of registration
    # end main()

# end class ChargeControl


class TessieProc(ABC):
    """Abstract base class for processors that use a Tessie interface"""
    PRIOR_CHARGE_LIMIT = "priorChargeLimit"

    # fields set by addTs
    tsIntrfc: TessieInterface
    vehicles: Sequence[CarDetails]

    def __init__(self, chargeCtl: ChargeControl, persistData: PersistentData):
        """Sole constructor - store charge control and persistent data references
        :param chargeCtl: Charge control reference
        :param persistData: Persistent data reference
        """
        self.chargeCtl = chargeCtl
        self.persistData = persistData
    # end __init__(ChargeControl, PersistentData)

    async def addTs(self, tsIntrfc: TessieInterface) -> None:
        """Store an interface to Tessie and a list of vehicles
        :param tsIntrfc: Interface to Tessie
        """
        self.tsIntrfc = tsIntrfc
        self.vehicles = await tsIntrfc.getStateOfActiveVehicles()
    # end addTs(TessieInterface)

    @abstractmethod
    async def process(self) -> None:
        """Method that will accomplish the goal of this processor"""
        pass
    # end process()

# end class TessieProc


class ReqCurrentControl(TessieProc):
    """Processor to set a specified car to a specified request current (amps)
       (the other car gets the remaining current)"""

    BREAKER_SPEC_DEG_C = 25

    async def process(self) -> None:
        # self.chargeCtl.specifyReqName is the prefix of the vehicle name being specified
        # self.chargeCtl.specifyReq is the request current (amps) to set for the vehicle
        specifiedCar: CarDetails | None = None
        otherCar: CarDetails | None = None

        for dtls in self.vehicles:
            logging.info(dtls.chargingStatusSummary())

            if dtls.displayName.startswith(self.chargeCtl.specifyReqName):
                specifiedCar = dtls
            else:
                otherCar = dtls
        # end for

        if not specifiedCar:
            raise Exception(f"Unable to locate car starting with"
                            f" {self.chargeCtl.specifyReqName},"
                            f" found {[car.displayName for car in self.vehicles]}")
        if not otherCar:
            raise Exception(f"Unable to locate both cars,"
                            f" found {[car.displayName for car in self.vehicles]}")

        await self.setReqCurrents((specifiedCar, otherCar), (self.chargeCtl.specifyReq, ))
    # end process()

    def mostRecentTemp(self) -> float:
        lastSeen = 0.0
        outsideTemp = 0.0

        for dtls in self.vehicles:
            if dtls.lastSeen > lastSeen:
                lastSeen = dtls.lastSeen
                outsideTemp = dtls.outsideTemp

        return outsideTemp
    # end mostRecentTemp()

    def derateTotalCurrent(self) -> int:
        roomTemp = self.mostRecentTemp()
        derated = self.chargeCtl.totalCurrent

        if roomTemp > self.BREAKER_SPEC_DEG_C:  # degrees C
            deratePercent = self.chargeCtl.deratePercentPerDegC * (
                    roomTemp - self.BREAKER_SPEC_DEG_C)
            derated = int(derated * (1.0 - deratePercent/100.0))

        return derated
    # end derateTotalCurrent()

    def limitRequestCurrents(self, vehicles: Sequence[CarDetails],
                             desReqCurrents: Sequence[float]) -> Sequence[int]:
        """Get corresponding request currents valid for each charge adapter
           - 'desReqCurrents' can be short - each car is given a value from remaining current
        :param vehicles: Sequence of cars to have their request currents limited
        :param desReqCurrents: Corresponding sequence of desired request currents (amps)
        :return: Corresponding sequence of valid request currents, length same as 'vehicles'
        """
        requestCurrents: list[int] = []
        remainingCurrent = self.derateTotalCurrent()

        for i, dtls in enumerate(vehicles):
            requestCurrent = dtls.limitRequestCurrent(
                int(desReqCurrents[i] + 0.5) if i < len(desReqCurrents) else remainingCurrent)
            requestCurrents.append(requestCurrent)
            remainingCurrent -= requestCurrent
        # end for

        if remainingCurrent < 0 < len(requestCurrents):
            # we oversubscribed, reduce the largest request current
            indices: list[int] = list(range(len(requestCurrents)))
            indices.sort(key=lambda j: requestCurrents[j], reverse=True)
            requestCurrents[indices[0]] += remainingCurrent

        return requestCurrents
    # end limitRequestCurrents(Sequence[CarDetails], Sequence[float])

    async def setReqCurrents(self, vehicles: Sequence[CarDetails],
                             desReqCurrents: Sequence[float], onlyWake=False,
                             waitForCompletion=False) -> None:
        """Set cars' request currents, decrease one before increasing the other
           - 'desReqCurrents' can be short - each car is given a value from remaining current
        :param vehicles: Sequence of cars to set
        :param desReqCurrents: Corresponding sequence of desired request currents (amps)
        :param onlyWake: Flag indicating to only wake up vehicles needing their currents set
        :param waitForCompletion: Flag indicating to wait for final request current to be set
        """
        reqCurrents = self.limitRequestCurrents(vehicles, desReqCurrents)
        wakeTasks: list[asyncio.Task] = []

        # run tasks to wake sleeping cars of interest
        for idx, dtls in enumerate(vehicles):
            if dtls.pluggedInAtHome() and not dtls.awake():
                # wake when current is to change or to get new temperature reading for onlyWake
                if (reqCurrents[idx] != dtls.chargeCurrentRequest) or onlyWake:
                    wakeTasks.append(self.tsIntrfc.getWakeTask(dtls))

        await Interpret.waitForTasks(wakeTasks)

        if not onlyWake:
            indices: list[int] = list(range(len(vehicles)))

            # to decrease first, sort indices ascending by increase in request current
            indices.sort(key=lambda i: reqCurrents[i] - vehicles[i].chargeCurrentRequest)
            lastIndex = indices[len(indices) - 1]

            for idx in indices:
                if vehicles[idx].pluggedInAtHome():
                    wait4Compl = (idx != lastIndex) or waitForCompletion
                    await self.tsIntrfc.setRequestCurrent(vehicles[idx], reqCurrents[idx],
                                                          waitForCompletion=wait4Compl)
            # end for
    # end setReqCurrents(Sequence[CarDetails], Sequence[float], bool, bool)

# end class ReqCurrentControl


class AutoCurrentControl(ReqCurrentControl):
    """Processor to automatically set request currents based on cars' charging needs"""

    async def process(self) -> None:
        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                logging.info(dtls.chargingStatusSummary())
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
        percent: int | None = self.persistData.getVal(self.PRIOR_CHARGE_LIMIT, dtls.vin)

        if percent is None:
            # use an average value if no limit is persisted
            percent = (dtls.limitMinPercent + dtls.limitMaxPercent) // 2

        return dtls.limitChargeLimit(percent)
    # end getPriorLimit(CarDetails)

    async def automaticallySetReqCurrent(self, onlyWake=False,
                                         waitForCompletion=False) -> None:
        """Automatically set cars' request currents based on each cars' charging needs
           - depends on having battery health details
        :param onlyWake: Flag indicating to only wake up vehicles needing their currents set
        :param waitForCompletion: Flag indicating to wait for final request current to be set
        """
        energiesNeeded: list[float] = []
        totalEnergyNeeded = 0.0

        for dtls in self.vehicles:
            energyNeeded = dtls.energyNeededC(None if not dtls.chargeLimitIsMin()
                                              else self.getPriorLimit(dtls))
            if not onlyWake:
                summary = dtls.chargingStatusSummary()

                if summary.updatedSinceLastSummary or energyNeeded:
                    if energyNeeded:
                        summary += f" ({energyNeeded:.1f} kWh < limit)"
                    logging.info(summary)

            energiesNeeded.append(energyNeeded)
            totalEnergyNeeded += energyNeeded
        # end for

        if totalEnergyNeeded:
            reqCurrents = [self.derateTotalCurrent() * (
                    energy / totalEnergyNeeded) for energy in energiesNeeded]

            await self.setReqCurrents(self.vehicles, reqCurrents, onlyWake, waitForCompletion)
    # end automaticallySetReqCurrent(bool, bool)

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
            # wake vehicles needing their currents set in case battery levels change
            tg.create_task(self.automaticallySetReqCurrent(onlyWake=True))
        # end async with (tasks are awaited)

        await self.automaticallySetReqCurrent()
    # end process()

    async def restoreChargeLimit(self, dtls: CarDetails, waitForCompletion=False) -> None:
        """If the specified vehicle's charge limit is minimum,
           ensure the vehicle is awake and restore its charge limit to a persisted percent
        :param dtls: Details of the vehicle to restore
        :param waitForCompletion: Flag indicating to wait for limit to be restored
        """
        if dtls.chargeLimitIsMin():
            # this vehicle is set to charge limit minimum
            percent = self.getPriorLimit(dtls)

            if percent != dtls.chargeLimit:
                if not dtls.awake():
                    # try to wake up this car
                    await self.tsIntrfc.getWakeTask(dtls)

                await self.tsIntrfc.setChargeLimit(dtls, percent, waitForCompletion)
            else:
                logging.info(f"No change made to {dtls.displayName} charge limit")
    # end restoreChargeLimit(CarDetails, bool)

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
                tg.create_task(self.startChargingWhenReady(dtls, onlyWake=True))
                tg.create_task(self.restoreChargeLimit(dtls, waitForCompletion=True))
            # wake vehicles needing their currents set in case battery levels change
            tg.create_task(self.automaticallySetReqCurrent(onlyWake=True))
        # end async with (tasks are awaited)

        await self.automaticallySetReqCurrent(waitForCompletion=True)

        async with asyncio.TaskGroup() as tg:
            for dtls in self.vehicles:
                tg.create_task(self.startChargingWhenReady(dtls))
            # end for
        # end async with (tasks are awaited)
    # end process()

    async def startChargingWhenReady(self, dtls: CarDetails, onlyWake=False) -> None:
        """Start charging if plugged in at home, not charging and could use a charge
        :param dtls: Details of the vehicle to start charging
        :param onlyWake: Flag indicating to only wake up a vehicle needing to charge
        """
        if dtls.pluggedInAtHome():
            if not dtls.awake():
                await self.tsIntrfc.getWakeTask(dtls)

            if not onlyWake and (dtls.dataAge() > 15 or dtls.modifiedBySetter):
                # make sure we have the current vehicle details
                await self.tsIntrfc.getCurrentState(dtls, attempts=5)

        if not onlyWake and dtls.pluggedInAtHome() and dtls.chargingState != "Charging" \
                and dtls.chargeNeeded():
            # this vehicle is plugged in at home, not charging and could use a charge
            retries = 4

            while (dtls.chargingState == "Complete" or dtls.chargingState == "NoPower") \
                    and dtls.chargeNeeded() and retries:
                # wait for charging state to change from Complete or NoPower
                await asyncio.sleep(14)
                await self.tsIntrfc.getCurrentState(dtls)
                retries -= 1
            # end while

            await self.tsIntrfc.startCharging(dtls)
    # end startChargingWhenReady(CarDetails, bool)

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

        # Sometimes the plugged-in state changes when a car awakes
        if dtls.pluggedInAtHome():
            await self.tsIntrfc.stopCharging(dtls, waitForCompletion=True)

            if not dtls.chargeLimitIsMin():
                # this vehicle is not set to minimum limit already
                self.persistData.setVal(self.PRIOR_CHARGE_LIMIT, dtls.vin, dtls.chargeLimit)
                await self.tsIntrfc.setChargeLimit(dtls, dtls.limitMinPercent)
    # end disableCarCharging(CarDetails)

# end class CarChargingDisabler


class StatusPresenter(TessieProc):
    """Processor to just display status"""

    async def process(self) -> None:
        for dtls in self.vehicles:
            logging.info(dtls.chargingStatusSummary())
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
