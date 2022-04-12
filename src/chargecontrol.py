
import json
import logging
from argparse import ArgumentParser, Namespace
from datetime import timedelta
from logging.config import dictConfig
from pathlib import Path

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
        lastSeen = self.chargeState["timestamp"] * 0.001  # convert ms to seconds
        self.sinceLastSeen = timedelta(seconds=int(callTime - lastSeen + 0.5))
    # end __init__(dict, float)

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
        filePath = Path(ChargeControl.findParmPath(), "accesstoken").with_suffix(".json")

        with open(filePath, "r", encoding="utf-8") as file:

            return json.load(file)["token"]
    # end loadToken()

    def getStateOfActiveVehicles(self) -> list[dict]:
        url = "https://api.tessie.com/vehicles"
        queryParams = {"only_active": "true"}

        response = request("GET", url, params=queryParams, headers=self.headers)
        response.raise_for_status()
        vehicles = response.json()["results"]

        return vehicles
    # end getStateOfActiveVehicles()

    def getStatus(self, vin: str) -> str:
        url = f"https://api.tessie.com/{vin}/status"

        response = request("GET", url, headers=self.headers)

        if response.status_code == 200:
            return response.json()["status"]
        else:
            logging.error(response.text)
            return "unknown"
    # end getStatus(str)

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
                         f" from {dtls.chargeLimit}"
                         f" to {percent}")
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
            return True
        else:
            logging.error(response.text)
            return False
    # end startCharging(CarDetails)

    def enableCarCharging(self, dtls: CarDetails) -> None:
        """Raise the charge limit to mean if minimum,
           then start charging if plugged in and not charging"""

        if dtls.chargeLimit == dtls.limitMinPercent:
            limitStdPercent = dtls.chargeState["charge_limit_soc_std"]
            geometricMeanLimitPercent = isqrt(dtls.limitMinPercent * limitStdPercent)

            self.setChargeLimit(dtls, geometricMeanLimitPercent)

        if dtls.chargingState != "Disconnected" and dtls.chargingState != "Charging":
            self.startCharging(dtls)
    # end enableCarCharging(CarDetails)

    def disableCarCharging(self, dtls: CarDetails) -> None:
        """Lower the charge limit to minimum if plugged in and not minimum already"""

        if dtls.chargingState != "Disconnected" and dtls.chargeLimit > dtls.limitMinPercent:
            # this vehicle is plugged in and not set to charge limit minimum already

            self.setChargeLimit(dtls, dtls.limitMinPercent)
    # end disableCarCharging(CarDetails)

    def main(self) -> None:
        vehicles = self.getStateOfActiveVehicles()
        callTime = time()

        for vehicle in vehicles:
            carDetails = CarDetails(vehicle["last_state"], callTime)

            # log the current charging state
            logging.info(f"{carDetails.displayName} is {self.getStatus(carDetails.vin)}"
                         f" with charge limit {carDetails.chargeLimit}"
                         f"; charging state is {carDetails.chargingState}"
                         f" {carDetails.sinceLastSeen} ago")

            if self.enable:
                self.enableCarCharging(carDetails)
            else:
                self.disableCarCharging(carDetails)
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
        dictConfig(json.load(file))
# end configLogging()


if __name__ == "__main__":
    clArgs = parseArgs()
    configLogging()
    try:
        chrgCtl = ChargeControl(clArgs)
        chrgCtl.main()
    except Exception as xcpt:
        logging.error(xcpt)
        logging.debug("Exception suppressed:", exc_info=xcpt)