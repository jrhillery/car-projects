
import json
import logging

from requests import HTTPError, request
from time import sleep

from util.configure import Configure
from util.extresponse import ExtResponse
from .cardetails import CarDetails


class CcException(HTTPError):
    """Detected exceptions"""

    @classmethod
    def fromError(cls, badResponse: ExtResponse):
        """Factory method for bad responses"""

        return cls(badResponse.unknownSummary(), response=badResponse)
    # end fromError(ExtResponse)

    @classmethod
    def fromXcp(cls, unableMsg: str, xcption: BaseException, badResponse: ExtResponse):
        """Factory method for Exceptions"""
        message = f"Unable to {unableMsg}, {xcption.__class__.__name__}: {str(xcption)}"

        return cls(message, response=badResponse)
    # end fromXcp(str, BaseException, ExtResponse)

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
    def loadToken() -> str:
        filePath = Configure.findParmPath().joinpath("accesstoken.json")

        with open(filePath, "r", encoding="utf-8") as tokenFile:

            return json.load(tokenFile)["token"]
    # end loadToken()

    def getStateOfActiveVehicles(self, withBatteryHealth: bool = False) -> list[CarDetails]:
        """Get all active vehicles and their latest state.
        This call always returns a complete set of data and doesn't impact vehicle sleep.
        If the vehicle is awake, the data is usually less than 10 seconds old.
        If the vehicle is asleep, the data is from the time the vehicle went to sleep."""
        url = "https://api.tessie.com/vehicles"
        queryParams = {"only_active": "true"}

        resp = ExtResponse(request("GET", url, params=queryParams, headers=self.headers))

        if resp.status_code != 200:
            raise CcException.fromError(resp)

        try:
            allResults: list[dict] = resp.json()["results"]

            return [self.addMoreDetails(CarDetails(car["last_state"]), withBatteryHealth)
                    for car in allResults]
        except CcException:
            raise
        except Exception as e:
            raise CcException.fromXcp("interpret get vehicles response", e, resp) from e
    # end getStateOfActiveVehicles()

    def getCurrentState(self, dtls: CarDetails) -> None:
        """Get the latest state of the vehicle.
        This call retrieves data using a live connection, which may return
        {"state": "asleep"} or network errors depending on vehicle connectivity."""
        url = f"https://api.tessie.com/{dtls.vin}/state"
        qryParms = {"use_cache": "false"}
        retries = 10

        while retries:
            resp = ExtResponse(request("GET", url, params=qryParms, headers=self.headers))

            if resp.status_code == 200:
                try:
                    carState: dict = resp.json()

                    if carState["state"] == "asleep":
                        logging.info(f"{dtls.displayName} didn't wake up")
                    else:
                        dtls.updateFromDict(carState)
                        self.addMoreDetails(dtls, withBatteryHealth=False)
                        logging.info(dtls.currentChargingStatus())

                        return
                except CcException:
                    raise
                except Exception as e:
                    raise CcException.fromXcp("interpret get state repsonse", e, resp) from e
            elif resp.status_code in {408, 500}:
                # Request Timeout or Internal Server Error
                logging.info(f"{dtls.displayName} encountered {resp.status_code}"
                             f" {resp.decodeReason()}: {resp.json()['error']}"
                             f" for url {resp.url}")
            else:
                raise CcException.fromError(resp)
            sleep(60)
            retries -= 1
        # end while
    # end getCurrentState(CarDetails)

    def addMoreDetails(self, dtls: CarDetails, withBatteryHealth: bool) -> CarDetails:
        """Get the status of the vehicle.
        The status may be asleep, waiting_for_sleep or awake."""
        url = f"https://api.tessie.com/{dtls.vin}/status"

        response = ExtResponse(request("GET", url, headers=self.headers))

        if response.status_code == 200:
            try:
                dtls.sleepStatus = response.json()["status"]
            except Exception as e:
                logging.error("Status retrieval problem:", exc_info=e)
                dtls.sleepStatus = "unknowable"
        else:
            logging.error(f"Encountered {response.unknownSummary()}")
            dtls.sleepStatus = "unknown"

        if withBatteryHealth:
            url = f"https://api.tessie.com/{dtls.vin}/battery_health"

            response = ExtResponse(request("GET", url, headers=self.headers))

            if response.status_code == 200:
                try:
                    dtls.batteryCapacity = response.json()["result"]["capacity"]
                except Exception as e:
                    raise CcException.fromXcp("interpret battery health response",
                                              e, response) from e
            else:
                raise CcException.fromError(response)
        # end if

        return dtls
    # end addMoreDetails(CarDetails, bool)

    def wake(self, dtls: CarDetails) -> None:
        """Attempt to wake the vehicle from sleep.
        Logs a message indicating if woke up, or timed out (30s)."""
        url = f"https://api.tessie.com/{dtls.vin}/wake"

        resp = ExtResponse(request("GET", url, headers=self.headers))

        if resp.status_code != 200:
            raise CcException.fromError(resp)

        try:
            wakeOkay: bool = resp.json()["result"]
        except Exception as e:
            raise CcException.fromXcp("interpret wake response", e, resp) from e

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

        resp = ExtResponse(request("GET", url, params=queryParams, headers=self.headers))
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

        resp = ExtResponse(request("GET", url, params=queryParams, headers=self.headers))
        dtls.chargingState = "Charging"

        if resp.status_code != 200:
            raise CcException.fromError(resp)

        logging.info(f"{dtls.displayName} charging started")
    # end startCharging(CarDetails)

# end class TessieInterface
