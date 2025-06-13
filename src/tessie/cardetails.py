
import logging
from asyncio import Task
from datetime import timedelta
from time import time

from util import SummaryStr


class CarDetails(object):
    """Details of a vehicle as reported by Tessie"""
    TESLA_APP_REQ_MIN_AMPS = 5

    # fields set in CarDetails.updateFromDict
    vin: str
    displayName: str
    shiftState: str
    chargeAmps: int
    chargeCurrentRequest: int
    requestMaxAmps: int
    chargeLimit: int
    limitMinPercent: int
    limitMaxPercent: int
    chargingState: str
    lastSeen: float
    outsideTemp: float
    updatedSinceSummary: bool
    modifiedBySetter: bool
    odometer: float

    # fields set in TessieInterface.addBattery
    battLevel: float
    energyLeft: float

    # field set in TessieInterface.addSleepStatus
    sleepStatus: str

    # field set in TessieInterface.addLocation
    savedLocation: str | None

    # field set in AutoCurrentControl.setBatteryCapacity and TessieInterface.addBatteryHealth
    battCapacity: float

    def __init__(self, vehicleState: dict):
        """Initialize this instance and allocate resources"""
        self.updateFromDict(vehicleState)
        self.wakeTask: Task | None = None
    # end __init__(dict)

    def updateFromDict(self, vehicleData: dict) -> None:
        """Populate details of this vehicle

        :param vehicleData: Dictionary of Tessie JSON data
        """
        self.vin = vehicleData["vin"]
        self.displayName = vehicleData["display_name"]
        driveState = vehicleData["drive_state"]
        self.shiftState = driveState["shift_state"]
        chargeState = vehicleData["charge_state"]
        self.chargeAmps = chargeState["charge_amps"]
        self.chargeCurrentRequest = chargeState["charge_current_request"]
        self.requestMaxAmps = chargeState["charge_current_request_max"]
        self.chargeLimit = chargeState["charge_limit_soc"]
        self.limitMinPercent = chargeState["charge_limit_soc_min"]
        self.limitMaxPercent = chargeState["charge_limit_soc_max"]
        self.chargingState = chargeState["charging_state"]
        self.lastSeen = chargeState["timestamp"] * 0.001  # convert ms to seconds
        climateState = vehicleData["climate_state"]
        self.outsideTemp = climateState["outside_temp"]
        self.updatedSinceSummary = True
        self.modifiedBySetter = False
        vehicleState = vehicleData["vehicle_state"]
        self.odometer = vehicleState["odometer"]
    # end updateFromDict(dict)

    def setChargeCurrentRequest(self, reqCurrent: int) -> None:
        self.chargeCurrentRequest = reqCurrent
        self.modifiedBySetter = True
    # end setChargeCurrentRequest(int)

    def setChargeLimit(self, percent: int) -> None:
        self.chargeLimit = percent
        self.modifiedBySetter = True
    # end setChargeLimit(int)

    def setChargingState(self, state: str) -> None:
        self.chargingState = state
        self.modifiedBySetter = True
    # end setChargingState(str)

    def pluggedIn(self) -> bool:
        return self.chargingState != "Disconnected"
    # end pluggedIn()

    def atHome(self) -> bool:
        return self.savedLocation == "Home"
    # end atHome()

    def pluggedInAtHome(self) -> bool:
        return self.pluggedIn() and self.atHome()
    # end pluggedInAtHome()

    def inPark(self) -> bool:
        return self.shiftState == "P"
    # end inPark()

    def awake(self) -> bool:
        return self.sleepStatus == "awake"
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

    def chargeLimitIsMin(self) -> bool:
        return self.chargeLimit == self.limitMinPercent
    # end chargeLimitIsMin()

    def limitChargeLimit(self, chargeLimit: int) -> int:
        """Return a charge limit within the valid range for this vehicle
        :param chargeLimit: The proposed charge limit
        :return: The nearest valid charge limit
        """
        if chargeLimit < self.limitMinPercent:
            logging.info(f"{chargeLimit}% is too small for {self.displayName}"
                         f" -- minimum is {self.limitMinPercent}%")
            chargeLimit = self.limitMinPercent

        if chargeLimit > self.limitMaxPercent:
            logging.info(f"{chargeLimit}% is too large for {self.displayName}"
                         f" -- maximum is {self.limitMaxPercent}%")
            chargeLimit = self.limitMaxPercent

        return chargeLimit
    # end limitChargeLimit(int)

    def chargeNeeded(self, chargeLimit: int | None = None) -> float:
        """Return the percent increase the battery needs to reach its charge limit
        :param chargeLimit: The charge limit to use, defaulting to existing charge limit
        :return: The percent increase needed
        """
        if chargeLimit is None:
            chargeLimit = self.chargeLimit

        if chargeLimit <= self.battLevel:
            return 0.0
        else:
            return chargeLimit - self.battLevel
    # end chargeNeeded(int | None)

    def energyNeededC(self, chargeLimit: int | None = None, plugInNeeded = True) -> float:
        """Return the energy needed to reach the charge limit, in kWh
           - this estimate is based on the reported battery charge level
           - depends on having battery capacity
        :param chargeLimit: The charge limit to use, defaulting to existing charge limit
        :param plugInNeeded: The car needs to be plugged in at home to return non-zero
        :return: The energy needed
        """
        if not plugInNeeded or self.pluggedInAtHome():
            return self.chargeNeeded(chargeLimit) * 0.01 * self.battCapacity
        else:
            return 0.0
    # end energyNeededC(int | None, bool)

    def dataAge(self) -> float:
        """Return the age of this CarDetails' data in seconds"""
        deltaSecs = time() - self.lastSeen

        if deltaSecs < 0.0:
            # our clock must not have been synchronized with the car's
            deltaSecs = 0.0

        return deltaSecs
    # end dataAge()

    def chargingStatusSummary(self, energyNeeded: float = 0.0) -> SummaryStr:
        """Return a summary charging status suitable for display
        :param energyNeeded: The kilowatt-hours below limit to include
        :return: Summary
        """
        parts: list[str] = [f"{self.displayName} was "]
        if self.inPark():
            parts.append(self.sleepStatus)
        else:
            parts.append("driving")
        if self.savedLocation:
            parts.append(f"@{self.savedLocation}")
        parts.append(f" {self.outsideTemp}\u00B0"
                     f" {timedelta(seconds=int(self.dataAge() + 0.5))} ago"
                     f" {self.chargingState}")
        if self.pluggedIn():
            parts.append(f" {self.chargeCurrentRequest}/{self.requestMaxAmps}A")
        parts.append(f", limit {self.chargeLimit}%"
                     f" and battery {self.battLevel}%")
        if energyNeeded:
            parts.append(f" ({energyNeeded:.1f} kWh < limit)")

        summary = SummaryStr("".join(parts), self.updatedSinceSummary)
        self.updatedSinceSummary = False

        return summary
    # end chargingStatusSummary(float)

    def __str__(self) -> str:
        return f"{self.displayName}@{self.battLevel}%"
    # end __str__()

# end class CarDetails
