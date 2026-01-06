# appdaemon/apps/requestcurrentcontrol.py
"""Automatically set cars' request currents based on each cars' charging needs."""

from __future__ import annotations

from collections import deque
from typing import Any, cast, Generator

from appdaemon.entity import Entity
from appdaemon.events import EventCallback
from appdaemon.exceptions import TimeOutException
from appdaemon.plugins.hass import Hass
from appdaemon.state import AsyncStateCallback

from tessie import CarDetails


# noinspection PyInvalidCast
class RequestCurrentControl(Hass):
    """AppDaemon app to automatically set cars' request currents."""
    messages: deque[str] = deque()
    vehicleNames: list[str]
    totalCurrent: int
    alreadyActive: bool

    async def initialize(self) -> None:
        """Called when AppDaemon starts the app."""

        # Get configuration
        self.vehicleNames = [name.lower() for name in self.args.get("vehicles", [])]
        self.totalCurrent = self.args.get("totalCurrent", 32)

        # Listen for plug-in events
        await self.listen_state(
            cast(AsyncStateCallback, self.handleStaleStateChange),
            [f"binary_sensor.{name}_charge_cable" for name in self.vehicleNames],
            old="off", new="on", eventDesc="plugged in")
        await self.listen_state(
            cast(AsyncStateCallback, self.handleStateChange),
            [f"binary_sensor.{name}_charge_cable" for name in self.vehicleNames],
            old="on", new="off", eventDesc="unplugged")

        # Listen for charge limit changes
        await self.listen_state(
            cast(AsyncStateCallback, self.handleStateChange),
            [f"number.{name}_charge_limit" for name in self.vehicleNames],
            eventDesc="changed")

        # Listen for charge stopped events
        await self.listen_state(
            cast(AsyncStateCallback, self.handleStateChange),
            [f"switch.{name}_charge" for name in self.vehicleNames],
            old="on", new="off", eventDesc="stopped")

        # Listen for a custom event
        await self.listen_event(
            cast(EventCallback, self.handleEvent),
            "SET_REQUEST_CURRENTS")
        self.alreadyActive = False
        self.log("Ready to adjust cars' request currents")
    # end initialize()

    async def handleStateChange(self, entity: str, _attribute: str, _old: Any, _new: Any,
                                **kwargs: Any) -> None:
        """Called when a state changes."""
        notRunning = not self.running()
        self.log("%s %s", await self.friendly_name(entity), kwargs["eventDesc"])
        if notRunning:
            await self.setRequestCurrents()
    # end handleStateChange(str, str, Any, Any, Any)

    async def handleStaleStateChange(self, entity: str, _attribute: str, _old: Any, _new: Any,
                                     **kwargs: Any) -> None:
        """Called when a state changes with stale charge current data."""
        notRunning = not self.running()
        self.log("%s %s", await self.friendly_name(entity), kwargs["eventDesc"])
        if notRunning:
            await self.listen_state(
                cast(AsyncStateCallback, self.handleFreshStateChange),
                f"number.{self.vehicleName(entity)}_charge_current",
                attribute="last_reported", oneshot=True)
    # end handleStaleStateChange(str, str, Any, Any, Any)

    async def handleFreshStateChange(self, entity: str, _attribute: str, _old: Any, _new: Any,
                                     **_kwargs: Any) -> None:
        """Called when we have fresh data."""
        self.log("%s reported", await self.friendly_name(entity))
        await self.setRequestCurrents()
    # end handleFreshStateChange(str, str, Any, Any, Any)

    async def handleEvent(self, event_type: str, _data: dict[str, Any],
                          **_kwargs: Any) -> None:
        """Handle custom event."""
        self.log("Event %s fired", event_type)
        await self.setRequestCurrents()
    # end handleEvent(str, dict[str, Any], Any)

    def running(self) -> bool:
        """Check if we are already running."""
        alreadyRunning = self.alreadyActive
        self.alreadyActive = True
        if alreadyRunning:
            self.log("Duplicate run suppressed")

        return alreadyRunning
    # end running()

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
        self.messages.append(message)
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
            vehicles = [await CarDetails.fromAdapi(self, name) for name in self.vehicleNames]
            for dtls in vehicles:
                if not dtls.awake() and dtls.pluggedInAtHome():
                    self.logMsg(f"Waking {dtls.displayName}")
                    await self.call_service(
                        "button/press",
                        entity_id=f"button.{dtls.vehicleName}_wake",
                        hass_timeout=55)
                    statuses.append(self.get_entity(f"binary_sensor.{dtls.vehicleName}_status"))

            timeouts = 0
            for vehicleStatus in statuses:
                try:
                    await vehicleStatus.wait_state("on", timeout=55)
                    self.log("%s awake", vehicleStatus.friendly_name)
                except TimeOutException:
                    timeouts += 1
                    self.log("%s timed out waking", vehicleStatus.friendly_name)
            if timeouts == 0:
                break
        # end for 6 attempts
    # end wakeSnoozers()

    def limitRequestCurrents(self, vehicles: list[CarDetails],
                             desReqCurrents: list[float]) -> list[int]:
        """Get corresponding request currents valid for each charge adapter.

        :param vehicles: List of cars to have their request currents limited
        :param desReqCurrents: Corresponding list of desired request currents (amps)
        :return: Corresponding list of valid request currents
        """
        requestCurrents: list[int] = []
        remainingCurrent = self.totalCurrent

        for i, dtls in enumerate(vehicles):
            requestCurrent = dtls.limitRequestCurrent(int(desReqCurrents[i] + 0.5))
            requestCurrents.append(requestCurrent)
            remainingCurrent -= requestCurrent
        # end for

        if remainingCurrent < 0 < len(requestCurrents):
            # we oversubscribed, reduce the largest request current
            indices: list[int] = list(range(len(requestCurrents)))
            indices.sort(key=lambda j: requestCurrents[j], reverse=True)
            requestCurrents[indices[0]] += remainingCurrent

        return requestCurrents
    # end limitRequestCurrents(list[CarDetails], list[float])

    def calcRequestCurrents(self, vehicles: list[CarDetails]) -> list[int]:
        """Calculate the current needed for each vehicle.

        :param vehicles: List of cars to have their request currents calculated
        :return: list of currents needed
        """
        energiesNeeded: list[float] = []
        totalEnergyNeeded = 0.0

        for dtls in vehicles:
            energyNeeded = dtls.neededKwh()

            if energyNeeded:
                self.logMsg(dtls.chargingStatusSummary(energyNeeded))

            energiesNeeded.append(energyNeeded)
            totalEnergyNeeded += energyNeeded
        # end for

        # Calculate request currents based on energy needs
        if totalEnergyNeeded:
            reqCurrents = [
                self.totalCurrent * (energy / totalEnergyNeeded) for energy in energiesNeeded
            ]
        else:
            reqCurrent = self.totalCurrent / len(vehicles)
            reqCurrents = [reqCurrent] * len(vehicles)

        return self.limitRequestCurrents(vehicles, reqCurrents)
    # end calcRequestCurrents(list[CarDetails])

    async def setRequestCurrent(self, dtls: CarDetails, reqCurrent: int) -> None:
        """Set the car's request current to a specified value

        :param dtls: Details of the vehicle to set
        :param reqCurrent: New maximum current to request (amps)
        """
        if reqCurrent != dtls.chargeCurrentRequest:
            self.logMsg(
                f"{dtls.displayName} request current changing from"
                f" {dtls.chargeCurrentRequest} to {reqCurrent} A"
            )
            results = await self.call_service(
                "number/set_value",
                entity_id=dtls.chargeCurrentEntityId,
                value=reqCurrent,
                hass_timeout=55)
            if results["success"] is False:
                self.log("Set results: %s", str(results))
        else:
            self.logMsg(f"{dtls.displayName} request current already set to {reqCurrent} A")
    # end setRequestCurrent(CarDetails, int)

    async def setRequestCurrents(self) -> None:
        """Automatically set cars' request currents based on each cars' charging needs."""
        await self.wakeSnoozers()

        # Get fresh details for all vehicles
        vehicles = [await CarDetails.fromAdapi(self, vehicleName) for vehicleName in self.vehicleNames]

        reqCurrents = self.calcRequestCurrents(vehicles)
        indices = list(range(len(vehicles)))

        # To decrease first, sort indices ascending by increase in request current
        indices.sort(key=lambda i: reqCurrents[i] - vehicles[i].chargeCurrentRequest)

        for idx in indices:
            dtls = vehicles[idx]
            if dtls.pluggedInAtHome():
                await self.setRequestCurrent(dtls, reqCurrents[idx])
            else:
                self.log(dtls.chargingStatusSummary())
        # end for

        if self.messages:
            await self.call_service(
                "persistent_notification/create",
                title="Set Cars' Request Currents",
                message="\n".join(self.generateMsgs()),
            )
        self.alreadyActive = False
        self.log("Request currents are set")
    # end setRequestCurrents()
