
import json
import logging
from argparse import ArgumentParser, Namespace
from logging.config import dictConfig
from pathlib import Path
from threading import current_thread, Thread

import sys
from math import isqrt
from time import sleep

from tessie.cardetails import CarDetails
from tessie.tessieinterface import TessieInterface


class ChargeControl(object):
    """Controls vehicles charging activity"""

    def __init__(self, args: Namespace):
        self.disable: bool = args.disable
        self.enable: bool = args.enable
        self.setLimit: int | None = args.setLimit
        self.carIntrfc = TessieInterface()
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

    def startChargingWhenReady(self, dtls: CarDetails) -> None:
        """Start charging if plugged in, not charging and could use a charge"""

        try:
            if dtls.pluggedIn():
                # make sure we have the current battery level and charge limit
                self.carIntrfc.getCurrentState(dtls)
        except Exception as e:
            logException(e)

        try:
            if dtls.pluggedIn() and dtls.chargingState != "Charging" \
                    and dtls.batteryLevel < dtls.chargeLimit:
                # this vehicle is plugged in, not charging and could use a charge
                retries = 6

                while dtls.chargingState == "Complete" \
                        and dtls.batteryLevel < dtls.chargeLimit and retries:
                    # wait for charging state to change from Complete
                    sleep(3.2)
                    self.carIntrfc.getCurrentState(dtls)
                    retries -= 1
                # end while

                self.carIntrfc.startCharging(dtls)
        except Exception as e:
            logException(e)
    # end startChargingWhenReady(CarDetails)

    def enableCarCharging(self, dtls: CarDetails) -> None:
        """Raise the charge limit to mean if minimum then start charging when ready"""

        try:
            if (dtls.chargeLimitIsMin() or dtls.pluggedIn()) and not dtls.awake():
                # try to wake up this car
                self.carIntrfc.wake(dtls)
        except Exception as e:
            logException(e)

        try:
            if dtls.chargeLimitIsMin():
                # this vehicle is set to charge limit minimum
                limitStdPercent: int = dtls.chargeState["charge_limit_soc_std"]
                # arithmeticMeanLimitPercent = (dtls.limitMinPercent + limitStdPercent) // 2
                geometricMeanLimitPercent = isqrt(dtls.limitMinPercent * limitStdPercent)

                self.carIntrfc.setChargeLimit(dtls, geometricMeanLimitPercent)
        except Exception as e:
            logException(e)

        self.startChargingWhenReady(dtls)
    # end enableCarCharging(CarDetails)

    def disableCarCharging(self, dtls: CarDetails) -> None:
        """Lower the charge limit to minimum if plugged in and not minimum already"""

        try:
            if dtls.pluggedIn() and not dtls.chargeLimitIsMin():
                # this vehicle is plugged in and not set to charge limit minimum already

                if not dtls.awake():
                    self.carIntrfc.wake(dtls)
                self.carIntrfc.setChargeLimit(dtls, dtls.limitMinPercent,
                                              waitForCompletion=False)
        except Exception as e:
            logException(e)
    # end disableCarCharging(CarDetails)

    def setChargeLimit(self, dtls: CarDetails) -> None:
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
                        self.carIntrfc.wake(dtls)
                    limitMaxPercent: int = dtls.chargeState["charge_limit_soc_max"]

                    if self.setLimit > limitMaxPercent:
                        logging.info(f"{self.setLimit}% is too large"
                                     f" -- maximum is {limitMaxPercent}%")
                        self.setLimit = limitMaxPercent

                    self.carIntrfc.setChargeLimit(dtls, self.setLimit,
                                                  waitForCompletion=False)
        except Exception as e:
            logException(e)
    # end setChargeLimit(CarDetails)

    def main(self) -> None:
        logging.debug(f"Starting {' '.join(sys.argv)}")
        vehicles = self.carIntrfc.getStateOfActiveVehicles()
        workMethod = self.enableCarCharging if self.enable \
            else self.disableCarCharging if self.disable \
            else self.setChargeLimit if self.setLimit \
            else None
        workers = []

        for carDetails in vehicles:
            logging.info(carDetails.currentChargingStatus())

            if workMethod:
                thrd = Thread(target=workMethod, args=(carDetails, ),
                              name=f"{carDetails.displayName}-Thread")
                workers.append(thrd)
                thrd.start()
        # end for

        for worker in workers:
            worker.join()
    # end main()

# end class ChargeControl


def configLogging() -> None:
    filePath = Path(sys.path[0], "chargecontrol.logging.config.json")

    with open(filePath, "r", encoding="utf-8") as loggingConfigFile:
        loggingConfig: dict = json.load(loggingConfigFile)

    filePath = Path(loggingConfig["handlers"]["file"]["filename"])

    if filePath.exists():
        # add a blank line each subsequent execution
        with open(filePath, "a", encoding="utf-8") as logFile:
            logFile.write("\n")

    dictConfig(loggingConfig)
# end configLogging()


def logException(exceptn: BaseException) -> None:
    logging.error(exceptn)
    logging.debug(f"{exceptn.__class__.__name__} suppressed in {current_thread().name}:",
                  exc_info=exceptn)
# end logException(BaseException)


if __name__ == "__main__":
    clArgs = ChargeControl.parseArgs()
    configLogging()
    try:
        chrgCtl = ChargeControl(clArgs)
        chrgCtl.main()
    except Exception as xcpt:
        logException(xcpt)
