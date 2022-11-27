
import asyncio
import json
import logging

from aiohttp import ClientSession

from util import Configure, HTTPException, Interpret
from . import CarDetails


class TessieInterface(object):
    """Provides an interface through Tessie to authorized vehicles"""

    def __init__(self):
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {TessieInterface.loadToken()}"
        }
        self.session = ClientSession(headers=headers)
    # end __init__()

    @staticmethod
    def loadToken() -> str:
        filePath = Configure.findParmPath().joinpath("accesstoken.json")

        with open(filePath, "r", encoding="utf-8") as tokenFile:

            return json.load(tokenFile)["token"]
    # end loadToken()

    async def getStateOfActiveVehicles(self) -> list[CarDetails]:
        """Get all active vehicles and their latest state.
        This call always returns a complete set of data and doesn't impact vehicle sleep.
        If the vehicle is awake, the data is usually less than 10 seconds old.
        If the vehicle is asleep, the data is from the time the vehicle went to sleep."""
        url = "https://api.tessie.com/vehicles"
        qryParms = {"only_active": "true"}

        async with self.session.get(url, params=qryParms) as resp:
            if resp.status != 200:
                raise HTTPException.fromError(resp, "all active vehicles")

            try:
                allResults: list[dict] = (await resp.json())["results"]
                vehicles = [CarDetails(car["last_state"]) for car in allResults]

                async with asyncio.TaskGroup() as tg:
                    for car in vehicles:
                        tg.create_task(self.addSleepStatus(car))
                        tg.create_task(self.addLocation(car))
                # end async with (tasks are awaited)

                return vehicles
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException.fromXcp(e, resp, "all active vehicles") from e
    # end getStateOfActiveVehicles()

    async def getCurrentState(self, dtls: CarDetails, attempts: int = 10) -> None:
        """Get the latest state of the vehicle.
        This call retrieves data using a live connection, which may return
        {"state": "asleep"} or network errors depending on vehicle connectivity."""
        url = f"https://api.tessie.com/{dtls.vin}/state"
        qryParms = {"use_cache": "false"}

        while attempts:
            async with self.session.get(url, params=qryParms) as resp:
                if resp.status == 200:
                    try:
                        carState: dict = await resp.json()

                        if carState["state"] == "asleep":
                            logging.info(f"{dtls.displayName} didn't wake up")
                        else:
                            dtls.updateFromDict(carState)
                            async with asyncio.TaskGroup() as tg:
                                tg.create_task(self.addSleepStatus(dtls))
                                tg.create_task(self.addLocation(dtls))
                            # end async with (tasks are awaited)
                            logging.info(dtls.currentChargingStatus())

                            return
                    except HTTPException:
                        raise
                    except Exception as e:
                        raise HTTPException.fromXcp(e, resp, dtls.displayName) from e
                elif resp.status in {408, 500}:
                    # Request Timeout or Internal Server Error
                    logging.info(f"{dtls.displayName} encountered {resp.status}"
                                 f" {Interpret.decodeReason(resp)}:"
                                 f" {(await resp.json())['error']}"
                                 f" for url {resp.url}")
                else:
                    raise HTTPException.fromError(resp, dtls.displayName)
            if attempts := attempts - 1:
                await asyncio.sleep(60)
        # end while
    # end getCurrentState(CarDetails, int)

    async def addSleepStatus(self, dtls: CarDetails) -> CarDetails:
        """Augment details of a specified vehicle with its status.
           The status may be asleep, waiting_for_sleep or awake.

        :param dtls: Details of the vehicle to augment
        :return: The updated vehicle details
        """
        url = f"https://api.tessie.com/{dtls.vin}/status"

        async with self.session.get(url) as resp:
            if resp.status == 200:
                try:
                    dtls.sleepStatus = (await resp.json())["status"]
                except Exception as e:
                    logging.error(f"Status retrieval problem:"
                                  f" {Interpret.responseXcp(resp, e, dtls.displayName)}",
                                  exc_info=e)
                    dtls.sleepStatus = "unknowable"
            else:
                logging.error(f"Encountered {Interpret.responseErr(resp, dtls.displayName)}")
                dtls.sleepStatus = "unknown"

        return dtls
    # end addSleepStatus(CarDetails)

    async def addLocation(self, dtls: CarDetails) -> CarDetails:
        """Augment details of a specified vehicle with its location.

        :param dtls: Details of the vehicle to augment
        :return: The updated vehicle details
        """
        url = f"https://api.tessie.com/{dtls.vin}/location"

        async with self.session.get(url) as resp:
            if resp.status == 200:
                try:
                    dtls.savedLocation = (await resp.json())["saved_location"]
                except Exception as e:
                    logging.error(f"Location retrieval problem:"
                                  f" {Interpret.responseXcp(resp, e, dtls.displayName)}",
                                  exc_info=e)
                    dtls.savedLocation = None
            else:
                logging.error(f"Encountered {Interpret.responseErr(resp, dtls.displayName)}")
                dtls.savedLocation = None

        return dtls
    # end addLocation(CarDetails)

    async def addBatteryHealth(self, dtls: CarDetails) -> CarDetails:
        """Augment details of a specified vehicle with battery health information.

        :param dtls: Details of the vehicle to augment
        :return: The updated vehicle details
        """
        url = f"https://api.tessie.com/{dtls.vin}/battery_health"
        qryParms = {"distance_format": "mi"}

        async with self.session.get(url, params=qryParms) as resp:
            if resp.status == 200:
                try:
                    result = (await resp.json())["result"]
                    dtls.battMaxRange = result["max_range"]
                    dtls.battCapacity = result["capacity"]
                except Exception as e:
                    raise HTTPException.fromXcp(e, resp, dtls.displayName) from e
            else:
                raise HTTPException.fromError(resp, dtls.displayName)

        return dtls
    # end addBatteryHealth(CarDetails)

    async def wake(self, dtls: CarDetails) -> None:
        """Attempt to wake the vehicle from sleep.
        Logs a message indicating if woke up, or timed out (30s)."""
        url = f"https://api.tessie.com/{dtls.vin}/wake"

        async with self.session.get(url) as resp:
            if resp.status != 200:
                raise HTTPException.fromError(resp, dtls.displayName)

            try:
                wakeOkay: bool = (await resp.json())["result"]
            except Exception as e:
                raise HTTPException.fromXcp(e, resp, dtls.displayName) from e

        if wakeOkay:
            # wait for this vehicle's sleep status to show awake
            retries = 10

            while retries:
                await self.getCurrentState(dtls, attempts=1)

                if dtls.awake():
                    return
                await asyncio.sleep(4)
                retries -= 1
            # end while
            logging.info(f"{dtls.displayName} never woke up")
        else:
            logging.info(f"{dtls.displayName} timed out while waking up")
    # end wake(CarDetails)

    async def setChargeLimit(self, dtls: CarDetails, percent: int, *,
                             waitForCompletion=True) -> None:
        """Set the charge limit."""
        url = f"https://api.tessie.com/{dtls.vin}/command/set_charge_limit"
        qryParms = {
            "retry_duration": 60,
            "wait_for_completion": "true" if waitForCompletion else "false",
            "percent": percent
        }

        async with self.session.get(url, params=qryParms) as resp:
            oldLimit = dtls.chargeLimit
            dtls.chargeLimit = percent

            if resp.status != 200:
                raise HTTPException.fromError(resp, dtls.displayName)

        logging.info(f"{dtls.displayName} charge limit changed"
                     f" from {oldLimit}% to {percent}%")
    # end setChargeLimit(CarDetails, int, *, bool)

    async def startCharging(self, dtls: CarDetails) -> None:
        """Start charging."""
        url = f"https://api.tessie.com/{dtls.vin}/command/start_charging"
        qryParms = {
            "retry_duration": 60,
            "wait_for_completion": "true"
        }

        async with self.session.get(url, params=qryParms) as resp:
            dtls.chargingState = "Charging"

            if resp.status != 200:
                raise HTTPException.fromError(resp, dtls.displayName)

        logging.info(f"{dtls.displayName} charging started")
    # end startCharging(CarDetails)

    async def stopCharging(self, dtls: CarDetails) -> None:
        """Stop charging."""
        url = f"https://api.tessie.com/{dtls.vin}/command/stop_charging"
        qryParms = {
            "retry_duration": 60,
            "wait_for_completion": "true"
        }

        async with self.session.get(url, params=qryParms) as resp:
            dtls.chargingState = "Stopping"

            if resp.status != 200:
                raise HTTPException.fromError(resp, dtls.displayName)

        logging.info(f"{dtls.displayName} charging stopped")
    # end stopCharging(CarDetails)

    async def aclose(self) -> None:
        """Close this instance and free up resources"""
        await self.session.close()
    # end aclose()

# end class TessieInterface
