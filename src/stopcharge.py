
import json
import logging
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
        vehicles = response.json()["results"]

        return vehicles
    # end getStateOfActiveVehicles()

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
        chargeState: dict = vehicleState["charge_state"]
        displayName: str = vehicleState["display_name"]
        chargingState: str = chargeState["charging_state"]
        chargeLimit: int = chargeState["charge_limit_soc"]
        limitMinPercent: int = chargeState["charge_limit_soc_min"]
        lastSeen: float = chargeState["timestamp"] * 0.001

        # log the current charging state
        timeAgo = timedelta(seconds=int(callTime - lastSeen + 0.5))
        logging.info(f"{displayName} charging state is {chargingState} {timeAgo} ago")

        if chargingState != "Disconnected" and chargeLimit > limitMinPercent:
            # this vehicle is plugged in and not set to charge limit minimum already

            if self.setChargeLimit(vehicleState["vin"], limitMinPercent):
                logging.info(f"{displayName} charge limit changed "
                             f"from {chargeLimit} "
                             f"to {limitMinPercent}")
    # end stopChargingCar(dict, float)

    def main(self) -> None:
        vehicles = self.getStateOfActiveVehicles()
        callTime = time()

        for vehicle in vehicles:
            self.stopChargingCar(vehicle["last_state"], callTime)
    # end main()

# end class StopCharge


def configLogging() -> None:
    # format times like: Tue Feb 08 18:25:02
    DATE_FMT_DAY_SECOND = "%a %b %d %H:%M:%S"

    dictConfig({
        "version": 1,
        "formatters": {
            "detail": {
                "format": "%(levelname)s %(asctime)s.%(msecs)03d %(module)s: %(message)s",
                "datefmt": DATE_FMT_DAY_SECOND
            },
            "simple": {
                "format": "%(asctime)s.%(msecs)03d: %(message)s",
                "datefmt": DATE_FMT_DAY_SECOND
            }
        },
        "handlers": {
            "console": {
                "class": "logging.StreamHandler",
                "level": "INFO",
                "formatter": "simple",
                "stream": "ext://sys.stdout"
            },
            "file": {
                "class": "logging.handlers.RotatingFileHandler",
                "level": "DEBUG",
                "formatter": "detail",
                "filename": "StopCharge.log",
                "maxBytes": 30000,
                "backupCount": 1,
                "encoding": "utf-8"
            }
        },
        "root": {
            "level": "DEBUG",
            "handlers": ["console", "file"]
        }
    })
# end configLogging()


if __name__ == "__main__":
    configLogging()
    try:
        stopChrg = StopCharge()
        stopChrg.main()
    except Exception as xcpt:
        logging.error(xcpt)
        logging.debug("Exception suppressed:", exc_info=xcpt)
