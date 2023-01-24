
import logging
from asyncio import Task
from datetime import timedelta
from time import time

from util import SummaryStr


class CarDetails(object):
    """Details of a vehicle as reported by Tessie"""

    # fields set in CarDetails.updateFromDict
    vin: str
    displayName: str
    battLevel: int
    battRange: float
    chargeAmps: int
    chargeCurrentRequest: int
    chargeLimit: int
    limitMinPercent: int
    limitMaxPercent: int
    chargingState: str
    lastSeen: float
    updatedSinceSummary: bool

    # field set in TessieInterface.addSleepStatus
    sleepStatus: str

    # field set in TessieInterface.addLocation
    savedLocation: str | None

    # fields set in TessieInterface.addBatteryHealth
    battMaxRange: float
    battCapacity: float

    def __init__(self, vehicleState: dict):
        """Initialize this instance and allocate resources"""
        self.updateFromDict(vehicleState)
        self.wakeTask: Task | None = None
    # end __init__(dict)

    def updateFromDict(self, vehicleState: dict) -> None:
        """Populate details of this vehicle
        :param vehicleState: Dictionary of Tessie JSON data
        """
        self.vin = vehicleState["vin"]
        self.displayName = vehicleState["display_name"]
        chargeState = vehicleState["charge_state"]
        self.battLevel = chargeState["battery_level"]
        self.battRange = chargeState["battery_range"]
        self.chargeAmps = chargeState["charge_amps"]
        self.chargeCurrentRequest = chargeState["charge_current_request"]
        self.chargeLimit = chargeState["charge_limit_soc"]
        self.limitMinPercent = chargeState["charge_limit_soc_min"]
        self.limitMaxPercent = chargeState["charge_limit_soc_max"]
        self.chargingState = chargeState["charging_state"]
        self.lastSeen = chargeState["timestamp"] * 0.001  # convert ms to seconds
        self.updatedSinceSummary = True
    # end updateFromDict(dict)

    def pluggedIn(self) -> bool:
        return self.chargingState != "Disconnected"
    # end pluggedIn()

    def atHome(self) -> bool:
        return self.savedLocation == "Home"
    # end atHome()

    def pluggedInAtHome(self) -> bool:
        return self.pluggedIn() and self.atHome()
    # end pluggedInAtHome()

    def awake(self) -> bool:
        return self.sleepStatus == "awake"
    # end awake()

    def chargeLimitIsMin(self) -> bool:
        return self.chargeLimit == self.limitMinPercent
    # end chargeLimitIsMin()

    def limitToCapabilities(self, chargeLimit: int) -> int:
        """Return a charge limit within the valid range for this vehicle
        :param chargeLimit: The proposed charge limit
        :return: The nearest valid charge limit
        """
        if chargeLimit < self.limitMinPercent:
            logging.info(f"{chargeLimit}% is too small for {self.displayName}"
                         f" -- minimum is {self.limitMinPercent}%")
            return self.limitMinPercent

        if chargeLimit > self.limitMaxPercent:
            logging.info(f"{chargeLimit}% is too large for {self.displayName}"
                         f" -- maximum is {self.limitMaxPercent}%")
            return self.limitMaxPercent

        return chargeLimit
    # end limitToCapabilities(int)

    def chargeNeeded(self) -> int:
        """Return the percent increase the battery needs to reach its charge limit"""
        if self.chargeLimit <= self.battLevel:
            return 0
        else:
            return self.chargeLimit - self.battLevel
    # end chargeNeeded()

    def rangeNeeded(self) -> float:
        """Return the range increase the battery needs to reach its charge limit"""
        rangeLimit = self.chargeLimit * self.battRange / self.battLevel

        if rangeLimit <= self.battRange:
            return 0.0
        else:
            return rangeLimit - self.battRange
    # end rangeNeeded()

    def energyNeededC(self) -> float:
        """Return the energy needed to reach the charge limit, in kWh
           - this estimate is based on the reported battery charge level"""
        if self.pluggedInAtHome():
            return self.chargeNeeded() * 0.01 * self.battCapacity
            # no improvement from: self.rangeNeeded() / self.battMaxRange * self.battCapacity
        else:
            return 0.0
    # end energyNeededC()

    def dataAge(self) -> float:
        """Return the age of this CarDetails' data in seconds"""
        deltaSecs = time() - self.lastSeen

        if deltaSecs < 0.0:
            # our clock must not have been synchronized with the car's
            deltaSecs = 0.0

        return deltaSecs
    # end dataAge()

    def chargingStatusSummary(self) -> SummaryStr:
        """Return a summary charging status suitable for display"""
        summary = SummaryStr(f"{self.displayName} was {self.sleepStatus}"
                             f" {timedelta(seconds=int(self.dataAge() + 0.5))} ago"
                             f" with charging {self.chargingState}"
                             f", limit {self.chargeLimit}%"
                             f" and battery {self.battLevel}%",
                             self.updatedSinceSummary)
        self.updatedSinceSummary = False

        return summary
    # end chargingStatusSummary()

    def __str__(self) -> str:
        return f"{self.displayName}@{self.battLevel}%"
    # end __str__()

# end class CarDetails
