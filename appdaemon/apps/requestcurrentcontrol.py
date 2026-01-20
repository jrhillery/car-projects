# appdaemon/apps/requestcurrentcontrol.py
"""Automatically set cars' request currents based on each cars' charging needs."""

import asyncio
import datetime as dt
from collections import deque
from typing import Any, Generator

from appdaemon import Hass
from appdaemon.entity import Entity
from appdaemon.exceptions import TimeOutException

from tessie import CarDetails


class RequestCurrentControl(Hass):
    """AppDaemon app to automatically set cars' request currents."""
    TESSIE_SETTLE_TIME = dt.timedelta(seconds=15)

    messages: deque[str] = deque()
    vehicles: dict[str, CarDetails]
    totalCurrent: int
    staleWaits: int
    executionLock: asyncio.Lock

    async def initialize(self) -> None:
        """Called when AppDaemon starts the app."""

        # Get configuration
        self.vehicles = {name.lower(): CarDetails.fromAdapi(self, name.lower())
                         for name in self.args.get("vehicles", [])}
        self.totalCurrent = self.args.get("totalCurrent", 32)
        self.staleWaits = 0
        self.executionLock = asyncio.Lock()

        for dtls in self.vehicles.values():
            # Listen for charge limit changes
            await dtls.chargeLimitNumber.listen_state(
                self.handleStateChange,
                callMsg=f"{dtls.chargeLimitNumber.friendly_name} changed to %new%")

            # Listen for charge stopped events
            await dtls.chargeSwitch.listen_state(
                self.handleStateChange, old="on", new="off",
                callMsg=f"{dtls.chargeSwitch.friendly_name} stopped")

            # Listen for plug-in events
            await dtls.chargeCableDetector.listen_state(
                self.handlePlugIn, old="off", new="on",
                callMsg=f"{dtls.chargeCableDetector.friendly_name} plugged in")
            await dtls.chargeCableDetector.listen_state(
                self.handleUnplug, old="on", new="off",
                callMsg=f"{dtls.chargeCableDetector.friendly_name} unplugged")

        # Listen for a custom event (type hint says no async callback, but it's supported)
        # noinspection PyTypeChecker
        await self.listen_event(self.handleEvent, "set_request_currents")

        self.log("Ready to adjust cars' charging request currents")
    # end initialize()

    async def handleStateChange(self, _entityId: str, _attribute: str, _old: Any,
                                new: Any, callMsg: str, **_kwargs: Any) -> None:
        """Called when a state changes."""
        callMsg = callMsg.replace("%new%", new)
        self.log(callMsg)
        await self.setRequestCurrentsIfNotRunning(callMsg)
    # end handleStateChange(str, str, Any, Any, str, **Any)

    async def handlePlugIn(self, entityId: str, _attribute: str, _old: Any,
                           _new: Any, callMsg: str, **_kwargs: Any) -> None:
        """Called when a new vehicle may begin charging with stale charge current data."""
        self.log(callMsg)
        self.staleWaits += 1
        try:
            callTime = await self.get_state(entityId, "last_updated")
            newChargeCarName = self.vehicleName(entityId)

            for name, dtls in self.vehicles.items():
                if dtls.chargingAtHome() and name != newChargeCarName:
                    await self.setRequestCurrent(dtls, dtls.TESLA_APP_REQ_MIN_AMPS)

            await self.waitStaleCurrents(newChargeCarName, callTime)
        finally:
            self.staleWaits -= 1

        await self.setRequestCurrentsIfNotRunning(callMsg)
    # end handlePlugIn(str, str, Any, Any, str, **Any)

    async def handleUnplug(self, entityId: str, _attribute: str, _old: Any,
                           _new: Any, callMsg: str, **_kwargs: Any) -> None:
        """Called when a vehicle's charging cable is unplugged."""
        self.log(callMsg)
        self.staleWaits += 1
        try:
            dtls = self.vehicles[self.vehicleName(entityId)]
            try:
                await dtls.chargingSensor.wait_state("disconnected", timeout=60)
            except TimeOutException:
                # already logged by Entity.wait_state
                pass
        finally:
            self.staleWaits -= 1

        await self.setRequestCurrentsIfNotRunning(callMsg)
    # end handleUnplug(str, str, Any, Any, str, **Any)

    async def waitStaleCurrents(self, vehicleName: str, callTime: str) -> None:
        """Waits a while for stale charge current data.

        :param vehicleName: Name of the vehicle triggering this wait
        :param callTime: Date and time state change triggered
        """
        await self.wakeSnoozers()

        # give the triggering vehicle more time to settle in
        settleTime = self.convert_utc(callTime) + self.TESSIE_SETTLE_TIME
        await self.sleep((settleTime - await self.get_now()).total_seconds())

        await self.awaitNewReport(self.vehicles[vehicleName].chargeCurrentNumber, callTime)
    # end waitStaleCurrents(str, str)

    async def handleEvent(self, eventType: str, _data: dict[str, Any], **_kwargs: Any) -> None:
        """Handle custom event."""
        title = f"Event {eventType} fired"
        self.log(title)
        await self.setRequestCurrentsIfNotRunning(title)
    # end handleEvent(str, dict[str, Any], **Any)

    async def awaitNewReport(self, entity: Entity, oldTime: str | None = None) -> None:
        """Wait for a new last reported time in a given entity.

        :param entity: Entity to wait for
        :param oldTime: Old time to surpass
        """
        oldTime = oldTime or await entity.get_state("last_updated")
        try:
            await entity.wait_state(
                lambda st: st["last_reported"] > oldTime,
                attribute="all", timeout=60)
            self.log("%s reported", entity.friendly_name)
        except TimeOutException:
            # already logged by Entity.wait_state
            pass
    # end awaitNewReport(Entity, str | None)

    @staticmethod
    def vehicleName(entityId: str) -> str:
        """Retrieve the vehicle name.

        :param entityId: Fully qualified entity id
        :return: vehicle name
        """
        _, entityName = entityId.split(".")
        vehicleName, _ = entityName.split("_", 1)

        return vehicleName
    # end vehicleName(str)

    def logMsg(self, message: str) -> None:
        """Log an info level message and add it to our list.

        :param message: Message to include
        """
        self.log(message)

        # tilde is a control character in Markdown - so escape it
        self.messages.append(message.replace("~", "\\~"))
    # end logMsg(str)

    def generateMsgs(self) -> Generator[str, None, None]:
        """Generate each included message once."""
        while self.messages:
            yield self.messages.popleft()
    # end generateMsgs()

    async def wakeSnoozers(self) -> None:
        """Wake up any cars that are sleeping, plugged-in and at home."""
        for _ in range(4):
            statuses: list[Entity] = []
            for dtls in self.vehicles.values():
                if not dtls.awake() and dtls.pluggedInAtHome():
                    self.log("Waking %s", dtls.displayName)
                    await dtls.wakeButton.call_service("press", hass_timeout=55)
                    statuses.append(dtls.statusDetector)

            timeout = False
            for vehicleStatus in statuses:
                try:
                    await vehicleStatus.wait_state("on", timeout=30)
                    self.log("%s awake", vehicleStatus.friendly_name)
                except TimeOutException:
                    # already logged by Entity.wait_state
                    timeout = True
            if not timeout:
                break
        # end for 4 attempts
    # end wakeSnoozers()

    def limitRequestCurrents(self, desReqCurrents: dict[str, float]) -> dict[str, int]:
        """Get corresponding request currents valid for each charge adapter.

        :param desReqCurrents: Dictionary of desired request currents (amps)
        :return: Corresponding dictionary of valid request currents
        """
        requestCurrents: dict[str, int] = {}
        remainingCurrent = self.totalCurrent

        for name, dtls in self.vehicles.items():
            requestCurrent = dtls.limitRequestCurrent(int(desReqCurrents[name] + 0.5))
            requestCurrents[name] = requestCurrent
            remainingCurrent -= requestCurrent
        # end for

        if remainingCurrent < 0 < len(requestCurrents):
            # we oversubscribed, reduce the largest request current
            keys = list(requestCurrents.keys())
            keys.sort(key=lambda k: requestCurrents[k], reverse=True)
            requestCurrents[keys[0]] += remainingCurrent  # this is negative

        return requestCurrents
    # end limitRequestCurrents(dict[str, float])

    def calcRequestCurrents(self) -> dict[str, int]:
        """Calculate the current needed for each vehicle.

        :return: dict of currents needed
        """
        energiesNeeded: dict[str, float] = {}
        totalEnergyNeeded = 0.0

        for name, dtls in self.vehicles.items():
            energyNeeded = dtls.neededKwh()

            if energyNeeded:
                self.logMsg(dtls.chargingStatusSummary(energyNeeded))
            else:
                self.log(dtls.chargingStatusSummary())

            energiesNeeded[name] = energyNeeded
            totalEnergyNeeded += energyNeeded
        # end for

        # Calculate request currents based on energy needs
        if totalEnergyNeeded:
            reqCurrents = {
                name: self.totalCurrent * (energy / totalEnergyNeeded)
                for name, energy in energiesNeeded.items()}
        else:
            reqCurrent = self.totalCurrent / len(self.vehicles)
            reqCurrents = {name: reqCurrent for name in energiesNeeded}

        return self.limitRequestCurrents(reqCurrents)
    # end calcRequestCurrents()

    async def setRequestCurrent(self, dtls: CarDetails, reqCurrent: int) -> None:
        """Set the car's request current to a specified value

        :param dtls: Details of the vehicle to set
        :param reqCurrent: New maximum charge current to request (amps)
        """
        if reqCurrent == dtls.chargeCurrentRequest:
            self.log("%s request current already set to %d A", dtls.displayName, reqCurrent)
            return

        self.logMsg(f"{dtls.displayName} request current changing from"
                    f" {dtls.chargeCurrentRequest:d} to {reqCurrent:d} A")

        for _ in range(9):
            results = await dtls.chargeCurrentNumber.call_service(
                "set_value", value=reqCurrent, hass_timeout=55)

            if (duration := results["ad_duration"]) > 0.4:
                break  # all good, break out of for loop

            # don't trust this quick response - wait to retry
            self.log("%s set value responded too quickly (%.2f s) - retrying",
                     dtls.chargeCurrentNumber.friendly_name, duration)
            await self.awaitNewReport(dtls.chargeCurrentNumber)
        # end for 9 attempts
    # end setRequestCurrent(CarDetails, int)

    async def setRequestCurrents(self, notificationTitle: str) -> None:
        """Automatically set cars' request currents based on each cars' charging needs.

        :param notificationTitle: Title for persistent notification, if any
        """
        await self.wakeSnoozers()

        for _ in range(5):
            try:
                reqCurrents = self.calcRequestCurrents()
                keys = list(self.vehicles.keys())

                # To decrease first, sort ascending by increase in request current
                keys.sort(key=lambda k: reqCurrents[k] - self.vehicles[k].chargeCurrentRequest)

                for vehicleName in keys:
                    dtls = self.vehicles[vehicleName]
                    if dtls.pluggedInAtHome():
                        await self.setRequestCurrent(dtls, reqCurrents[vehicleName])
                # end for

                break  # all good, break out of for loop
            except Exception as e:
                self.error("Retrying after error setting request currents %s: %s",
                           e.__class__.__name__, e)
                await self.sleep(15)
        # end for 5 attempts

        if self.messages:
            await self.call_service("persistent_notification/create", title=notificationTitle,
                                    message="\n".join(self.generateMsgs()))
        self.log("Charging request currents are set")
    # end setRequestCurrents(str)

    async def setRequestCurrentsIfNotRunning(self, notificationTitle: str) -> None:
        """Call setRequestCurrents if it's not currently running.

        :param notificationTitle: Title for persistent notification, if any
        """
        if self.staleWaits > 0:
            self.log("Premature run suppressed")
        else:
            try:
                async with asyncio.timeout(0) as to:
                    async with self.executionLock:
                        to.reschedule(None)
                        await self.setRequestCurrents(notificationTitle)
            except asyncio.TimeoutError:
                self.log("Simultaneous run suppressed")
    # end setRequestCurrentsIfNotRunning(str)
