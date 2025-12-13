# python_scripts/set_request_currents.py
"""Automatically set cars' request currents based on each cars' charging needs."""

messages = []
TESLA_APP_REQ_MIN_AMPS = 5


def getCarDetails(vehicleName: str):
    """Get details of a vehicle and return as a dictionary."""
    vehicle_name = vehicleName.lower()
    shiftStateState = hass.states.get(f"sensor.{vehicle_name}_shift_state")
    chargeCurrentEntityId = f"number.{vehicle_name}_charge_current"
    chargeCurrentState = hass.states.get(chargeCurrentEntityId)
    chargeLimitState = hass.states.get(f"number.{vehicle_name}_charge_limit")
    chargingState = hass.states.get(f"sensor.{vehicle_name}_charging")
    batteryLevelState = hass.states.get(f"sensor.{vehicle_name}_battery_level")
    locationState = hass.states.get(f"device_tracker.{vehicle_name}_location")

    return {
        "displayName": vehicleName,
        "shiftState": shiftStateState.state,
        "chargeCurrentEntityId": chargeCurrentEntityId,
        "chargeCurrentRequest": int(float(chargeCurrentState.state) + 0.5),
        "requestMaxAmps": chargeCurrentState.attributes.get("max"),
        "chargeLimit": int(float(chargeLimitState.state) + 0.5),
        "chargingState": chargingState.state,
        "battLevel": float(batteryLevelState.state),
        "savedLocation": locationState.state if locationState else None,
        "battCapacity": 68.3,
    }


def pluggedIn(details) -> bool:
    return details["chargingState"] != "disconnected"


def pluggedInAtHome(details) -> bool:
    return pluggedIn(details) and details["savedLocation"] == "home"


def limitRequestCurrent(details, requestCurrent: int) -> int:
    """Return a request current that does not exceed limits.

       - the charge adapter's maximum
       - the minimum supported by Tesla's app
    :param details: Details of the vehicle to check
    :param requestCurrent: Desired request current (amps)
    :return: Nearest valid request current
    """
    if requestCurrent > details["requestMaxAmps"]:
        requestCurrent = details["requestMaxAmps"]

    if requestCurrent < TESLA_APP_REQ_MIN_AMPS and pluggedInAtHome(details):
        requestCurrent = TESLA_APP_REQ_MIN_AMPS

    return requestCurrent


def chargeNeeded(details) -> float:
    """Return the percent increase the battery needs to reach its charge limit.

    :param details: Details of the vehicle to check
    :return: The percent increase needed
    """
    chargeLimit = details["chargeLimit"]

    if chargeLimit <= details["battLevel"]:
        return 0.0

    return chargeLimit - details["battLevel"]


def neededKwh(details, plugInNeeded=True) -> float:
    """Return the energy needed to reach the charge limit, in kWh.

    :param details: Details of the vehicle to check
    :param plugInNeeded: The car needs to be plugged in at home to return non-zero
    :return: The energy needed
    """
    if plugInNeeded and not pluggedInAtHome(details):
        return 0.0

    return chargeNeeded(details) * 0.01 * details["battCapacity"]


def chargingStatusSummary(details, kwhNeeded: float = 0.0) -> str:
    """Return a summary charging status suitable for display.

    :param details: Details of the vehicle to check
    :param kwhNeeded: The kilowatt-hours below limit to include
    :return: Summary str
    """
    parts = [f"{details['displayName']} was "]
    if details["shiftState"] == "p":
        parts.append("parked")
    else:
        parts.append("driving")
    if details["savedLocation"]:
        parts.append(f"@{details['savedLocation']}")
    parts.append(f" {details['chargingState']}")
    if pluggedIn(details):
        parts.append(f" {details['chargeCurrentRequest']}/{details['requestMaxAmps']}A")
    parts.append(
        f", limit {details['chargeLimit']}% and battery {details['battLevel']:.0f}%"
    )
    if kwhNeeded:
        parts.append(f" ({kwhNeeded:.1f} kWh < limit)")

    return "".join(parts)


def logMsg(message: str) -> None:
    """Log an info level message and add it to our list.

    :param message: Message to include
    """
    logger.info(message)
    messages.append(message)


vehicleNames = data.get("vehicles", [])
totalCurrent = data.get("totalCurrent", 32)

# Get details for all vehicles
vehicles = [getCarDetails(vehicleName) for vehicleName in vehicleNames]

# Calculate energy needed for each vehicle
energiesNeeded = []
totalEnergyNeeded = 0.0

for dtls in vehicles:
    energyNeeded = neededKwh(dtls)

    if energyNeeded:
        logMsg(chargingStatusSummary(dtls, energyNeeded))

    energiesNeeded.append(energyNeeded)
    totalEnergyNeeded += energyNeeded
# end for

# Calculate request currents based on energy needs
if totalEnergyNeeded:
    reqCurrents = [
        totalCurrent * (energy / totalEnergyNeeded) for energy in energiesNeeded
    ]
else:
    reqCurrent = totalCurrent / len(vehicles)
    reqCurrents = [reqCurrent] * len(vehicles)

reqCurrents = [
    limitRequestCurrent(dtls, int(reqCurrents[i] + 0.5))
    for i, dtls in enumerate(vehicles)
]
indices = list(range(len(vehicles)))

# To decrease first, sort indices ascending by increase in request current
indices.sort(key=lambda i: reqCurrents[i] - vehicles[i]["chargeCurrentRequest"])

for idx in indices:
    dtls = vehicles[idx]
    if pluggedInAtHome(dtls):
        reqCurrent = reqCurrents[idx]
        if reqCurrent != dtls["chargeCurrentRequest"]:
            hass.services.call(
                "number",
                "set_value",
                {"entity_id": dtls["chargeCurrentEntityId"], "value": reqCurrent},
            )

            logMsg(
                f"{dtls['displayName']} request current"
                f" changing from {dtls['chargeCurrentRequest']} to {reqCurrent} A"
            )
        else:
            logMsg(
                f"{dtls['displayName']} request current already set to {reqCurrent} A"
            )
    else:
        logger.info(chargingStatusSummary(dtls))
# end for

if messages:
    hass.services.call(
        "persistent_notification",
        "create",
        {"title": "Set Cars' Request Currents", "message": "\n".join(messages)},
    )
