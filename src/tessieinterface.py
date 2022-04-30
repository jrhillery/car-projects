
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

    def getState(self, dtls: CarDetails) -> CarDetails:
        url = f"https://api.tessie.com/{dtls.vin}/state"
        queryParams = {"use_cache": "false"}

        response = request("GET", url, params=queryParams, headers=self.headers)

        if response.status_code != 200:
            raise CcException.fromError(response)

        try:
            return CarDetails(response.json())
        except Exception as e:
            raise CcException.fromError(response) from e
    # end getState(CarDetails)

    def getStatus(self, dtls: CarDetails) -> str:
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

# end class TessieInterface
