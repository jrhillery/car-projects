
import json
import logging
from argparse import ArgumentParser, Namespace
from datetime import timedelta
from logging.config import dictConfig
from pathlib import Path
from threading import current_thread, Thread

import sys
from math import isqrt
from requests import request
from time import time


class CarDetails(object):

    def __init__(self, vehicleState: dict, callTime: float):
        self.vin: str = vehicleState["vin"]
        self.chargeState: dict = vehicleState["charge_state"]
        self.displayName: str = vehicleState["display_name"]
        self.chargingState: str = self.chargeState["charging_state"]
        self.chargeLimit: int = self.chargeState["charge_limit_soc"]
        self.limitMinPercent: int = self.chargeState["charge_limit_soc_min"]
        self.batteryLevel: int = self.chargeState["usable_battery_level"]
        lastSeen = self.chargeState["timestamp"] * 0.001  # convert ms to seconds
        self.sinceLastSeen = timedelta(seconds=int(callTime - lastSeen + 0.5))
    # end __init__(dict, float)

    def __str__(self) -> str:
        return f"{self.displayName}@{self.batteryLevel}%"
    # end __str__()

# end class CarDetails


class ChargeControl(object):
    """Controls vehicles charging activity"""

    def __init__(self, args: Namespace):
        self.enable: bool = args.enable
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {ChargeControl.loadToken()}"
        }
    # end __init__()

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

        with open(filePath, "r", encoding="utf-8") as file:

            return json.load(file)["token"]
    # end loadToken()

    def getStateOfActiveVehicles(self) -> list[dict]:
        url = "https://api.tessie.com/vehicles"
        queryParams = {"only_active": "true"}

        response = request("GET", url, params=queryParams, headers=self.headers)
        response.raise_for_status()

        return response.json()["results"]
    # end getStateOfActiveVehicles()

    def getStatus(self, dtls: CarDetails) -> str:
        url = f"https://api.tessie.com/{dtls.vin}/status"

        response = request("GET", url, headers=self.headers)

        if response.status_code == 200:
            return response.json()["status"]
        else:
            logging.error(response.text)
            return "unknown"
    # end getStatus(CarDetails)

    def setChargeLimit(self, dtls: CarDetails, percent: int) -> bool:
        url = f"https://api.tessie.com/{dtls.vin}/command/set_charge_limit"
        queryParams = {
            "retry_duration": 60,
            "wait_for_completion": "true",
            "percent": percent
        }

        response = request("GET", url, params=queryParams, headers=self.headers)

        if response.status_code == 200:
            logging.info(f"{dtls.displayName} charge limit changed"
                         f" from {dtls.chargeLimit}%"
                         f" to {percent}%")
            dtls.chargeLimit = percent
            return True
        else:
            logging.error(response.text)
            return False
    # end setChargeLimit(CarDetails, int)

    def startCharging(self, dtls: CarDetails) -> bool:
        url = f"https://api.tessie.com/{dtls.vin}/command/start_charging"
        queryParams = {
            "retry_duration": 60,
            "wait_for_completion": "true"
        }

        response = request("GET", url, params=queryParams, headers=self.headers)

        if response.status_code == 200:
            logging.info(f"{dtls.displayName} charging started")
            dtls.chargingState = "Charging"
            return True
        else:
            logging.error(response.text)
            return False
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

            if dtls.chargingState != "Disconnected" and dtls.chargingState != "Charging":
                # this vehicle is plugged in and not charging

                if dtls.batteryLevel < dtls.chargeLimit:
                    self.startCharging(dtls)
        except Exception as e:
            handleException(e)
    # end enableCarCharging(CarDetails)

    def disableCarCharging(self, dtls: CarDetails) -> None:
        """Lower the charge limit to minimum if plugged in and not minimum already"""

        try:
            if dtls.chargingState != "Disconnected" \
                    and dtls.chargeLimit > dtls.limitMinPercent:
                # this vehicle is plugged in and not set to charge limit minimum already

                self.setChargeLimit(dtls, dtls.limitMinPercent)
        except Exception as e:
            handleException(e)
    # end disableCarCharging(CarDetails)

    def main(self) -> None:
        vehicles = self.getStateOfActiveVehicles()
        callTime = time()
        workers = []

        for vehicle in vehicles:
            carDetails = CarDetails(vehicle["last_state"], callTime)

            # log the current charging state
            logging.info(f"{carDetails.displayName} was {self.getStatus(carDetails)}"
                         f" {carDetails.sinceLastSeen} ago"
                         f" with charging {carDetails.chargingState}"
                         f", charge limit {carDetails.chargeLimit}%"
                         f" and battery {carDetails.batteryLevel}%")

            if self.enable:
                method = self.enableCarCharging
            else:
                method = self.disableCarCharging

            thrd = Thread(target=method, args=(carDetails, ),
                          name=f"Thread-{carDetails.displayName}")
            workers.append(thrd)
            thrd.start()
        # end for

        for worker in workers:
            worker.join()
    # end main()

# end class ChargeControl


def parseArgs() -> Namespace:
    """Parse command line arguments"""
    ap = ArgumentParser(description="Module to control car charging")
    ap.add_argument("-e", "--enable", action="store_true",
                    help="enable charging (default is to disable)")

    return ap.parse_args()
# end parseArgs()


def configLogging() -> None:
    filePath = Path(sys.path[0], "chargecontrol.logging.config.json")

    with open(filePath, "r", encoding="utf-8") as file:
        loggingConfig: dict = json.load(file)

    filePath = Path(loggingConfig["handlers"]["file"]["filename"])

    if filePath.exists():
        # add a blank line each subsequent execution
        with open(filePath, "a", encoding="utf-8") as logFile:
            logFile.write("\n")

    dictConfig(loggingConfig)
# end configLogging()


def handleException(exceptn: Exception) -> None:
    logging.error(exceptn)
    logging.debug(f"Exception suppressed in thread {current_thread().name}:",
                  exc_info=exceptn)
# end handleException(Exception)


if __name__ == "__main__":
    clArgs = parseArgs()
    configLogging()
    try:
        chrgCtl = ChargeControl(clArgs)
        chrgCtl.main()
    except Exception as xcpt:
        handleException(xcpt)
