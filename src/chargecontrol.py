
import asyncio
import logging
import sys
from argparse import ArgumentParser, Namespace
from contextlib import aclosing

from tessie import CarDetails, TessieInterface
from util import Configure, ExceptionGroupHandler


class ChargeControl(object):
    """Controls vehicles charging activity"""

    def __init__(self, args: Namespace):
        self.disable: bool = args.disable
        self.enableLimit: int | None = args.enableLimit
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
        group.add_argument("-e", "--enableLimit", type=int, metavar="percent",
                           help="enable charging with limit if 50%%")
        group.add_argument("-s", "--setLimit", type=int, metavar="percent",
                           help="set charge limits if 50%%")

        return ap.parse_args()
    # end parseArgs()

    @staticmethod
    async def startChargingWhenReady(carIntrfc: TessieInterface, dtls: CarDetails) -> None:
        """Start charging if plugged in at home, not charging and could use a charge"""

        if dtls.pluggedInAtHome():
            # make sure we have the current battery level and charge limit
            await carIntrfc.getCurrentState(dtls)

        if dtls.pluggedInAtHome() and dtls.chargingState != "Charging" and dtls.chargeNeeded():
            # this vehicle is plugged in at home, not charging and could use a charge
            retries = 6

            while dtls.chargingState == "Complete" and dtls.chargeNeeded() and retries:
                # wait for charging state to change from Complete
                await asyncio.sleep(3.2)
                await carIntrfc.getCurrentState(dtls, attempts=1)
                retries -= 1
            # end while

            await carIntrfc.startCharging(dtls)
    # end startChargingWhenReady(TessieInterface, CarDetails)

    async def enableCarCharging(self, carIntrfc: TessieInterface, dtls: CarDetails) -> None:
        """Raise the charge limit if minimum then start charging when ready"""

        if (dtls.chargeLimitIsMin() or dtls.pluggedInAtHome()) and not dtls.awake():
            # try to wake up this car
            await carIntrfc.wake(dtls)

        if dtls.chargeLimitIsMin():
            # this vehicle is set to charge limit minimum
            self.enableLimit = dtls.limitToCapabilities(self.enableLimit)

            if self.enableLimit != dtls.chargeLimit:
                await carIntrfc.setChargeLimit(dtls, self.enableLimit)

        await self.startChargingWhenReady(carIntrfc, dtls)
    # end enableCarCharging(TessieInterface, CarDetails)

    @staticmethod
    async def disableCarCharging(carIntrfc: TessieInterface, dtls: CarDetails) -> None:
        """Stop charging and lower the charge limit to minimum
           if plugged in at home and not minimum already"""

        if dtls.pluggedInAtHome():
            # this vehicle is plugged in at home

            if not dtls.awake():
                await carIntrfc.wake(dtls)
            await carIntrfc.stopCharging(dtls)

            if not dtls.chargeLimitIsMin():
                # this vehicle is not set to minimum limit already
                await carIntrfc.setChargeLimit(dtls, dtls.limitMinPercent,
                                               waitForCompletion=False)
    # end disableCarCharging(TessieInterface, CarDetails)

    @staticmethod
    async def setChargeStop(dtls: CarDetails, percent: int, carIntrfc: TessieInterface, *,
                            waitForCompletion=True) -> None:
        if dtls.chargeLimitIsMin():
            # this vehicle is set to charge limit minimum
            percent = dtls.limitToCapabilities(percent)

            if percent != dtls.chargeLimit:
                if not dtls.awake():
                    await carIntrfc.wake(dtls)

                await carIntrfc.setChargeLimit(dtls, percent,
                                               waitForCompletion=waitForCompletion)
            else:
                logging.info(f"No change made to {dtls.displayName}")
    # end setChargeStop(CarDetails, int, TessieInterface, bool)

    async def setChargeLimit(self, carIntrfc: TessieInterface, dtls: CarDetails) -> None:
        """Set the charge limit if minimum"""

        await self.setChargeStop(dtls, self.setLimit, carIntrfc, waitForCompletion=False)
    # end setChargeLimit(TessieInterface, CarDetails)

    async def main(self) -> None:
        logging.debug(f"Starting {' '.join(sys.argv)}")

        async with aclosing(TessieInterface()) as tsIntrfc:
            tsIntrfc: TessieInterface
            vehicles = await tsIntrfc.getStateOfActiveVehicles()

            async with asyncio.TaskGroup() as tg:
                for dtls in vehicles:
                    logging.info(dtls.currentChargingStatus())
                    tName = f"{dtls.displayName}-task"

                    match True:
                        case _ if self.enableLimit:
                            tg.create_task(self.enableCarCharging(tsIntrfc, dtls), name=tName)
                        case _ if self.disable:
                            tg.create_task(self.disableCarCharging(tsIntrfc, dtls), name=tName)
                        case _ if self.setLimit:
                            tg.create_task(self.setChargeLimit(tsIntrfc, dtls), name=tName)
                    # end match
                # end for
            # end async with (tasks are awaited)
        # end async with (carIntrfc is closed)
    # end main()

# end class ChargeControl


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
