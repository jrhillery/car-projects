
from __future__ import annotations

import dataclasses

from appdaemon import ADAPI
from appdaemon.entity import Entity


@dataclasses.dataclass
class CarDetails:
    """Details of a vehicle as reported by Tessie"""
    TESLA_APP_REQ_MIN_AMPS = 5

    vehicleName: str
    shiftStateEntity: Entity
    chargeCurrentEntity: Entity
    chargeLimitEntity: Entity
    chargingState: str
    battLevel: float
    statusEntity: Entity
    savedLocation: str | None
    battCapacity: float

    @classmethod
    async def fromAdapi(cls, ad: ADAPI, vehicleName: str) -> CarDetails:
        """Gets the details of a vehicle.

        :param ad: AppDaemon api instance
        :param vehicleName: Name of the vehicle
        :return: CarDetails instance
        """
        chargingState: str = await ad.get_state(f"sensor.{vehicleName}_charging")
        batteryLevelState: str = await ad.get_state(f"sensor.{vehicleName}_battery_level")
        locationState: str = await ad.get_state(f"device_tracker.{vehicleName}_location")

        return cls(
            vehicleName=vehicleName,
            shiftStateEntity=ad.get_entity(f"sensor.{vehicleName}_shift_state"),
            chargeCurrentEntity=ad.get_entity(f"number.{vehicleName}_charge_current"),
            chargeLimitEntity=ad.get_entity(f"number.{vehicleName}_charge_limit"),
            chargingState=chargingState,
            battLevel=float(batteryLevelState),
            statusEntity=ad.get_entity(f"binary_sensor.{vehicleName}_status"),
            savedLocation=locationState,
            battCapacity=68.3,
        )
    # end fromAdapi(ADAPI, str)

    @property
    def displayName(self) -> str:
        return self.vehicleName.title()
    # end displayName()

    @property
    def chargeCurrentRequest(self) -> int:
        return int(float(self.chargeCurrentEntity.state) + 0.5)
    # end chargeCurrentRequest()

    @property
    def requestMaxAmps(self) -> int:
        return self.chargeCurrentEntity.attributes.get("max")
    # end requestMaxAmps()

    @property
    def chargeLimit(self) -> int:
        return int(float(self.chargeLimitEntity.state) + 0.5)
    # end chargeLimit()

    @property
    def status(self) -> str:
        return self.statusEntity.state
    # end status()

    def pluggedIn(self) -> bool:
        return self.chargingState != "disconnected"
    # end pluggedIn()

    def atHome(self) -> bool:
        return self.savedLocation == "home"
    # end atHome()

    def pluggedInAtHome(self) -> bool:
        return self.pluggedIn() and self.atHome()
    # end pluggedInAtHome()

    def inPark(self) -> bool:
        return self.shiftStateEntity.state == "p"
    # end inPark()

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
           - depends on having battery capacity
        :param plugInNeeded: The car needs to be plugged in at home to return non-zero
        :return: The energy needed
        """
        if not plugInNeeded or self.pluggedInAtHome():
            return self.chargeNeeded() * 0.01 * self.battCapacity
        else:
            return 0.0
    # end neededKwh(bool)

    def chargingStatusSummary(self, khwNeeded: float = 0.0) -> str:
        """Return a summary charging status suitable for display.

        :param khwNeeded: The kilowatt-hours below limit to include
        :return: Summary
        """
        parts: list[str] = [f"{self.displayName} was "]
        if self.inPark():
            parts.append(f"{self.status}line")
        else:
            parts.append("driving")
        if self.savedLocation:
            parts.append(f"@{self.savedLocation}")
        parts.append(f" {self.chargingState}")
        if self.pluggedIn():
            parts.append(f" {self.chargeCurrentRequest}/{self.requestMaxAmps}A")
        parts.append(f", limit {self.chargeLimit}%"
                     f" and battery {self.battLevel}%")
        if khwNeeded:
            parts.append(f" ({khwNeeded:.1f} kWh < limit)")

        return "".join(parts)
    # end chargingStatusSummary(float)

    def __str__(self) -> str:
        return f"{self.displayName}@{self.battLevel}%"
    # end __str__()

# end class CarDetails
