
import json
import logging

from aiohttp import ClientSession
from requests import request
from time import sleep

from util import Configure, HTTPException, Interpret
from . import CarDetails


class TessieInterface(object):
    """Provides an interface through Tessie to authorized vehicles"""
    session: ClientSession

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

    async def setSession(self) -> None:
        if not hasattr(self, "session"):
            self.session = ClientSession(headers=self.headers)
    # end setSession()

    def getStateOfActiveVehicles(self) -> list[CarDetails]:
        """Get all active vehicles and their latest state.
        This call always returns a complete set of data and doesn't impact vehicle sleep.
        If the vehicle is awake, the data is usually less than 10 seconds old.
        If the vehicle is asleep, the data is from the time the vehicle went to sleep."""
        url = "https://api.tessie.com/vehicles"
        qryParms = {"only_active": "true"}

        with request("GET", url, params=qryParms, headers=self.headers) as resp:
            if resp.status_code != 200:
                raise HTTPException.fromError(resp, "all active vehicles")

            try:
                allResults: list[dict] = resp.json()["results"]

                return [self.addMoreDetails(CarDetails(car["last_state"])) for car in allResults]
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException.fromXcp(e, resp, "all active vehicles") from e
    # end getStateOfActiveVehicles()

    def getCurrentState(self, dtls: CarDetails) -> None:
        """Get the latest state of the vehicle.
        This call retrieves data using a live connection, which may return
        {"state": "asleep"} or network errors depending on vehicle connectivity."""
        url = f"https://api.tessie.com/{dtls.vin}/state"
        qryParms = {"use_cache": "false"}
        retries = 10

        while retries:
            with request("GET", url, params=qryParms, headers=self.headers) as resp:
                if resp.status_code == 200:
                    try:
                        carState: dict = resp.json()

                        if carState["state"] == "asleep":
                            logging.info(f"{dtls.displayName} didn't wake up")
                        else:
                            dtls.updateFromDict(carState)
                            self.addMoreDetails(dtls)
                            logging.info(dtls.currentChargingStatus())

                            return
                    except HTTPException:
                        raise
                    except Exception as e:
                        raise HTTPException.fromXcp(e, resp, dtls.displayName) from e
                elif resp.status_code in {408, 500}:
                    # Request Timeout or Internal Server Error
                    logging.info(f"{dtls.displayName} encountered {resp.status_code}"
                                 f" {Interpret.decodeReason(resp)}:"
                                 f" {(resp.json())['error']}"
                                 f" for url {resp.url}")
                else:
                    raise HTTPException.fromError(resp, dtls.displayName)
            sleep(60)
            retries -= 1
        # end while
    # end getCurrentState(CarDetails)

    def addMoreDetails(self, dtls: CarDetails) -> CarDetails:
        """Augment details of a specified vehicle with its status and location.
           The status may be asleep, waiting_for_sleep or awake.

        :param dtls: Details of the vehicle to augment
        :return: The updated vehicle details
        """
        url = f"https://api.tessie.com/{dtls.vin}/status"

        with request("GET", url, headers=self.headers) as resp:
            if resp.status_code == 200:
                try:
                    dtls.sleepStatus = resp.json()["status"]
                except Exception as e:
                    logging.error(f"Status retrieval problem:"
                                  f" {Interpret.responseXcp(resp, e, dtls.displayName)}",
                                  exc_info=e)
                    dtls.sleepStatus = "unknowable"
            else:
                logging.error(f"Encountered {Interpret.responseErr(resp, dtls.displayName)}")
                dtls.sleepStatus = "unknown"

        url = f"https://api.tessie.com/{dtls.vin}/location"

        with request("GET", url, headers=self.headers) as resp:
            if resp.status_code == 200:
                try:
                    dtls.savedLocation = resp.json()["saved_location"]
                except Exception as e:
                    logging.error(f"Location retrieval problem:"
                                  f" {Interpret.responseXcp(resp, e, dtls.displayName)}",
                                  exc_info=e)
                    dtls.savedLocation = None
            else:
                logging.error(f"Encountered {Interpret.responseErr(resp, dtls.displayName)}")
                dtls.savedLocation = None

        return dtls
    # end addMoreDetails(CarDetails)

    def addBatteryHealth(self, dtls: CarDetails) -> CarDetails:
        """Augment details of a specified vehicle with battery health information.

        :param dtls: Details of the vehicle to augment
        :return: The updated vehicle details
        """
        url = f"https://api.tessie.com/{dtls.vin}/battery_health"
        qryParms = {"distance_format": "mi"}

        with request("GET", url, params=qryParms, headers=self.headers) as resp:
            if resp.status_code == 200:
                try:
                    result = resp.json()["result"]
                    dtls.battMaxRange = result["max_range"]
                    dtls.battCapacity = result["capacity"]
                except Exception as e:
                    raise HTTPException.fromXcp(e, resp, dtls.displayName) from e
            else:
                raise HTTPException.fromError(resp, dtls.displayName)

        return dtls
    # end addBatteryHealth(CarDetails)

    def wake(self, dtls: CarDetails) -> None:
        """Attempt to wake the vehicle from sleep.
        Logs a message indicating if woke up, or timed out (30s)."""
        url = f"https://api.tessie.com/{dtls.vin}/wake"

        with request("GET", url, headers=self.headers) as resp:
            if resp.status_code != 200:
                raise HTTPException.fromError(resp, dtls.displayName)

            try:
                wakeOkay: bool = resp.json()["result"]
            except Exception as e:
                raise HTTPException.fromXcp(e, resp, dtls.displayName) from e

        if wakeOkay:
            # wait for this vehicle's sleep status to show awake
            retries = 10

            while retries:
                self.getCurrentState(dtls)

                if dtls.awake():
                    return
                sleep(4)
                retries -= 1
            # end while
            logging.info(f"{dtls.displayName} never woke up")
        else:
            logging.info(f"{dtls.displayName} timed out while waking up")
    # end wake(CarDetails)

    def setChargeLimit(self, dtls: CarDetails, percent: int, *,
                       waitForCompletion=True) -> None:
        """Set the charge limit."""
        url = f"https://api.tessie.com/{dtls.vin}/command/set_charge_limit"
        qryParms = {
            "retry_duration": 60,
            "wait_for_completion": "true" if waitForCompletion else "false",
            "percent": percent
        }

        with request("GET", url, params=qryParms, headers=self.headers) as resp:
            oldLimit = dtls.chargeLimit
            dtls.chargeLimit = percent

            if resp.status_code != 200:
                raise HTTPException.fromError(resp, dtls.displayName)

        logging.info(f"{dtls.displayName} charge limit changed"
                     f" from {oldLimit}% to {percent}%")
    # end setChargeLimit(CarDetails, int, *, bool)

    def startCharging(self, dtls: CarDetails) -> None:
        """Start charging."""
        url = f"https://api.tessie.com/{dtls.vin}/command/start_charging"
        qryParms = {
            "retry_duration": 60,
            "wait_for_completion": "true"
        }

        with request("GET", url, params=qryParms, headers=self.headers) as resp:
            dtls.chargingState = "Charging"

            if resp.status_code != 200:
                raise HTTPException.fromError(resp, dtls.displayName)

        logging.info(f"{dtls.displayName} charging started")
    # end startCharging(CarDetails)

    async def aclose(self) -> None:
        """Close this instance and free up resources"""
        if hasattr(self, "session"):
            await self.session.close()
    # end aclose()

# end class TessieInterface
