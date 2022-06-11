
from datetime import timedelta
from time import time


class CarDetails(object):
    """Details of a vehicle as reported by Tessie"""

    sleepStatus: str
    vin: str
    chargeState: dict
    displayName: str
    lastSeen: float
    chargingState: str
    chargeLimit: int
    limitMinPercent: int
    batteryLevel: int

    def __init__(self, sleepStatus: str, vehicleState: dict):
        self.updateFromDict(sleepStatus, vehicleState)
    # end __init__(str, dict)

    def updateFromDict(self, sleepStatus: str, vehicleState: dict) -> None:
        self.sleepStatus = sleepStatus
        self.vin = vehicleState["vin"]
        self.chargeState = vehicleState["charge_state"]
        self.displayName = vehicleState["display_name"]
        self.lastSeen = self.chargeState["timestamp"] * 0.001  # convert ms to seconds
        self.chargingState = self.chargeState["charging_state"]
        self.chargeLimit = self.chargeState["charge_limit_soc"]
        self.limitMinPercent = self.chargeState["charge_limit_soc_min"]
        self.batteryLevel = self.chargeState["usable_battery_level"]
    # end updateFromDict(str, dict)

    def pluggedIn(self) -> bool:
        return self.chargingState != "Disconnected"
    # end pluggedIn()

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
