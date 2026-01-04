# appdaemon/apps/requestcurrentcontrol.py
"""Automatically set cars' request currents based on each cars' charging needs."""

from __future__ import annotations

from collections import deque
from typing import Any, cast, Generator

from appdaemon.events import EventCallback
from appdaemon.plugins.hass import Hass
from appdaemon.state import AsyncStateCallback

from tessie import CarDetails


class RequestCurrentControl(Hass):
    """AppDaemon app to automatically set cars' request currents."""
    messages: deque[str] = deque()
    vehicleNames: list[str]
    totalCurrent: int

    # noinspection PyInvalidCast
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
        self.log("Ready to adjust cars' request currents")
    # end initialize()

    async def handleStateChange(self, entity: str, _attribute: str, _old: Any, _new: Any,
                                **kwargs: Any) -> None:
        """Called when a state changes."""
        self.log("%s %s", await self.friendly_name(entity), kwargs["eventDesc"])
        await self.setRequestCurrents()
    # end handleStateChange(str, str, Any, Any, Any)

    # noinspection PyInvalidCast
    async def handleStaleStateChange(self, entity: str, _attribute: str, _old: Any, _new: Any,
                                     **kwargs: Any) -> None:
        """Called when a state changes with stale charge current data."""
        self.log("%s %s", await self.friendly_name(entity), kwargs["eventDesc"])
        await self.listen_state(
            cast(AsyncStateCallback, self.handleStateChange),
            f"number.{await self.vehicleName(entity)}_charge_current",
            attribute="last_reported", oneshot=True, eventDesc="reported")
    # end handleStaleStateChange(str, str, Any, Any, Any)

    async def handleEvent(self, event_type: str, _data: dict[str, Any],
                          **_kwargs: Any) -> None:
        """Handle custom event."""
        self.log("Event %s fired", event_type)
        await self.setRequestCurrents()
    # end handleEvent(str, dict[str, Any], Any)

    async def vehicleName(self, entityId: str) -> str:
        """Retrieve the vehicle name.

        :param entityId: Fully qualified entity id
        :return: vehicle name
        """
        _, entityName = await self.split_entity(entityId)
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

    async def setRequestCurrents(self) -> None:
        """Automatically set cars' request currents based on each cars' charging needs."""

        # Get fresh details for all vehicles
        vehicles = [await CarDetails.fromAdapi(self, vehicleName) for vehicleName in self.vehicleNames]

        reqCurrents = self.calcRequestCurrents(vehicles)
        indices = list(range(len(vehicles)))

        # To decrease first, sort indices ascending by increase in request current
        indices.sort(key=lambda i: reqCurrents[i] - vehicles[i].chargeCurrentRequest)

        for idx in indices:
            dtls = vehicles[idx]
            if dtls.pluggedInAtHome():
                reqCurrent = reqCurrents[idx]
                if reqCurrent != dtls.chargeCurrentRequest:
                    self.logMsg(
                        f"{dtls.displayName} request current changing from"
                        f" {dtls.chargeCurrentRequest} to {reqCurrent} A"
                    )
                    await self.call_service(
                        "number/set_value",
                        entity_id=dtls.chargeCurrentEntityId,
                        value=reqCurrent,
                    )
                else:
                    self.logMsg(f"{dtls.displayName} request current already set to {reqCurrent} A")
            else:
                self.log(dtls.chargingStatusSummary())
        # end for

        if self.messages:
            await self.call_service(
                "persistent_notification/create",
                title="Set Cars' Request Currents",
                message="\n".join(self.generateMsgs()),
            )
        self.log("Request currents are set")
    # end setRequestCurrents()
