
import json
import logging
from logging.config import dictConfig
from pathlib import Path

from requests import request


class StopCharge(object):
    """Stops vehicles from charging if plugged in"""
    LIMIT_PERCENT = 50

    def __init__(self):
        self.parmPath = StopCharge.findParmPath()
        self.token = self.loadToken()
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}"
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

    def parmFile(self, fileNm: Path) -> Path:
        if fileNm.exists():
            return fileNm
        else:
            pf = Path(self.parmPath, fileNm)

            return pf.with_suffix(".json")
    # end parmFile(Path)

    def loadToken(self) -> str:
        fileNm = self.parmFile(Path("accesstoken"))

        with open(fileNm, "r", encoding="utf-8") as file:

            return json.load(file)["token"]
    # end loadToken()

    def getStateOfActiveVehicles(self):
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

    def main(self):
        vehicles = self.getStateOfActiveVehicles()

        for vehicle in vehicles:
            lastState = vehicle["last_state"]
            displayName = lastState["display_name"]
            chargeState = lastState["charge_state"]
            chargingState = chargeState["charging_state"]
            logging.info(f"{displayName} is {chargingState}")

            if chargingState != "Disconnected":
                if self.setChargeLimit(lastState["vin"], StopCharge.LIMIT_PERCENT):
                    logging.info(f"{displayName} charge limit changed "
                                 f"from {chargeState['charge_limit_soc']} "
                                 f"to {StopCharge.LIMIT_PERCENT}")
    # end main()

# end class StopCharge


def configLogging():
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
