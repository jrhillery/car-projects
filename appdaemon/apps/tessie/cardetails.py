
from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable

from appdaemon import ADAPI
from appdaemon.entity import Entity


@dataclasses.dataclass
class CarDetails:
    """Details of a vehicle as reported by Tessie"""
    TESLA_APP_REQ_MIN_AMPS = 5
    PERSISTENT_NS = "tessie_car_details_persist"

    vehicleName: str
    shiftStateSensor: Entity
    chargeCurrentNumber: Entity
    chargeLimitNumber: Entity
    chargingSensor: Entity
    batteryLevelSensor: Entity
    statusDetector: Entity
    locationTracker: Entity
    energyRemainingSensor: Entity
    chargeEnergyAddedSensor: Entity
    batteryCapacitySensor: Entity
    chargeStartPercent: float
    chargeCableDetector: Entity
    chargeSwitch: Entity
    wakeButton: Entity
    log: Callable[..., None]

    @classmethod
    def fromAdapi(cls, ad: ADAPI, vehicleName: str) -> CarDetails:
        """Gets the details of a vehicle.

        :param ad: AppDaemon api instance
        :param vehicleName: Name of the vehicle
        :return: CarDetails instance
        """
        batteryCapacitySensor = ad.get_entity(f"sensor.{vehicleName}_battery_capacity",
                                              cls.PERSISTENT_NS, check_existence=False)
        if not ad.entity_exists(batteryCapacitySensor.entity_id, cls.PERSISTENT_NS):
            # create this battery capacity entity
            batteryCapacitySensor.set_state(
                "0.0", attributes={"battery_level_added": 0.0}, check_existence=False)

        return cls(
            vehicleName=vehicleName,
            shiftStateSensor=ad.get_entity(f"sensor.{vehicleName}_shift_state"),
            chargeCurrentNumber=ad.get_entity(f"number.{vehicleName}_charge_current"),
            chargeLimitNumber=ad.get_entity(f"number.{vehicleName}_charge_limit"),
            chargingSensor=ad.get_entity(f"sensor.{vehicleName}_charging"),
            batteryLevelSensor=ad.get_entity(f"sensor.{vehicleName}_battery_level"),
            statusDetector=ad.get_entity(f"binary_sensor.{vehicleName}_status"),
            locationTracker=ad.get_entity(f"device_tracker.{vehicleName}_location"),
            energyRemainingSensor=ad.get_entity(f"sensor.{vehicleName}_energy_remaining"),
            chargeEnergyAddedSensor=ad.get_entity(f"sensor.{vehicleName}_charge_energy_added"),
            batteryCapacitySensor=batteryCapacitySensor,
            chargeStartPercent=-1.0,
            chargeCableDetector=ad.get_entity(f"binary_sensor.{vehicleName}_charge_cable"),
            chargeSwitch=ad.get_entity(f"switch.{vehicleName}_charge"),
            wakeButton=ad.get_entity(f"button.{vehicleName}_wake"),
            log=ad.log,
        )
    # end fromAdapi(ADAPI, str)

    @property
    def displayName(self) -> str:
        return self.vehicleName.title()
    # end displayName()

    @property
    def inPark(self) -> bool:
        """Whether the vehicle is in park or not."""
        return self.shiftStateSensor.state == "p"
    # end inPark()

    @property
    def chargeCurrentRequest(self) -> int:
        """The requested charge current in amps."""
        try:
            return int(float(self.chargeCurrentNumber.state) + 0.5)
        except ValueError as ve:
            self.logError("Invalid charge current %s: %s", ve.__class__.__name__, ve)
            return 0
    # end chargeCurrentRequest()

    @property
    def requestMaxAmps(self) -> int:
        """The maximum allowed requested charge current in amps."""
        try:
            return self.chargeCurrentNumber.attributes["max"]
        except KeyError as ke:
            self.logError("Missing max charge current: %s", ke)
            return 1
    # end requestMaxAmps()

    @property
    def chargeLimit(self) -> int:
        """The charge limit as percent of capacity."""
        try:
            return int(float(self.chargeLimitNumber.state) + 0.5)
        except ValueError as ve:
            self.logError("Invalid charge limit %s: %s", ve.__class__.__name__, ve)
            return 0
    # end chargeLimit()

    @property
    def chargingStatus(self) -> str:
        """Charging status of the vehicle.

        "starting", "charging", "stopped", "complete",
        "disconnected", "no_power", "unavailable" or "unknown"
        """
        return self.chargingSensor.state
    # end chargingStatus()

    @property
    def battLevel(self) -> float:
        """The battery level as percent of capacity."""
        try:
            return float(self.batteryLevelSensor.state)
        except ValueError as ve:
            self.logError("Invalid battery level %s: %s", ve.__class__.__name__, ve)
            return 0.0
    # end battLevel()

    @property
    def status(self) -> str:
        """Vehicle connectivity status.

        "on", "off", "unavailable" or "unknown"
        """
        return self.statusDetector.state
    # end status()

    @property
    def savedLocation(self) -> str:
        """Vehicle location.

        "home", zone name or "not_home"
        """
        return self.locationTracker.state
    # end savedLocation()

    @property
    def energyRemaining(self) -> float:
        """The remaining battery energy in kWh."""
        try:
            return float(self.energyRemainingSensor.state)
        except ValueError as ve:
            self.logError("Invalid energy remaining %s: %s", ve.__class__.__name__, ve)
            return 0.0
    # end energyRemaining()

    @property
    def energyAdded(self) -> float:
        """The charge energy added in kWh."""
        try:
            return float(self.chargeEnergyAddedSensor.state)
        except ValueError as ve:
            self.logError("Invalid energy added %s: %s", ve.__class__.__name__, ve)
            return 0.0
    # end energyAdded()

    @property
    def batteryCapacity(self) -> float:
        """The persisted estimated battery capacity in kWh."""
        try:
            return float(self.batteryCapacitySensor.state)
        except ValueError as ve:
            self.logError("Invalid battery capacity %s: %s", ve.__class__.__name__, ve)
            return 0.0
    # end batteryCapacity()

    @property
    def persistedBatteryLevelAdded(self) -> float:
        """The persisted battery level added as a fraction of capacity."""
        try:
            return self.batteryCapacitySensor.attributes["battery_level_added"]
        except ValueError as ve:
            self.logError("Invalid battery level added %s: %s", ve.__class__.__name__, ve)
            return 0.0
    # end persistedBatteryLevelAdded()

    def chargingStarting(self) -> None:
        """The charging process is starting."""
        self.chargeStartPercent = self.battLevel
    # end chargingStarting()

    async def updateBatteryCapacity(self) -> None:
        """Update the estimated battery capacity of the vehicle."""
        if self.chargeStartPercent < 0.0:
            self.logError("Unable to update estimated battery capacity"
                          " - app reinitialized since %s started charging", self.displayName)
            return
        batteryLevelAddedAccumulator = self.persistedBatteryLevelAdded
        energyAccumulator = self.batteryCapacity * batteryLevelAddedAccumulator
        batteryLevelAddedAccumulator += (self.battLevel - self.chargeStartPercent) / 100.0
        energyAccumulator += self.energyAdded
        capacity = energyAccumulator / batteryLevelAddedAccumulator

        await self.batteryCapacitySensor.set_state(
            str(capacity), attributes={"battery_level_added": batteryLevelAddedAccumulator})
        self.log("New estimated battery capacity: %.2f, total portion added: %.3f",
                 capacity, batteryLevelAddedAccumulator)
    # end updateBatteryCapacity()

    def logError(self, msg: str, *args, **kwargs) -> None:
        self.log(msg, *args, level=logging.ERROR, **kwargs)
    # end logError(str, *Any, **Any)

    def pluggedIn(self) -> bool:
        return self.chargingStatus != "disconnected"
    # end pluggedIn()

    def atHome(self) -> bool:
        return self.savedLocation == "home"
    # end atHome()

    def pluggedInAtHome(self) -> bool:
        return self.pluggedIn() and self.atHome()
    # end pluggedInAtHome()

    def chargingAtHome(self) -> bool:
        return self.chargingStatus in {"starting", "charging"} and self.atHome()
    # end chargingAtHome()

    def awake(self) -> bool:
        return self.status == "on"
    # end awake()

    def limitRequestCurrent(self, reqCurrent: int) -> int:
        """Return a request current that does not exceed
           - the charge adapter's maximum
           - the minimum supported by Tesla's app
        :param reqCurrent: Desired request current (amps)
        :return: Nearest valid request current
        """
        if reqCurrent > self.requestMaxAmps:
            reqCurrent = self.requestMaxAmps

        if reqCurrent < self.TESLA_APP_REQ_MIN_AMPS and self.pluggedInAtHome():
            reqCurrent = self.TESLA_APP_REQ_MIN_AMPS

        return reqCurrent
    # end limitRequestCurrent(int)

    def chargeNeeded(self) -> float:
        """Return the percent increase the battery needs to reach its charge limit.

        :return: The percent increase needed
        """
        chargeLimit = self.chargeLimit

        if chargeLimit <= self.battLevel:
            return 0.0
        else:
            return chargeLimit - self.battLevel
    # end chargeNeeded()

    def neededKwh(self, plugInNeeded = True) -> float:
        """Return the energy needed to reach the charge limit, in kWh
           - this estimate is based on the reported battery charge level
        :param plugInNeeded: The car needs to be plugged in at home to return non-zero
        :return: The energy needed
        """
        if not plugInNeeded or self.pluggedInAtHome():
            if self.batteryCapacity:
                return self.chargeNeeded() * 0.01 * self.batteryCapacity
            elif self.battLevel:
                return self.chargeNeeded() * self.energyRemaining / self.battLevel
            else:
                return 50.0
        else:
            return 0.0
    # end neededKwh(bool)

    def chargingStatusSummary(self, kwhNeeded: float = 0.0) -> str:
        """Return a summary charging status suitable for display.

        :param kwhNeeded: The kilowatt-hours below limit to include
        :return: Summary
        """
        parts: list[str] = [f"{self.displayName} was "]
        if self.inPark:
            parts.append(f"{self.status}line")
        else:
            parts.append("driving")
        if self.savedLocation:
            parts.append(f"@{self.savedLocation}")
        parts.append(f" {self.chargingStatus}")
        if self.pluggedIn():
            parts.append(f" {self.chargeCurrentRequest}/{self.requestMaxAmps}A")
        parts.append(f", limit {self.chargeLimit}%"
                     f" and battery {self.battLevel:.0f}%")
        if kwhNeeded:
            parts.append(f" (~{kwhNeeded:.1f} kWh < limit)")

        return "".join(parts)
    # end chargingStatusSummary(float)

    def __str__(self) -> str:
        return f"{self.displayName}@{self.battLevel}%"
    # end __str__()

# end class CarDetails
