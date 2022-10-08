
from datetime import timedelta
from time import time


class CarDetails(object):
    """Details of a vehicle as reported by Tessie"""

    sleepStatus: str
    vin: str
    displayName: str
    chargeState: dict
    batteryLevel: int
    chargeAmps: int
    chargeLimit: int
    limitMinPercent: int
    chargingState: str
    lastSeen: float

    def __init__(self, sleepStatus: str, vehicleState: dict):
        self.updateFromDict(sleepStatus, vehicleState)
    # end __init__(str, dict)

    def updateFromDict(self, sleepStatus: str, vehicleState: dict) -> None:
        self.sleepStatus = sleepStatus
        self.vin = vehicleState["vin"]
        self.displayName = vehicleState["display_name"]
        self.chargeState = vehicleState["charge_state"]
        self.batteryLevel = self.chargeState["battery_level"]
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
        if self.chargeLimit <= self.batteryLevel:
            return 0
        else:
            return self.chargeLimit - self.batteryLevel
    # end chargeNeeded()

    def currentChargingStatus(self) -> str:
        return (f"{self.displayName} was {self.sleepStatus}"
                f" {timedelta(seconds=int(time() - self.lastSeen + 0.5))} ago"
                f" with charging {self.chargingState}"
                f", charge limit {self.chargeLimit}%"
                f" and battery {self.batteryLevel}%")
    # end currentChargingStatus()

    def __str__(self) -> str:
        return f"{self.displayName}@{self.batteryLevel}%"
    # end __str__()

# end class CarDetails
