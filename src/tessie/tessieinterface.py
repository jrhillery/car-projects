
import json
import logging
from pathlib import Path

from requests import HTTPError, request
from time import sleep

from .cardetails import CarDetails
from .tessieresponse import TessieResponse


class CcException(HTTPError):
    """Detected exceptions"""

    @classmethod
    def fromError(cls, badResponse: TessieResponse):
        """Factory method for bad responses"""

        return cls(badResponse.unknownSummary(), response=badResponse)
    # end fromError(TessieResponse)

# end class CcException


class TessieInterface(object):
    """Provides an interface through Tessie to authorized vehicles"""

    def __init__(self):
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {TessieInterface.loadToken()}"
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
        filePath = Path(TessieInterface.findParmPath(), "accesstoken.json")

        with open(filePath, "r", encoding="utf-8") as tokenFile:

            return json.load(tokenFile)["token"]
    # end loadToken()

    def getStateOfActiveVehicles(self) -> list[CarDetails]:
        """Get all active vehicles and their latest state.
        This call always returns a complete set of data and doesn't impact vehicle sleep.
        If the vehicle is awake, the data is usually less than 10 seconds old.
        If the vehicle is asleep, the data is from the time the vehicle went to sleep."""
        url = "https://api.tessie.com/vehicles"
        queryParams = {"only_active": "true"}

        resp = TessieResponse(request("GET", url, params=queryParams, headers=self.headers))

        if resp.status_code != 200:
            raise CcException.fromError(resp)

        try:
            allResults: list[dict] = resp.json()["results"]
            carStates = []

            for car in allResults:
                # car has vin: str, is_active: bool, last_state: dict
                carState: dict = car["last_state"]
                carStates.append(CarDetails(self.getStatus(carState["vin"]), carState))

            return carStates
        except Exception as e:
            raise CcException.fromError(resp) from e
    # end getStateOfActiveVehicles()

    def getCurrentState(self, dtls: CarDetails) -> None:
        """Get the latest state of the vehicle.
        This call retrieves data using a live connection, which may return
        {"state": "asleep"} or network errors depending on vehicle connectivity."""
        url = f"https://api.tessie.com/{dtls.vin}/state"
        qryParms = {"use_cache": "false"}
        retries = 10

        while retries:
            resp = TessieResponse(request("GET", url, params=qryParms, headers=self.headers))

            if resp.status_code == 200:
                try:
                    carState: dict = resp.json()

                    if carState["state"] == "asleep":
                        logging.info(f"{dtls.displayName} didn't wake up")
                    else:
                        dtls.updateFromDict(self.getStatus(dtls.vin), carState)

                        return
                except Exception as e:
                    raise CcException.fromError(resp) from e
            elif resp.status_code in {408, 500}:
                # Request Timeout or Internal Server Error
                logging.info(f"{dtls.displayName} encountered {resp.errorSummary()}")
            else:
                raise CcException.fromError(resp)
            sleep(60)
            retries -= 1
        # end while
    # end getCurrentState(CarDetails)

    def getStatus(self, vin: str) -> str:
        """Get the status of the vehicle.
        The status may be asleep, waiting_for_sleep or awake."""
        url = f"https://api.tessie.com/{vin}/status"

        response = TessieResponse(request("GET", url, headers=self.headers))

        if response.status_code == 200:
            try:
                return response.json()["status"]
            except Exception as e:
                logging.error(e)

        logging.error(f"Encountered {response.unknownSummary()}")

        return "unknown"
    # end getStatus(CarDetails)

    def wake(self, dtls: CarDetails) -> None:
        """Wake the vehicle from sleep.
        Logs a message indicating if woke up, or timed out (30s)."""
        url = f"https://api.tessie.com/{dtls.vin}/wake"

        response = TessieResponse(request("GET", url, headers=self.headers))

        if response.status_code != 200:
            raise CcException.fromError(response)

        wakeOkay: bool = response.json()["result"]

        if wakeOkay:
            logging.info(f"{dtls.displayName} woke up")
            dtls.sleepStatus = "woke"
        else:
            logging.info(f"{dtls.displayName} timed out while waking up")
    # end wake(CarDetails)

    def setChargeLimit(self, dtls: CarDetails, percent: int, *,
                       waitForCompletion=True) -> None:
        """Set the charge limit."""
        url = f"https://api.tessie.com/{dtls.vin}/command/set_charge_limit"
        queryParams = {
            "retry_duration": 60,
            "wait_for_completion": "true" if waitForCompletion else "false",
            "percent": percent
        }

        resp = TessieResponse(request("GET", url, params=queryParams, headers=self.headers))
        oldLimit = dtls.chargeLimit
        dtls.chargeLimit = percent

        if resp.status_code != 200:
            raise CcException.fromError(resp)

        logging.info(f"{dtls.displayName} charge limit changed"
                     f" from {oldLimit}% to {percent}%")
    # end setChargeLimit(CarDetails, int, *, bool)

    def startCharging(self, dtls: CarDetails) -> None:
        """Start charging."""
        url = f"https://api.tessie.com/{dtls.vin}/command/start_charging"
        queryParams = {
            "retry_duration": 60,
            "wait_for_completion": "true"
        }

        resp = TessieResponse(request("GET", url, params=queryParams, headers=self.headers))
        dtls.chargingState = "Charging"

        if resp.status_code != 200:
            raise CcException.fromError(resp)

        logging.info(f"{dtls.displayName} charging started")
    # end startCharging(CarDetails)

# end class TessieInterface
