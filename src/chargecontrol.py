
import json
import logging
from argparse import ArgumentParser, Namespace
from datetime import timedelta
from logging.config import dictConfig
from pathlib import Path
from threading import current_thread, Thread

import sys
from math import isqrt
from time import sleep, time

from tessieinterface import CarDetails, TessieInterface


class ChargeControl(object):
    """Controls vehicles charging activity"""

    def __init__(self, args: Namespace):
        self.disable: bool = args.disable
        self.enable: bool = args.enable
        self.carIntrfc = TessieInterface()
    # end __init__(Namespace)

    @staticmethod
    def parseArgs() -> Namespace:
        """Parse command line arguments"""
        ap = ArgumentParser(description="Module to control charging all authorized cars")
        group = ap.add_mutually_exclusive_group()
        group.add_argument("-d", "--disable", action="store_true",
                           help="disable charging")
        group.add_argument("-e", "--enable", action="store_true",
                           help="enable charging")

        return ap.parse_args()
    # end parseArgs()

    def startChargingWhenReady(self, dtls: CarDetails) -> None:
        """Start charging if plugged in, not charging and could use a charge"""

        if dtls.chargingState != "Disconnected" and dtls.chargingState != "Charging" \
                and dtls.batteryLevel < dtls.chargeLimit:
            # this vehicle is plugged in, not charging and could use a charge
            retries = 15

            while dtls.chargingState == "Complete" \
                    and dtls.batteryLevel < dtls.chargeLimit and retries:
                # wait for charging state to change from Complete
                sleep(0.5)
                self.carIntrfc.getState(dtls)
                self.logStatus(dtls)
                retries -= 1
            # end while

            self.carIntrfc.startCharging(dtls)
    # end startChargingWhenReady(CarDetails)

    def enableCarCharging(self, dtls: CarDetails) -> None:
        """Raise the charge limit to mean if minimum then start charging when ready"""

        try:
            if dtls.chargeLimit == dtls.limitMinPercent:
                # this vehicle is set to charge limit minimum
                limitStdPercent: int = dtls.chargeState["charge_limit_soc_std"]
                # arithmeticMeanLimitPercent = (dtls.limitMinPercent + limitStdPercent) // 2
                geometricMeanLimitPercent = isqrt(dtls.limitMinPercent * limitStdPercent)

                self.carIntrfc.setChargeLimit(dtls, geometricMeanLimitPercent)
        except Exception as e:
            logException(e)
            try:
                # make sure we have the current charge limit
                self.carIntrfc.getState(dtls)
            except Exception as e:
                logException(e)

        try:
            self.startChargingWhenReady(dtls)
        except Exception as e:
            logException(e)
    # end enableCarCharging(CarDetails)

    def disableCarCharging(self, dtls: CarDetails) -> None:
        """Lower the charge limit to minimum if plugged in and not minimum already"""

        try:
            if dtls.chargingState != "Disconnected" \
                    and dtls.chargeLimit > dtls.limitMinPercent:
                # this vehicle is plugged in and not set to charge limit minimum already

                self.carIntrfc.setChargeLimit(dtls, dtls.limitMinPercent)
        except Exception as e:
            logException(e)
    # end disableCarCharging(CarDetails)

    def logStatus(self, dtls: CarDetails) -> None:
        # log the current charging status
        logging.info(f"{dtls.displayName} was {self.carIntrfc.getStatus(dtls)}"
                     f" {timedelta(seconds=int(time() - dtls.lastSeen + 0.5))} ago"
                     f" with charging {dtls.chargingState}"
                     f", charge limit {dtls.chargeLimit}%"
                     f" and battery {dtls.batteryLevel}%")
    # end logStatus(CarDetails)

    def main(self) -> None:
        vehicles = self.carIntrfc.getStateOfActiveVehicles()
        workMethod = self.enableCarCharging if self.enable \
            else self.disableCarCharging if self.disable \
            else None
        workers = []

        for carDetails in vehicles:
            self.logStatus(carDetails)

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
