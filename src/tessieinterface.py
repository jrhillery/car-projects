
import json
import logging
from datetime import timedelta
from pathlib import Path
from threading import current_thread

from requests import HTTPError, request, Response
from time import sleep, time


class CarDetails(object):
    """Details of a vehicle as reported by Tessie"""

    def __init__(self, sleepStatus: str, vehicleState: dict):
        self.sleepStatus = sleepStatus
        self.vin: str = vehicleState["vin"]
        self.chargeState: dict = vehicleState["charge_state"]
        self.displayName: str = vehicleState["display_name"]
        self.lastSeen = self.chargeState["timestamp"] * 0.001  # convert ms to seconds
        self.chargingState: str = self.chargeState["charging_state"]
        self.chargeLimit: int = self.chargeState["charge_limit_soc"]
        self.limitMinPercent: int = self.chargeState["charge_limit_soc_min"]
        self.batteryLevel: int = self.chargeState["usable_battery_level"]
    # end __init__(str, dict)

    def updateFromDict(self, sleepStatus: str, vehicleState: dict) -> None:
        self.sleepStatus = sleepStatus
        self.vin = vehicleState["vin"]
        self.chargeState = vehicleState["charge_state"]
        self.displayName = vehicleState["display_name"]
        self.lastSeen = self.chargeState["timestamp"] * 0.001  # convert ms to seconds
        self.chargingState = self.chargeState["charging_state"]
        self.chargeLimit = self.chargeState["charge_limit_soc"]
        self.limitMinPercent = self.chargeState["charge_limit_soc_min"]
        self.batteryLevel = self.chargeState["usable_battery_level"]
    # end updateFromDict(str, dict)

    def pluggedIn(self) -> bool:
        return self.chargingState != "Disconnected"
    # end pluggedIn()

    def currentChargingStatus(self) -> str:
        return f"{self.displayName} was {self.sleepStatus}" \
               f" {timedelta(seconds=int(time() - self.lastSeen + 0.5))} ago" \
               f" with charging {self.chargingState}" \
               f", charge limit {self.chargeLimit}%" \
               f" and battery {self.batteryLevel}%"
    # end currentChargingStatus()

    def __str__(self) -> str:
        return f"{self.displayName}@{self.batteryLevel}%"
    # end __str__()

# end class CarDetails


class TessieResponse(Response):
    """Extend Response object"""

    def __init__(self, orig: Response):
        super().__init__()
        # noinspection PyProtectedMember
        self._content = orig._content
        self.status_code = orig.status_code
        self.headers = orig.headers
        self.raw = orig.raw
        self.url = orig.url
        self.encoding = orig.encoding
        self.history = orig.history
        self.reason = orig.reason
        self.cookies = orig.cookies
        self.elapsed = orig.elapsed
        self.request = orig.request
    # end __init__(Response)

    def unknownSummary(self) -> str:
        return (f"{self.status_code} {self.decodeReason()} in {current_thread().name}:"
                f" {self.text} for url {self.url}")
    # end unknownSummary()

    def errorSummary(self) -> str:
        return (f"{self.status_code} {self.decodeReason()}:"
                f" {self.json()['error']} for url {self.url}")
    # end errorSummary()

    def decodeReason(self) -> str:
        reason = TessieResponse.decodeText(self.reason)

        if not reason:
            reason = "Error"

        return reason
    # end decodeReason()

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

# end class TessieResponse


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
        queryParams = {"use_cache": "false"}
        retries = 10

        while retries:
            response = TessieResponse(
                request("GET", url, params=queryParams, headers=self.headers))

            if response.status_code == 200:
                try:
                    carState: dict = response.json()

                    if carState["state"] == "asleep":
                        logging.info(f"{dtls.displayName} didn't wake up")
                    else:
                        dtls.updateFromDict(self.getStatus(dtls.vin), carState)

                        return
                except Exception as e:
                    raise CcException.fromError(response) from e
            elif response.status_code in {408, 500}:
                # Request Timeout or Internal Server Error
                logging.info(f"{dtls.displayName} encountered {response.errorSummary()}")
            else:
                raise CcException.fromError(response)
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

        logging.error(CcException.fromError(response))

        return "unknown"
    # end getStatus(CarDetails)

    def wake(self, dtls: CarDetails) -> None:
        """Wake the vehicle from sleep.
        Logs a message indicating if woke up, or timed out (30s)."""
        url = f"https://api.tessie.com/{dtls.vin}/wake"

        response = TessieResponse(request("GET", url, headers=self.headers))

        if response.status_code != 200:
            raise CcException.fromError(response)

        if response.json()["result"]:
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

        if resp.status_code != 200:
            raise CcException.fromError(resp)

        logging.info(f"{dtls.displayName} charging started")
        dtls.chargingState = "Charging"
    # end startCharging(CarDetails)

# end class TessieInterface
