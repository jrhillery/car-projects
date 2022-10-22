
from datetime import timedelta
from time import time


class CarDetails(object):
    """Details of a vehicle as reported by Tessie"""

    vin: str
    displayName: str
    chargeState: dict
    battLevel: int
    battRange: float
    chargeAmps: int
    chargeLimit: int
    limitMinPercent: int
    chargingState: str
    lastSeen: float

    sleepStatus: str
    battMaxRange: float
    battCapacity: float

    def __init__(self, vehicleState: dict):
        self.updateFromDict(vehicleState)
    # end __init__(str, dict)

    def updateFromDict(self, vehicleState: dict) -> None:
        self.vin = vehicleState["vin"]
        self.displayName = vehicleState["display_name"]
        self.chargeState = vehicleState["charge_state"]
        self.battLevel = self.chargeState["battery_level"]
        self.battRange = self.chargeState["battery_range"]
        self.chargeAmps = self.chargeState["charge_amps"]
        self.chargeLimit = self.chargeState["charge_limit_soc"]
        self.limitMinPercent = self.chargeState["charge_limit_soc_min"]
        self.chargingState = self.chargeState["charging_state"]
        self.lastSeen = self.chargeState["timestamp"] * 0.001  # convert ms to seconds
    # end updateFromDict(str, dict)

    def pluggedIn(self) -> bool:
        return self.chargingState != "Disconnected"
    # end pluggedIn()

    def awake(self) -> bool:
        return self.sleepStatus == "awake"
    # end awake()

    def chargeLimitIsMin(self) -> bool:
        return self.chargeLimit == self.limitMinPercent
    # end chargeLimitIsMin()

    def chargeNeeded(self) -> int:
        """return the percent increase the battery needs to reach its charge limit"""
        if self.chargeLimit <= self.battLevel:
            return 0
        else:
            return self.chargeLimit - self.battLevel
    # end chargeNeeded()

    def rangeNeeded(self) -> float:
        """return the range increase the battery needs to reach its charge limit"""
        rangeLimit = self.chargeLimit * 0.01 * self.battMaxRange

        if rangeLimit <= self.battRange:
            return 0.0
        else:
            return rangeLimit - self.battRange
    # end rangeNeeded()

    def energyNeeded(self) -> float:
        """return the energy needed to reach the charge limit, in kWh"""
        if self.pluggedIn():
            return self.chargeNeeded() * 0.01 * self.battCapacity
            # also tried: self.rangeNeeded() / self.battMaxRange * self.battCapacity
        else:
            return 0.0
    # end energyNeeded()

    def currentChargingStatus(self) -> str:
        return (f"{self.displayName} was {self.sleepStatus}"
                f" {timedelta(seconds=int(time() - self.lastSeen + 0.5))} ago"
                f" with charging {self.chargingState}"
                f", limit {self.chargeLimit}%"
                f" and battery {self.battLevel}%")
    # end currentChargingStatus()

    def __str__(self) -> str:
        return f"{self.displayName}@{self.battLevel}%"
    # end __str__()

# end class CarDetails
