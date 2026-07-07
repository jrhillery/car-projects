
from appdaemon import Hass

from tessie import CarDetails


class RestorePriorState(Hass):

    def initialize(self):
        vehicleName = self.args["vehicleName"].lower()
        capacity = str(self.args["capacity"])
        dtls = CarDetails.fromAdapi(self, vehicleName, 0)
        oldCapacity = dtls.batteryCapacity

        dtls.batteryCapacitySensor.set_state(
            capacity, attributes={"battery_level_added": dtls.persistedBatteryLevelAdded})
        self.log("%s working capacity estimate restored from %.2f to %s kWh",
                 dtls.displayName, oldCapacity, capacity)
