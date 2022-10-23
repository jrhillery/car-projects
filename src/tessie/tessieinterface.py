
import json
import logging

from requests import request
from time import sleep

from util import Configure, HTTPException, Interpret
from . import CarDetails


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

    def getStateOfActiveVehicles(self) -> list[CarDetails]:
        """Get all active vehicles and their latest state.
        This call always returns a complete set of data and doesn't impact vehicle sleep.
        If the vehicle is awake, the data is usually less than 10 seconds old.
        If the vehicle is asleep, the data is from the time the vehicle went to sleep."""
        url = "https://api.tessie.com/vehicles"
        qryParms = {"only_active": "true"}

        resp = request("GET", url, params=qryParms, headers=self.headers)

        if resp.status_code != 200:
            raise HTTPException.fromError(resp)

        try:
            allResults: list[dict] = resp.json()["results"]

            return [self.addMoreDetails(CarDetails(car["last_state"])) for car in allResults]
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException.fromXcp(e, resp) from e
    # end getStateOfActiveVehicles()

    def getCurrentState(self, dtls: CarDetails) -> None:
        """Get the latest state of the vehicle.
        This call retrieves data using a live connection, which may return
        {"state": "asleep"} or network errors depending on vehicle connectivity."""
        url = f"https://api.tessie.com/{dtls.vin}/state"
        qryParms = {"use_cache": "false"}
        retries = 10

        while retries:
            resp = request("GET", url, params=qryParms, headers=self.headers)

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
                    raise HTTPException.fromXcp(e, resp) from e
            elif resp.status_code in {408, 500}:
                # Request Timeout or Internal Server Error
                logging.info(f"{dtls.displayName} encountered {resp.status_code}"
                             f" {Interpret.decodeReason(resp)}: {resp.json()['error']}"
                             f" for url {resp.url}")
            else:
                raise HTTPException.fromError(resp)
            sleep(60)
            retries -= 1
        # end while
    # end getCurrentState(CarDetails)

    def addMoreDetails(self, dtls: CarDetails) -> CarDetails:
        """Augment details of a specified vehicle with the status of the vehicle.
           The status may be asleep, waiting_for_sleep or awake.

        :param dtls: Details of the vehicle to augment
        :return: The updated vehicle details
        """
        url = f"https://api.tessie.com/{dtls.vin}/status"

        resp = request("GET", url, headers=self.headers)

        if resp.status_code == 200:
            try:
                dtls.sleepStatus = resp.json()["status"]
            except Exception as e:
                logging.error("Status retrieval problem:", exc_info=e)
                dtls.sleepStatus = "unknowable"
        else:
            logging.error(f"Encountered {Interpret.responseErr(resp)}")
            dtls.sleepStatus = "unknown"

        return dtls
    # end addMoreDetails(CarDetails)

    def addBatteryHealth(self, dtls: CarDetails) -> CarDetails:
        """Augment details of a specified vehicle with battery health information.

        :param dtls: Details of the vehicle to augment
        :return: The updated vehicle details
        """
        url = f"https://api.tessie.com/{dtls.vin}/battery_health"
        qryParms = {"distance_format": "mi"}

        resp = request("GET", url, params=qryParms, headers=self.headers)

        if resp.status_code == 200:
            try:
                result = resp.json()["result"]
                dtls.battMaxRange = result["max_range"]
                dtls.battCapacity = result["capacity"]
            except Exception as e:
                raise HTTPException.fromXcp(e, resp) from e
        else:
            raise HTTPException.fromError(resp)

        return dtls
    # end addBatteryHealth(CarDetails)

    def wake(self, dtls: CarDetails) -> None:
        """Attempt to wake the vehicle from sleep.
        Logs a message indicating if woke up, or timed out (30s)."""
        url = f"https://api.tessie.com/{dtls.vin}/wake"

        resp = request("GET", url, headers=self.headers)

        if resp.status_code != 200:
            raise HTTPException.fromError(resp)

        try:
            wakeOkay: bool = resp.json()["result"]
        except Exception as e:
            raise HTTPException.fromXcp(e, resp) from e

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
        qryParms = {
            "retry_duration": 60,
            "wait_for_completion": "true" if waitForCompletion else "false",
            "percent": percent
        }

        resp = request("GET", url, params=qryParms, headers=self.headers)
        oldLimit = dtls.chargeLimit
        dtls.chargeLimit = percent

        if resp.status_code != 200:
            raise HTTPException.fromError(resp)

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

        resp = request("GET", url, params=qryParms, headers=self.headers)
        dtls.chargingState = "Charging"

        if resp.status_code != 200:
            raise HTTPException.fromError(resp)

        logging.info(f"{dtls.displayName} charging started")
    # end startCharging(CarDetails)

# end class TessieInterface
