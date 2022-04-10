
import json
import logging
import sys
from datetime import timedelta
from logging.config import dictConfig
from pathlib import Path

from requests import request
from time import time


class StopCharge(object):
    """Stops vehicles from charging if plugged in"""
    def __init__(self):
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.loadToken()}"
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
    def parmFile(fileNm: Path) -> Path:
        if fileNm.exists():
            return fileNm
        else:
            pf = Path(StopCharge.findParmPath(), fileNm)

            return pf.with_suffix(".json")
    # end parmFile(Path)

    def loadToken(self) -> str:
        fileNm = self.parmFile(Path("accesstoken"))

        with open(fileNm, "r", encoding="utf-8") as file:

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

    def setChargeLimit(self, vin: str, percent: int) -> bool:
        url = f"https://api.tessie.com/{vin}/command/set_charge_limit"
        queryParams = {
            "retry_duration": 40,
            "wait_for_completion": "true",
            "percent": percent
        }

        response = request("GET", url, params=queryParams, headers=self.headers)

        if response.status_code == 200:
            return True
        else:
            logging.error(response.text)
            return False
    # end setChargeLimit(str, int)

    def stopChargingCar(self, vehicleState: dict, callTime: float):
        """Lower the charge limit to minimum if plugged in and not minimum already"""
        vin: str = vehicleState["vin"]
        chargeState: dict = vehicleState["charge_state"]
        displayName: str = vehicleState["display_name"]
        chargingState: str = chargeState["charging_state"]
        chargeLimit: int = chargeState["charge_limit_soc"]
        limitMinPercent: int = chargeState["charge_limit_soc_min"]
        lastSeen: float = chargeState["timestamp"] * 0.001  # convert ms to seconds

        # log the current charging state
        timeAgo = timedelta(seconds=int(callTime - lastSeen + 0.5))
        logging.info(f"{displayName} is {self.getStatus(vin)}"
                     f" with charge limit {chargeLimit}"
                     f"; charging state is {chargingState} {timeAgo} ago")

        if chargingState != "Disconnected" and chargeLimit > limitMinPercent:
            # this vehicle is plugged in and not set to charge limit minimum already

            if self.setChargeLimit(vin, limitMinPercent):
                logging.info(f"{displayName} charge limit changed"
                             f" from {chargeLimit}"
                             f" to {limitMinPercent}")
    # end stopChargingCar(dict, float)

    def main(self) -> None:
        vehicles = self.getStateOfActiveVehicles()
        callTime = time()

        for vehicle in vehicles:
            self.stopChargingCar(vehicle["last_state"], callTime)
    # end main()

# end class StopCharge


def configLogging() -> None:
    filePath = Path(sys.path[0], "StopCharge.logging.config.json")

    with open(filePath, "r", encoding="utf-8") as file:
        dictConfig(json.load(file))
# end configLogging()


if __name__ == "__main__":
    configLogging()
    try:
        stopChrg = StopCharge()
        stopChrg.main()
    except Exception as xcpt:
        logging.error(xcpt)
        logging.debug("Exception suppressed:", exc_info=xcpt)
