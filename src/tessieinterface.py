
import json
import logging
from pathlib import Path
from threading import current_thread

from requests import HTTPError, request, Response


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

    def updateFromDict(self, vehicleState: dict) -> None:
        self.vin = vehicleState["vin"]
        self.chargeState = vehicleState["charge_state"]
        self.displayName = vehicleState["display_name"]
        self.lastSeen = self.chargeState["timestamp"] * 0.001  # convert ms to seconds
        self.chargingState = self.chargeState["charging_state"]
        self.chargeLimit = self.chargeState["charge_limit_soc"]
        self.limitMinPercent = self.chargeState["charge_limit_soc_min"]
        self.batteryLevel = self.chargeState["usable_battery_level"]
    # end updateFromDict(dict)

    def pluggedIn(self) -> bool:
        return self.chargingState != "Disconnected"
    # end pluggedIn()

    def __str__(self) -> str:
        return f"{self.displayName}@{self.batteryLevel}%"
    # end __str__()

# end class CarDetails


class CcException(HTTPError):
    """Detected exceptions"""

    @classmethod
    def fromError(cls, badResponse: Response):
        """Factory method for bad responses"""
        prefix = CcException.decodeText(badResponse.reason)

        if not prefix:
            prefix = "Error"

        return cls(f"{badResponse.status_code} {prefix} in {current_thread().name}"
                   f" {badResponse.text} for url {badResponse.url}",
                   response=badResponse)
    # end fromError(Response)

    @staticmethod
    def decodeText(text: bytes | str) -> str:
        if isinstance(text, bytes):
            # Some servers choose to localize their reason strings.
            try:
                string = text.decode('utf-8')
            except UnicodeDecodeError:
                string = text.decode('iso-8859-1')
        else:
            string = text

        return string
    # end decodeText(bytes | str)

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
        """Get all vehicles and their latest state.
        This call always returns a complete set of data and doesn't impact vehicle sleep.
        If the vehicle is awake, the data is usually less than 10 seconds old.
        If the vehicle is asleep, the data is from the time the vehicle went to sleep."""
        url = "https://api.tessie.com/vehicles"
        queryParams = {"only_active": "true"}

        response = request("GET", url, params=queryParams, headers=self.headers)

        if response.status_code != 200:
            raise CcException.fromError(response)

        try:
            allResults: list[dict] = response.json()["results"]

            return [CarDetails(car["last_state"]) for car in allResults]
        except Exception as e:
            raise CcException.fromError(response) from e
    # end getStateOfActiveVehicles()

    def getCurrentState(self, dtls: CarDetails) -> None:
        """Get the latest state of the vehicle.
        This call retrieves data using a live connection, which may return
        {"state": "asleep"} or network errors depending on vehicle connectivity."""
        url = f"https://api.tessie.com/{dtls.vin}/state"
        queryParams = {"use_cache": "false"}

        response = request("GET", url, params=queryParams, headers=self.headers)

        if response.status_code == 200:
            try:
                carState = response.json()

                if carState["state"] == "asleep":
                    logging.info(f"{dtls.displayName} didn't wake up")
                else:
                    dtls.updateFromDict(carState)
            except Exception as e:
                raise CcException.fromError(response) from e
        elif response.status_code == 500:
            # Internal Server Error
            logging.info(f"{dtls.displayName} encountered 500"
                         f" {CcException.decodeText(response.reason)}"
                         f" {response.json()['error']} for url {response.url}")
        else:
            raise CcException.fromError(response)
    # end getCurrentState(CarDetails)

    def getStatus(self, dtls: CarDetails) -> str:
        """Get the status of the vehicle.
        The status may be asleep, waiting_for_sleep or awake."""
        url = f"https://api.tessie.com/{dtls.vin}/status"

        response = request("GET", url, headers=self.headers)

        if response.status_code == 200:
            try:
                return response.json()["status"]
            except Exception as e:
                logging.error(e)

        logging.error(CcException.fromError(response))

        return "unknown"
    # end getStatus(CarDetails)

    def setChargeLimit(self, dtls: CarDetails, percent: int) -> None:
        """Set the charge limit."""
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
        """Start charging."""
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

# end class TessieInterface
