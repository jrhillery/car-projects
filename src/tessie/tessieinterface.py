
import asyncio
import json
import logging
from typing import AsyncContextManager, Self

from aiohttp import ClientResponse, ClientSession

from util import Configure, HTTPException, Interpret
from . import CarDetails


class TessieInterface(AsyncContextManager[Self]):
    """Provides an interface through Tessie to authorized vehicles"""

    def __init__(self, totalCurrent: int):
        """Initialize this instance
        :param totalCurrent: The total current available to all cars
        """
        self.totalCurrent: int = totalCurrent
    # end __init__(int)

    async def __aenter__(self) -> Self:
        """Allocate resources"""
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {await TessieInterface.loadToken()}"
        }
        self.session = ClientSession(headers=headers)

        return self
    # end __aenter__()

    @staticmethod
    async def loadToken() -> str:
        filePath = Configure.findParmPath().joinpath("accesstoken.json")

        with open(filePath, "r", encoding="utf-8") as tokenFile:

            return json.load(tokenFile)["token"]
    # end loadToken()

    async def getStateOfActiveVehicles(self) -> list[CarDetails]:
        """Get all active vehicles and their latest state - this call always
           returns a complete set of data and doesn't impact vehicle sleep
           - if the vehicle is awake, the data is usually less than 10 seconds old
           - if the vehicle is asleep, the data is from the time the vehicle went to sleep
        :return: A list with details of the active vehicles in the account
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

    async def getCurrentState(self, dtls: CarDetails, attempts: int = 10) -> None:
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
                                tg.create_task(self.addSleepStatus(dtls))
                                tg.create_task(self.addLocation(dtls))
                            # end async with (tasks are awaited)

                            return logging.info(dtls.chargingStatusSummary())
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

    async def addLocation(self, dtls: CarDetails) -> CarDetails:
        """Augment details of a specified vehicle with its location
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
                                  f" {await Interpret.responseXcp(resp, e, dtls.displayName)}",
                                  exc_info=e)
                    dtls.savedLocation = None
            else:
                logging.error(await self.respErrLog(resp, dtls))
                dtls.savedLocation = None

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
                    result = (await resp.json())["result"]
                    dtls.battMaxRange = result["max_range"]
                    dtls.battCapacity = result["capacity"]
                except Exception as e:
                    raise await HTTPException.fromXcp(e, resp, dtls.displayName) from e
            else:
                raise await HTTPException.fromError(resp, dtls.displayName)

        return dtls
    # end addBatteryHealth(CarDetails)

    async def wake(self, dtls: CarDetails, attempts: int = 6) -> None:
        """Attempt to wake a specified vehicle from sleep
           - logs a message indicating if fails to wake up, or times out (30s)
        :param dtls: Details of the vehicle to wake
        :param attempts: Number of times to attempt query
        """
        url = f"https://api.tessie.com/{dtls.vin}/wake"

        while attempts:
            async with self.session.get(url) as resp:
                if resp.status != 200:
                    raise await HTTPException.fromError(resp, dtls.displayName)

                try:
                    wakeOkay: bool = (await resp.json())["result"]
                except Exception as e:
                    raise await HTTPException.fromXcp(e, resp, dtls.displayName) from e
            attempts -= 1

            if wakeOkay:
                if await self.waitTillAwake(dtls):
                    break
                logging.info(f"{dtls.displayName} never woke up, {attempts} more attempts")
            else:
                logging.info(f"{dtls.displayName} timed out waking, {attempts} more attempts")

            if attempts:
                await asyncio.sleep(60)
        # end while
    # end wake(CarDetails, int)

    async def waitTillAwake(self, dtls: CarDetails) -> bool:
        """Wait for this vehicle's sleep status to show awake
        :param dtls: Details of the vehicle to wait for
        :return: True when this vehicle is awake
        """
        retries = 8

        while retries:
            await self.getCurrentState(dtls, attempts=1)

            if dtls.awake():
                return True

            if retries := retries - 1:
                await asyncio.sleep(4)
        # end while

        return False
    # end waitTillAwake(CarDetails)

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
            dtls.chargeLimit = percent

            if resp.status != 200:
                raise await HTTPException.fromError(resp, dtls.displayName)

        logging.info(f"{dtls.displayName} charge limit changed"
                     f" from {oldLimit}% to {percent}%")
    # end setChargeLimit(CarDetails, int, bool)

    async def setChargingCurrent(self, dtls: CarDetails, maxCurrent: int,
                                 waitForCompletion=False) -> None:
        """Set the car's maximum request current to a specified value
        :param dtls: Details of the vehicle to set
        :param maxCurrent: New maximum current to request (amps)
        :param waitForCompletion: Flag indicating to wait for request current to be set
        """
        if dtls.atHome():
            if maxCurrent != dtls.chargeCurrentRequest:
                url = f"https://api.tessie.com/{dtls.vin}/command/set_charging_amps"
                qryParms = {
                    "retry_duration": 60,
                    "wait_for_completion": "true" if waitForCompletion else "false",
                    "amps": maxCurrent,
                }

                async with self.session.get(url, params=qryParms) as resp:
                    oldMax = dtls.chargeCurrentRequest
                    dtls.chargeCurrentRequest = maxCurrent

                    if resp.status != 200:
                        raise await HTTPException.fromError(resp, dtls.displayName)

                logging.info(f"{dtls.displayName} charging current changed"
                             f" from {oldMax} to {maxCurrent} A")
            elif maxCurrent != self.totalCurrent // 2:
                # supress already set messages when sharing current equally - they're common
                logging.info(f"{dtls.displayName} charging current already"
                             f" set to {maxCurrent} A")
    # end setChargingCurrent(CarDetails, int, bool)

    def wakeIfSettingCurrent(self, dtls: CarDetails, maxCurrent: int,
                             tg: asyncio.TaskGroup) -> None:
        """Create a task to wake this car if the setChargingCurrent logic says this
           car will have its charging current set and this car is not already awake
        :param dtls: Details of the vehicle to wake
        :param maxCurrent: Maximum current to request (amps)
        :param tg: Task group to hold the resulting task
        """
        if dtls.atHome() and maxCurrent != dtls.chargeCurrentRequest and not dtls.awake():
            tg.create_task(self.wake(dtls))
    # end wakeIfSettingCurrent(CarDetails, int, asyncio.TaskGroup)

    async def setMaximums(self, dtlsA: CarDetails, maxAmpsA: int, dtlsB: CarDetails,
                          waitForCompletion=False) -> None:
        """Set cars' maximum charge request currents, decrease one before increasing the other
        :param dtlsA: Details of one of the cars to set
        :param maxAmpsA: Desired maximum request current for dtlsA (amps)
        :param dtlsB: Details of the other car to set (gets remaining current)
        :param waitForCompletion: Flag indicating to wait for final request current to be set
        """
        maxAmpsB = self.totalCurrent - maxAmpsA

        async with asyncio.TaskGroup() as tg:
            # create tasks to wake cars that will have their charging current set
            self.wakeIfSettingCurrent(dtlsA, maxAmpsA, tg)
            self.wakeIfSettingCurrent(dtlsB, maxAmpsB, tg)
        # end async with (tasks are awaited)

        if maxAmpsA < dtlsA.chargeCurrentRequest:
            # decreasing dtlsA current, so do it first
            await self.setChargingCurrent(dtlsA, maxAmpsA, waitForCompletion=True)
            await self.setChargingCurrent(dtlsB, maxAmpsB, waitForCompletion)
        else:
            await self.setChargingCurrent(dtlsB, maxAmpsB, waitForCompletion=True)
            await self.setChargingCurrent(dtlsA, maxAmpsA, waitForCompletion)
    # end setMaximums(CarDetails, int, CarDetails, bool)

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
            dtls.chargingState = "Charging"

            if resp.status != 200:
                raise await HTTPException.fromError(resp, dtls.displayName)

        logging.info(f"{dtls.displayName} charging started")
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
            dtls.chargingState = "Stopping"

            if resp.status != 200:
                raise await HTTPException.fromError(resp, dtls.displayName)

        logging.info(f"{dtls.displayName} charging stopped")
    # end stopCharging(CarDetails, bool)

    async def __aexit__(self, exc_type, exc: BaseException | None, exc_tb) -> None:
        """Close this instance and free up resources"""
        await self.session.close()
    # end __aexit__(Type[BaseException] | None, BaseException | None, TracebackType | None)

# end class TessieInterface
