
from __future__ import annotations

import dataclasses

from appdaemon import ADAPI
from appdaemon.entity import Entity


@dataclasses.dataclass
class CarDetails:
    """Details of a vehicle as reported by Tessie"""
    TESLA_APP_REQ_MIN_AMPS = 5

    vehicleName: str
    shiftStateSensor: Entity
    chargeCurrentNumber: Entity
    chargeLimitNumber: Entity
    chargingSensor: Entity
    batteryLevelSensor: Entity
    statusDetector: Entity
    locationTracker: Entity
    battCapacity: float
    chargeCableDetector: Entity
    chargeSwitch: Entity
    wakeButton: Entity

    @classmethod
    def fromAdapi(cls, ad: ADAPI, vehicleName: str) -> CarDetails:
        """Gets the details of a vehicle.

        :param ad: AppDaemon api instance
        :param vehicleName: Name of the vehicle
        :return: CarDetails instance
        """

        return cls(
            vehicleName=vehicleName,
            shiftStateSensor=ad.get_entity(f"sensor.{vehicleName}_shift_state"),
            chargeCurrentNumber=ad.get_entity(f"number.{vehicleName}_charge_current"),
            chargeLimitNumber=ad.get_entity(f"number.{vehicleName}_charge_limit"),
            chargingSensor=ad.get_entity(f"sensor.{vehicleName}_charging"),
            batteryLevelSensor=ad.get_entity(f"sensor.{vehicleName}_battery_level"),
            statusDetector=ad.get_entity(f"binary_sensor.{vehicleName}_status"),
            locationTracker=ad.get_entity(f"device_tracker.{vehicleName}_location"),
            battCapacity=68.3,
            chargeCableDetector=ad.get_entity(f"binary_sensor.{vehicleName}_charge_cable"),
            chargeSwitch=ad.get_entity(f"switch.{vehicleName}_charge"),
            wakeButton=ad.get_entity(f"button.{vehicleName}_wake"),
        )
    # end fromAdapi(ADAPI, str)

    @property
    def displayName(self) -> str:
        return self.vehicleName.title()
    # end displayName()

    @property
    def chargeCurrentRequest(self) -> int:
        return int(float(self.chargeCurrentNumber.state) + 0.5)
    # end chargeCurrentRequest()

    @property
    def requestMaxAmps(self) -> int:
        return self.chargeCurrentNumber.attributes.get("max")
    # end requestMaxAmps()

    @property
    def chargeLimit(self) -> int:
        return int(float(self.chargeLimitNumber.state) + 0.5)
    # end chargeLimit()

    @property
    def battLevel(self) -> float:
        return float(self.batteryLevelSensor.state)
    # end battLevel()

    @property
    def status(self) -> str:
        return self.statusDetector.state
    # end status()

    @property
    def savedLocation(self) -> str:
        return self.locationTracker.state
    # end savedLocation()

    def pluggedIn(self) -> bool:
        return self.chargingSensor.state != "disconnected"
    # end pluggedIn()

    def atHome(self) -> bool:
        return self.savedLocation == "home"
    # end atHome()

    def pluggedInAtHome(self) -> bool:
        return self.pluggedIn() and self.atHome()
    # end pluggedInAtHome()

    def chargingAtHome(self) -> bool:
        return self.chargingSensor.state == "charging" and self.atHome()
    # end chargingAtHome()

    def inPark(self) -> bool:
        return self.shiftStateSensor.state == "p"
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
        parts.append(f" {self.chargingSensor.state}")
        if self.pluggedIn():
            parts.append(f" {self.chargeCurrentRequest}/{self.requestMaxAmps}A")
        parts.append(f", limit {self.chargeLimit}%"
                     f" and battery {self.battLevel:.0f}%")
        if khwNeeded:
            parts.append(f" ({khwNeeded:.1f} kWh < limit)")

        return "".join(parts)
    # end chargingStatusSummary(float)

    def __str__(self) -> str:
        return f"{self.displayName}@{self.battLevel}%"
    # end __str__()

# end class CarDetails
