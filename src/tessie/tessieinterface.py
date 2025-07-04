
import math

import asyncio
import haversine
import json
import logging
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from typing import Self

from aiohttp import ClientResponse, ClientSession

from util import Configure, HTTPException, Interpret
from . import CarDetails


class TessieInterface(AbstractAsyncContextManager[Self]):
    """Provides an interface through Tessie to authorized vehicles"""

    async def __aenter__(self) -> Self:
        """Allocate resources"""
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {await TessieInterface.loadToken()}"
        }
        self.session = ClientSession(headers=headers)

        return self
    # end __aenter__()

    async def __aexit__(self, exc_type, exc: BaseException | None, exc_tb) -> None:
        """Close this instance and free up resources"""
        await self.session.close()
    # end __aexit__(Type[BaseException] | None, BaseException | None, TracebackType | None)

    @staticmethod
    async def loadToken() -> str:
        filePath = Configure.findParmPath().joinpath("accesstoken.json")

        with open(filePath, "r", encoding="utf-8") as tokenFile:

            return json.load(tokenFile)["token"]
    # end loadToken()

    async def getStateOfActiveVehicles(self) -> Sequence[CarDetails]:
        """Get all active vehicles and their latest state - this call always
           returns a complete set of data and doesn't impact vehicle sleep
           - if the vehicle is awake, the data is usually less than 15 seconds old
           - if the vehicle is asleep, the data is from the time the vehicle went to sleep
        :return: A sequence with details of the active vehicles in the account
        """
        url = "https://api.tessie.com/vehicles"
        qryParms = {"only_active": "true"}

        async with self.session.get(url, params=qryParms) as resp:
            if resp.status != 200:
                raise await HTTPException.fromError(resp, "all active vehicles")

            try:
                allResults: list[dict] = (await resp.json())["results"]
                vehicles = [CarDetails(car["last_state"]) for car in allResults]

                async with asyncio.TaskGroup() as tg:
                    for car in vehicles:
                        tg.create_task(self.addBattery(car))
                        tg.create_task(self.addSleepStatus(car))
                        tg.create_task(self.addLocation(car))
                # end async with (tasks are awaited)

                return vehicles
            except HTTPException:
                raise
            except Exception as e:
                raise await HTTPException.fromXcp(e, resp, "all active vehicles") from e
    # end getStateOfActiveVehicles()

    @staticmethod
    async def respErrLog(resp: ClientResponse, dtls: CarDetails) -> str:
        """Retrieve information about a response suitable for logging
        :param resp: The response to detail
        :param dtls: Details of the associated vehicle
        :return: A summary string
        """

        return f"Encountered {await Interpret.responseErr(resp, dtls.displayName)}"
    # end respErrLog(ClientResponse, CarDetails)

    async def getCurrentState(self, dtls: CarDetails, attempts: int = 1) -> None:
        """Get the latest state of a specified vehicle - uses a live connection, which may
           return {"state": "asleep"} or network errors depending on vehicle connectivity
        :param dtls: Details of the vehicle to query
        :param attempts: Number of times to attempt query
        """
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
                                tg.create_task(self.addBattery(dtls))
                                tg.create_task(self.addSleepStatus(dtls))
                                tg.create_task(self.addLocation(dtls))
                            # end async with (tasks are awaited)
                            logging.info(dtls.chargingStatusSummary())

                            return
                    except HTTPException:
                        raise
                    except Exception as e:
                        raise await HTTPException.fromXcp(e, resp, dtls.displayName) from e
                elif resp.status in {408, 500}:
                    # Request Timeout or Internal Server Error
                    logging.info(await self.respErrLog(resp, dtls))
                else:
                    raise await HTTPException.fromError(resp, dtls.displayName)
            if attempts := attempts - 1:
                await asyncio.sleep(60)
        # end while
    # end getCurrentState(CarDetails, int)

    async def addBattery(self, dtls: CarDetails) -> CarDetails:
        """Augment details of a specified vehicle with its battery state
        :param dtls: Details of the vehicle to augment
        :return: The updated vehicle details
        """
        url = f"https://api.tessie.com/{dtls.vin}/battery"

        async with self.session.get(url) as resp:
            if resp.status == 200:
                try:
                    batteryData = await resp.json()
                    dtls.battLevel = batteryData["battery_level"]
                    dtls.energyLeft = batteryData["energy_remaining"]
                except Exception as e:
                    logging.error(f"Battery retrieval problem:"
                                  f" {await Interpret.responseXcp(resp, e, dtls.displayName)}",
                                  exc_info=e)
                    dtls.battLevel = dtls.energyLeft = 0.0
            else:
                logging.error(await self.respErrLog(resp, dtls))
                dtls.battLevel = dtls.energyLeft = 0.0

        return dtls
    # end addBattery(CarDetails)

    async def addSleepStatus(self, dtls: CarDetails) -> CarDetails:
        """Augment details of a specified vehicle with its status
           - the status may be asleep, waiting_for_sleep or awake
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
                                  f" {await Interpret.responseXcp(resp, e, dtls.displayName)}",
                                  exc_info=e)
                    dtls.sleepStatus = "unknowable"
            else:
                logging.error(await self.respErrLog(resp, dtls))
                dtls.sleepStatus = "unknown"

        return dtls
    # end addSleepStatus(CarDetails)

    @staticmethod
    def log_location(dtls: CarDetails, location: tuple[float, float]) -> None:
        """Create a debug log of a specified unknown location

        :param dtls: Details of the vehicle
        :param location: Latitude and longitude of the location in decimal degrees
        """
        home = (35.35203, -80.77707)
        dist = haversine.haversine(home, location, haversine.Unit.MILES)
        logging.debug(f"{dtls.displayName} location {location} unknown"
                      f" {dist:.2f} mi from home")
    # end log_location(CarDetails, tuple)

    async def getLastDrive(self, dtls: CarDetails) -> dict | None:
        """Get data from the last drive because the
        car's current location can go wrong while garaged

        :param dtls: Details of the vehicle to augment
        """
        url = f"https://api.tessie.com/{dtls.vin}/drives"
        qryParms = {"limit": 1}

        async with self.session.get(url, params=qryParms) as resp:
            if resp.status == 200:
                try:
                    allResults: list[dict] = (await resp.json())["results"]

                    if allResults:
                        return allResults[0]
                    else:
                        logging.error(f"Unable to get {dtls.displayName}'s last drive details")
                except Exception as e:
                    logging.error(f"Drives retrieval problem:"
                                  f" {await Interpret.responseXcp(resp, e, dtls.displayName)}",
                                  exc_info=e)
            else:
                logging.error(await self.respErrLog(resp, dtls))

        return None
    # end getLastDrive(CarDetails)

    async def addCurrentLocation(self, dtls: CarDetails) -> None:
        """Add the car's current location

        :param dtls: Details of the vehicle to augment
        """
        url = f"https://api.tessie.com/{dtls.vin}/location"

        async with self.session.get(url) as resp:
            if resp.status == 200:
                try:
                    respJson: dict = await resp.json()
                    dtls.savedLocation = respJson.get("saved_location")

                    if dtls.savedLocation is None:
                        self.log_location(dtls, (respJson["latitude"], respJson["longitude"]))
                except Exception as e:
                    logging.error(f"Location retrieval problem:"
                                  f" {await Interpret.responseXcp(resp, e, dtls.displayName)}",
                                  exc_info=e)
            else:
                logging.error(await self.respErrLog(resp, dtls))
    # end addCurrentLocation(CarDetails)

    async def addLocation(self, dtls: CarDetails) -> CarDetails:
        """Augment details of a specified vehicle with its location

        :param dtls: Details of the vehicle to augment
        :return: The updated vehicle details
        """
        dtls.savedLocation = None
        lastDrive = await self.getLastDrive(dtls)

        if lastDrive and math.isclose(lastDrive["ending_odometer"], dtls.odometer, abs_tol=0.05):
            if "ending_saved_location" in lastDrive:
                dtls.savedLocation = lastDrive["ending_saved_location"]
            else:
                ending = (lastDrive["ending_latitude"], lastDrive["ending_longitude"])
                self.log_location(dtls, ending)
        else:
            await self.addCurrentLocation(dtls)

        return dtls
    # end addLocation(CarDetails)

    async def addBatteryHealth(self, dtls: CarDetails) -> CarDetails:
        """Augment details of a specified vehicle with battery health information
        :param dtls: Details of the vehicle to augment
        :return: The updated vehicle details
        """
        url = f"https://api.tessie.com/{dtls.vin}/battery_health"
        qryParms = {"distance_format": "mi"}

        async with self.session.get(url, params=qryParms) as resp:
            if resp.status == 200:
                try:
                    result: dict = (await resp.json())["result"]
                except Exception as e:
                    raise await HTTPException.fromXcp(e, resp, dtls.displayName) from e
                dtls.battCapacity = result.get("capacity")
            else:
                raise await HTTPException.fromError(resp, dtls.displayName)

            if dtls.battCapacity is None:
                dtls.battCapacity = 1.0  # avoid execution errors
                logging.debug("Missing data"
                              + await Interpret.responseContext(resp, dtls.displayName))

        return dtls
    # end addBatteryHealth(CarDetails)

    async def _wake(self, dtls: CarDetails, attempts: int = 6) -> None:
        """Attempt to wake a specified vehicle from sleep
           - logs a message indicating if fails to wake up, or times out (30s)
        :param dtls: Details of the vehicle to wake
        :param attempts: Number of times to attempt query
        """
        url = f"https://api.tessie.com/{dtls.vin}/wake"

        while attempts:
            logging.info(f"Waking {dtls.displayName}")
            attempts -= 1
            try:
                async with self.session.get(url) as resp:
                    if resp.status != 200:
                        raise await HTTPException.fromError(resp, dtls.displayName)

                    try:
                        wakeOkay: bool = (await resp.json())["result"]
                    except Exception as e:
                        raise await HTTPException.fromXcp(e, resp, dtls.displayName) from e

                if wakeOkay:
                    if await self.waitTillAwake(dtls):
                        break
                    logging.info(f"{dtls.displayName} never woke up, {attempts} more attempts")
                else:
                    logging.info(f"{dtls.displayName} timed out waking, {attempts} more attempts")
            except Exception as e:
                logging.error(e)
                logging.debug(f"{e.__class__.__name__} suppressed:", exc_info=e)

            if attempts:
                await asyncio.sleep(60)
        # end while
    # end _wake(CarDetails, int)

    async def waitTillAwake(self, dtls: CarDetails) -> bool:
        """Wait for this vehicle's sleep status to show awake
        :param dtls: Details of the vehicle to wait for
        :return: True when this vehicle is awake
        """
        retries = 5

        while retries:
            await asyncio.sleep(6)
            await self.getCurrentState(dtls)

            if dtls.awake():
                return True

            retries -= 1
        # end while

        return False
    # end waitTillAwake(CarDetails)

    def getWakeTask(self, dtls: CarDetails) -> asyncio.Task:
        """Get a task scheduled to wake up a specified vehicle
        :param dtls: Details of the vehicle to wake
        :return: Common wake up task instance for the vehicle
        """
        if dtls.wakeTask is None:
            dtls.wakeTask = asyncio.create_task(self._wake(dtls),
                                                name=f"Wake {dtls.displayName}")

        return dtls.wakeTask
    # end getWakeTask(CarDetails)

    async def wakeVehicle(self, dtls: CarDetails) -> None:
        """Wake up a specified vehicle using its common wake task
           - this is needed so TaskGroups can create their own wake-up tasks
        :param dtls: Details of the vehicle to wake
        """
        await self.getWakeTask(dtls)
    # end wakeVehicle(CarDetails)

    @staticmethod
    def edOrIng(pastTense: bool) -> str:
        """Retrieve either "ed" or "ing" depending on past tense argument
        :param pastTense: Flag indicating past tense
        :return: selected string
        """

        return "ed" if pastTense else "ing"
    # end edOrIng(bool)

    async def setChargeLimit(self, dtls: CarDetails, percent: int,
                             waitForCompletion=False) -> None:
        """Set a specified vehicle's charge limit
        :param dtls: Details of the vehicle to set
        :param percent: Charging limit percent
        :param waitForCompletion: Flag indicating to wait for limit to be set
        """
        url = f"https://api.tessie.com/{dtls.vin}/command/set_charge_limit"
        qryParms = {
            "retry_duration": 60,
            "wait_for_completion": "true" if waitForCompletion else "false",
            "percent": percent
        }

        async with self.session.get(url, params=qryParms) as resp:
            oldLimit = dtls.chargeLimit
            dtls.setChargeLimit(percent)

            if resp.status != 200:
                raise await HTTPException.fromError(resp, dtls.displayName)

        logging.info(f"{dtls.displayName} charge limit"
                     f" chang{self.edOrIng(waitForCompletion)}"
                     f" from {oldLimit}% to {percent}%")
    # end setChargeLimit(CarDetails, int, bool)

    async def setRequestCurrent(self, dtls: CarDetails, reqCurrent: int,
                                waitForCompletion=False) -> None:
        """Set the car's request current to a specified value
        :param dtls: Details of the vehicle to set
        :param reqCurrent: New maximum current to request (amps)
        :param waitForCompletion: Flag indicating to wait for request current to be set
        """
        if reqCurrent != dtls.chargeCurrentRequest:
            if not dtls.awake():
                await self.getWakeTask(dtls)

            url = f"https://api.tessie.com/{dtls.vin}/command/set_charging_amps"
            qryParms = {
                "retry_duration": 60,
                "wait_for_completion": "true" if waitForCompletion else "false",
                "amps": reqCurrent,
            }

            async with self.session.get(url, params=qryParms) as resp:
                oldReq = dtls.chargeCurrentRequest
                dtls.setChargeCurrentRequest(reqCurrent)

                if resp.status != 200:
                    raise await HTTPException.fromError(resp, dtls.displayName)

            logging.info(f"{dtls.displayName} request current"
                         f" chang{self.edOrIng(waitForCompletion)}"
                         f" from {oldReq} to {reqCurrent} A")
        else:
            logging.info(f"{dtls.displayName} request current already"
                         f" set to {reqCurrent} A")
    # end setRequestCurrent(CarDetails, int, bool)

    async def startCharging(self, dtls: CarDetails, waitForCompletion=False) -> None:
        """Start charging a specified vehicle
        :param dtls: Details of the vehicle to start charging
        :param waitForCompletion: Flag indicating to wait for charging to start
        """
        url = f"https://api.tessie.com/{dtls.vin}/command/start_charging"
        qryParms = {
            "retry_duration": 60,
            "wait_for_completion": "true" if waitForCompletion else "false"
        }

        async with self.session.get(url, params=qryParms) as resp:
            dtls.setChargingState("Charging")

            if resp.status != 200:
                raise await HTTPException.fromError(resp, dtls.displayName)

        logging.info(f"{dtls.displayName} charging"
                     f" start{self.edOrIng(waitForCompletion)}")
    # end startCharging(CarDetails, bool)

    async def stopCharging(self, dtls: CarDetails, waitForCompletion=False) -> None:
        """Stop charging a specified vehicle
        :param dtls: Details of the vehicle to stop charging
        :param waitForCompletion: Flag indicating to wait for charging to stop
        """
        url = f"https://api.tessie.com/{dtls.vin}/command/stop_charging"
        qryParms = {
            "retry_duration": 60,
            "wait_for_completion": "true" if waitForCompletion else "false"
        }

        async with self.session.get(url, params=qryParms) as resp:
            dtls.setChargingState("Stopping")

            if resp.status != 200:
                raise await HTTPException.fromError(resp, dtls.displayName)

        logging.info(f"{dtls.displayName} charging"
                     f" stopp{self.edOrIng(waitForCompletion)}")
    # end stopCharging(CarDetails, bool)

# end class TessieInterface
