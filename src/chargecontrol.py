
import json
import logging
from argparse import ArgumentParser, Namespace
from datetime import timedelta
from logging.config import dictConfig
from pathlib import Path
from threading import current_thread, Thread

import sys
from math import isqrt
from requests import HTTPError, request, Response
from time import time


class CarDetails(object):
    """Details of a vehicle as reported by Tessie"""

    def __init__(self, vehicleState: dict):
        self.vin: str = vehicleState["vin"]
        self.chargeState: dict = vehicleState["charge_state"]
        self.displayName: str = vehicleState["display_name"]
        self.lastSeen = self.chargeState["timestamp"] * 0.001  # convert ms to seconds
        self.chargingState: str = self.chargeState["charging_state"]
        self.chargeLimit: int = self.chargeState["charge_limit_soc"]
        self.limitMinPercent: int = self.chargeState["charge_limit_soc_min"]
        self.batteryLevel: int = self.chargeState["usable_battery_level"]
    # end __init__(dict)

    def __str__(self) -> str:
        return f"{self.displayName}@{self.batteryLevel}%"
    # end __str__()

# end class CarDetails


class CcException(HTTPError):
    """Detected exceptions"""

    @classmethod
    def fromError(cls, badResponse: Response):
        """Factory method for bad responses"""
        if isinstance(badResponse.reason, bytes):
            # Some servers choose to localize their reason strings.
            try:
                prefix = badResponse.reason.decode('utf-8')
            except UnicodeDecodeError:
                prefix = badResponse.reason.decode('iso-8859-1')
        else:
            prefix = badResponse.reason

        if not prefix:
            prefix = "Error"

        return cls(f"{badResponse.status_code} {prefix} in {current_thread().name}"
                   f" {badResponse.text} for url {badResponse.url}",
                   response=badResponse)
    # end fromError(Response)

# end class CcException


class ChargeControl(object):
    """Controls vehicles charging activity"""

    def __init__(self, args: Namespace):
        self.disable: bool = args.disable
        self.enable: bool = args.enable
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {ChargeControl.loadToken()}"
        }
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

    @staticmethod
    def findParmPath() -> Path:
        # look in child with a specific name
        pp = Path("parmFiles")

        if not pp.is_dir():
            # just use current directory
            pp = Path(".")

        return pp
    # end findParmPath()

    @staticmethod
    def loadToken() -> str:
        filePath = Path(ChargeControl.findParmPath(), "accesstoken.json")

        with open(filePath, "r", encoding="utf-8") as tokenFile:

            return json.load(tokenFile)["token"]
    # end loadToken()

    def getStateOfActiveVehicles(self) -> list[CarDetails]:
        url = "https://api.tessie.com/vehicles"
        queryParams = {"only_active": "true"}

        response = request("GET", url, params=queryParams, headers=self.headers)

        if response.status_code != 200:
            raise CcException.fromError(response)

        allResults: list[dict] = response.json()["results"]

        return [CarDetails(car["last_state"]) for car in allResults]
    # end getStateOfActiveVehicles()

    def getStatus(self, dtls: CarDetails) -> str:
        url = f"https://api.tessie.com/{dtls.vin}/status"

        response = request("GET", url, headers=self.headers)

        if response.status_code == 200:
            return response.json()["status"]
        else:
            logging.error(CcException.fromError(response))
            return "unknown"
    # end getStatus(CarDetails)

    def setChargeLimit(self, dtls: CarDetails, percent: int) -> None:
        url = f"https://api.tessie.com/{dtls.vin}/command/set_charge_limit"
        queryParams = {
            "retry_duration": 60,
            "wait_for_completion": "true",
            "percent": percent
        }

        response = request("GET", url, params=queryParams, headers=self.headers)

        if response.status_code != 200:
            raise CcException.fromError(response)

        logging.info(f"{dtls.displayName} charge limit changed"
                     f" from {dtls.chargeLimit}% to {percent}%")
        dtls.chargeLimit = percent
    # end setChargeLimit(CarDetails, int)

    def startCharging(self, dtls: CarDetails) -> None:
        url = f"https://api.tessie.com/{dtls.vin}/command/start_charging"
        queryParams = {
            "retry_duration": 60,
            "wait_for_completion": "true"
        }

        response = request("GET", url, params=queryParams, headers=self.headers)

        if response.status_code != 200:
            raise CcException.fromError(response)

        logging.info(f"{dtls.displayName} charging started")
        dtls.chargingState = "Charging"
    # end startCharging(CarDetails)

    def enableCarCharging(self, dtls: CarDetails) -> None:
        """Raise the charge limit to mean if minimum,
           then start charging if plugged in and not charging"""

        try:
            if dtls.chargeLimit == dtls.limitMinPercent:
                # this vehicle is set to charge limit minimum
                limitStdPercent = dtls.chargeState["charge_limit_soc_std"]
                geometricMeanLimitPercent = isqrt(dtls.limitMinPercent * limitStdPercent)

                self.setChargeLimit(dtls, geometricMeanLimitPercent)
        except Exception as e:
            logException(e)

        try:
            if dtls.chargingState != "Disconnected" and dtls.chargingState != "Charging":
                # this vehicle is plugged in and not charging

                if dtls.batteryLevel < dtls.chargeLimit:
                    self.startCharging(dtls)
        except Exception as e:
            logException(e)
    # end enableCarCharging(CarDetails)

    def disableCarCharging(self, dtls: CarDetails) -> None:
        """Lower the charge limit to minimum if plugged in and not minimum already"""

        try:
            if dtls.chargingState != "Disconnected" \
                    and dtls.chargeLimit > dtls.limitMinPercent:
                # this vehicle is plugged in and not set to charge limit minimum already

                self.setChargeLimit(dtls, dtls.limitMinPercent)
        except Exception as e:
            logException(e)
    # end disableCarCharging(CarDetails)

    def main(self) -> None:
        vehicles = self.getStateOfActiveVehicles()
        workMethod = self.enableCarCharging if self.enable \
            else self.disableCarCharging if self.disable \
            else None
        workers = []

        for carDetails in vehicles:
            # log the current charging state
            logging.info(f"{carDetails.displayName} was {self.getStatus(carDetails)}"
                         f" {timedelta(seconds=int(time() - carDetails.lastSeen + 0.5))} ago"
                         f" with charging {carDetails.chargingState}"
                         f", charge limit {carDetails.chargeLimit}%"
                         f" and battery {carDetails.batteryLevel}%")

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
