# appdaemon/apps/requestcurrentcontrol.py
"""Automatically set cars' request currents based on each cars' charging needs."""

from __future__ import annotations

import datetime as dt
from collections import deque
from typing import Any, cast, Generator

from appdaemon import Hass
from appdaemon.entity import Entity
from appdaemon.events import EventCallback
from appdaemon.exceptions import TimeOutException
from appdaemon.state import AsyncStateCallback

from tessie import CarDetails


# noinspection PyInvalidCast
class RequestCurrentControl(Hass):
    """AppDaemon app to automatically set cars' request currents."""
    TESSIE_SETTLE_TIME = dt.timedelta(seconds=15)

    messages: deque[str] = deque()
    vehicles: dict[str, CarDetails]
    totalCurrent: int
    alreadyActive: bool
    staleWaits: int

    async def initialize(self) -> None:
        """Called when AppDaemon starts the app."""

        # Get configuration
        self.vehicles = {name.lower(): CarDetails.fromAdapi(self, name.lower())
                         for name in self.args.get("vehicles", [])}
        self.totalCurrent = self.args.get("totalCurrent", 32)
        self.alreadyActive = False
        self.staleWaits = 0

        for dtls in self.vehicles.values():
            # Listen for charge limit changes
            await dtls.chargeLimitNumber.listen_state(
                cast(AsyncStateCallback, self.handleStateChange),
                callMsg=f"{dtls.chargeLimitNumber.friendly_name} changed to %new%")

            # Listen for charge stopped events
            await dtls.chargeSwitch.listen_state(
                cast(AsyncStateCallback, self.handleStateChange),
                old="on", new="off",
                callMsg=f"{dtls.chargeSwitch.friendly_name} stopped")

            # Listen for plug-in events
            await dtls.chargeCableDetector.listen_state(
                cast(AsyncStateCallback, self.handleNewCharge),
                old="off", new="on",
                callMsg=f"{dtls.chargeCableDetector.friendly_name} plugged in")
            await dtls.chargeCableDetector.listen_state(
                cast(AsyncStateCallback, self.handleStaleStateChange),
                old="on", new="off",
                callMsg=f"{dtls.chargeCableDetector.friendly_name} unplugged")

        # Listen for a custom event
        await self.listen_event(
            cast(EventCallback, self.handleEvent),
            "set_request_currents")

        self.log("Ready to adjust cars' request currents")
    # end initialize()

    async def handleStateChange(self, _entityId: str, _attribute: str, _old: Any,
                                new: Any, callMsg: str, **_kwargs: Any) -> None:
        """Called when a state changes."""
        callMsg = callMsg.replace("%new%", new)
        self.log(callMsg)

        if self.alreadyActive or self.staleWaits > 0:
            self.log("Simultaneous run suppressed")
        else:
            self.alreadyActive = True
            await self.setRequestCurrents(callMsg)
    # end handleStateChange(str, str, Any, Any, str, **Any)

    async def handleNewCharge(self, entityId: str, *_args: Any,
                              callMsg: str, **_kwargs: Any) -> None:
        """Called when a new vehicle may begin charging with stale charge current data."""
        self.log(callMsg)
        self.staleWaits += 1
        callTime = await self.get_state(entityId, "last_updated")
        newChargeCarName = self.vehicleName(entityId)

        for name, dtls in self.vehicles.items():
            if dtls.chargingAtHome() and name != newChargeCarName:
                await self.setRequestCurrent(dtls, dtls.TESLA_APP_REQ_MIN_AMPS)

        await self.waitStaleCurrents(newChargeCarName, callTime, callMsg)
    # end handleNewCharge(str, *Any, str, **Any)

    async def handleStaleStateChange(self, entityId: str, *_args: Any,
                                     callMsg: str, **_kwargs: Any) -> None:
        """Called when a state changes with stale charge current data."""
        self.log(callMsg)
        self.staleWaits += 1
        callTime = await self.get_state(entityId, "last_updated")

        await self.waitStaleCurrents(self.vehicleName(entityId), callTime, callMsg)
    # end handleStaleStateChange(str, *Any, str, **Any)

    async def waitStaleCurrents(self, vehicleName: str, callTime: str,
                                notificationTitle: str) -> None:
        """Waits a while for stale charge current data.

        :param vehicleName: Name of the vehicle triggering this wait
        :param callTime: Date and time state change triggered
        :param notificationTitle: Title for persistent notification, if any
        """
        await self.wakeSnoozers()

        # give the triggering vehicle more time to settle in
        settleTime = self.convert_utc(callTime) + self.TESSIE_SETTLE_TIME
        await self.sleep((settleTime - await self.get_now()).total_seconds())

        vehicle = self.vehicles[vehicleName]
        try:
            await vehicle.chargeCurrentNumber.wait_state(
                lambda st: st["last_reported"] > callTime,
                attribute="all", timeout=60)
            self.log("%s reported", vehicle.chargeCurrentNumber.friendly_name)
        except TimeOutException:
            pass

        self.alreadyActive = True
        self.staleWaits -= 1

        if self.staleWaits == 0:
            await self.setRequestCurrents(notificationTitle)
    # end waitStaleCurrents(str, str, str)

    async def handleEvent(self, eventType: str, _data: dict[str, Any],
                          **_kwargs: Any) -> None:
        """Handle custom event."""
        title = f"Event {eventType} fired"
        self.log(title)
        await self.setRequestCurrents(title)
    # end handleEvent(str, dict[str, Any], **Any)

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
        for _ in range(6):
            statuses: list[Entity] = []
            for dtls in self.vehicles.values():
                if not dtls.awake() and dtls.pluggedInAtHome():
                    self.log(f"Waking {dtls.displayName}")
                    await dtls.wakeButton.call_service("press", hass_timeout=55)
                    statuses.append(dtls.statusDetector)

            timeout = False
            for vehicleStatus in statuses:
                try:
                    await vehicleStatus.wait_state("on", timeout=55)
                    self.log("%s awake", vehicleStatus.friendly_name)
                except TimeOutException:
                    timeout = True
            if not timeout:
                break
        # end for 6 attempts
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
            requestCurrents[keys[0]] += remainingCurrent

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
        :param reqCurrent: New maximum current to request (amps)
        """
        if reqCurrent != dtls.chargeCurrentRequest:
            self.logMsg(
                f"{dtls.displayName} request current changing from"
                f" {dtls.chargeCurrentRequest} to {reqCurrent} A")
            results = await dtls.chargeCurrentNumber.call_service(
                "set_value", value=reqCurrent, hass_timeout=55)
            self.log("Result: %s", results)
        else:
            self.log(f"{dtls.displayName} request current already set to {reqCurrent} A")
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
            except ValueError as e:
                self.error(f"Error setting request currents {e.__class__.__name__}: {e}")
                await self.sleep(15)
        # end for 5 attempts

        if self.messages:
            await self.call_service(
                "persistent_notification/create",
                title=notificationTitle, message="\n".join(self.generateMsgs()))
        self.alreadyActive = False
        self.log("Request currents are set")
    # end setRequestCurrents(str)
