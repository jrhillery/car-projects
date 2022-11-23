
import asyncio
import logging
from argparse import ArgumentParser, Namespace
from contextlib import aclosing

import sys
from math import isqrt

from tessie import CarDetails, TessieInterface
from util import Configure


class ChargeControl(object):
    """Controls vehicles charging activity"""
    carIntrfc: TessieInterface

    def __init__(self, args: Namespace):
        self.disable: bool = args.disable
        self.enable: bool = args.enable
        self.setLimit: int | None = args.setLimit
    # end __init__(Namespace)

    @staticmethod
    def parseArgs() -> Namespace:
        """Parse command line arguments"""
        ap = ArgumentParser(description="Module to control charging all authorized cars",
                            epilog="Just displays status when no option is specified")
        group = ap.add_mutually_exclusive_group()
        group.add_argument("-d", "--disable", action="store_true",
                           help="disable charging")
        group.add_argument("-e", "--enable", action="store_true",
                           help="enable charging")
        group.add_argument("-s", "--setLimit", type=int, metavar="percent",
                           help="set charge limits if 50%%")

        return ap.parse_args()
    # end parseArgs()

    async def startChargingWhenReady(self, dtls: CarDetails) -> None:
        """Start charging if plugged in at home, not charging and could use a charge"""

        try:
            if dtls.pluggedInAtHome():
                # make sure we have the current battery level and charge limit
                await self.carIntrfc.getCurrentState(dtls)
        except Exception as e:
            logException(e)

        try:
            if dtls.pluggedInAtHome() \
                    and dtls.chargingState != "Charging" and dtls.chargeNeeded():
                # this vehicle is plugged in at home, not charging and could use a charge
                retries = 6

                while dtls.chargingState == "Complete" and dtls.chargeNeeded() and retries:
                    # wait for charging state to change from Complete
                    await asyncio.sleep(3.2)
                    await self.carIntrfc.getCurrentState(dtls)
                    retries -= 1
                # end while

                await self.carIntrfc.startCharging(dtls)
        except Exception as e:
            logException(e)
    # end startChargingWhenReady(CarDetails)

    async def enableCarCharging(self, dtls: CarDetails) -> None:
        """Raise the charge limit to mean if minimum then start charging when ready"""

        try:
            if (dtls.chargeLimitIsMin() or dtls.pluggedInAtHome()) and not dtls.awake():
                # try to wake up this car
                await self.carIntrfc.wake(dtls)
        except Exception as e:
            logException(e)

        try:
            if dtls.chargeLimitIsMin():
                # this vehicle is set to charge limit minimum
                limitStdPercent: int = dtls.chargeState["charge_limit_soc_std"]
                # arithmeticMeanLimitPercent = (dtls.limitMinPercent + limitStdPercent) // 2
                geometricMeanLimitPercent = isqrt(dtls.limitMinPercent * limitStdPercent)

                await self.carIntrfc.setChargeLimit(dtls, geometricMeanLimitPercent)
        except Exception as e:
            logException(e)

        await self.startChargingWhenReady(dtls)
    # end enableCarCharging(CarDetails)

    async def disableCarCharging(self, dtls: CarDetails) -> None:
        """Lower the charge limit to minimum if plugged in at home and not minimum already"""

        try:
            if dtls.pluggedInAtHome() and not dtls.chargeLimitIsMin():
                # this vehicle is plugged in at home and not set to minimum limit already

                if not dtls.awake():
                    await self.carIntrfc.wake(dtls)
                await self.carIntrfc.setChargeLimit(dtls, dtls.limitMinPercent,
                                                    waitForCompletion=False)
        except Exception as e:
            logException(e)
    # end disableCarCharging(CarDetails)

    async def setChargeLimit(self, dtls: CarDetails) -> None:
        """Set the charge limit if minimum"""

        try:
            if dtls.chargeLimitIsMin():
                # this vehicle is set to charge limit minimum

                if self.setLimit < dtls.limitMinPercent:
                    logging.info(f"{self.setLimit}% is too small"
                                 f" -- minimum is {dtls.limitMinPercent}%"
                                 f" so no change made to {dtls.displayName}")
                else:
                    if not dtls.awake():
                        await self.carIntrfc.wake(dtls)
                    limitMaxPercent: int = dtls.chargeState["charge_limit_soc_max"]

                    if self.setLimit > limitMaxPercent:
                        logging.info(f"{self.setLimit}% is too large"
                                     f" -- maximum is {limitMaxPercent}%")
                        self.setLimit = limitMaxPercent

                    await self.carIntrfc.setChargeLimit(dtls, self.setLimit,
                                                        waitForCompletion=False)
        except Exception as e:
            logException(e)
    # end setChargeLimit(CarDetails)

    async def main(self) -> None:
        logging.debug(f"Starting {' '.join(sys.argv)}")

        async with aclosing(TessieInterface()) as carIntrfc:
            carIntrfc: TessieInterface
            await carIntrfc.setSession()
            self.carIntrfc = carIntrfc
            vehicles = await carIntrfc.getStateOfActiveVehicles()
            workMethod = self.enableCarCharging if self.enable \
                else self.disableCarCharging if self.disable \
                else self.setChargeLimit if self.setLimit \
                else None
            async with asyncio.TaskGroup() as tasks:
                for carDetails in vehicles:
                    logging.info(carDetails.currentChargingStatus())

                    if workMethod:
                        tasks.create_task(workMethod(carDetails),
                                          name=f"{carDetails.displayName}-task")
                # end for
            # end async with (tasks are awaited)
    # end main()

# end class ChargeControl


def logException(exceptn: BaseException) -> None:
    logging.error(exceptn)
    curTask = asyncio.current_task()
    curTaskName = "" if curTask is None else f" in {curTask.get_name()}"
    logging.debug(f"{exceptn.__class__.__name__} suppressed{curTaskName}:",
                  exc_info=exceptn)
# end logException(BaseException)


if __name__ == "__main__":
    clArgs = ChargeControl.parseArgs()
    Configure.logToFile()
    try:
        chrgCtl = ChargeControl(clArgs)
        asyncio.run(chrgCtl.main())
    except Exception as xcpt:
        logException(xcpt)
