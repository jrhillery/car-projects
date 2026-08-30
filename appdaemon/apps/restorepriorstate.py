
from appdaemon import Hass

from tessie import CarDetails


class RestorePriorState(Hass):

    async def initialize(self):
        """Called when AppDaemon starts the app."""

        for vehicle in self.args.get("vehicles", []):
            if "name" not in vehicle:
                continue
            name = vehicle["name"].lower()
            capacity = float(vehicle["capacity"])
            batteryLevelAdded = float(vehicle["battery_level_added"])
            dtls = CarDetails.fromAdapi(self, name, 0)
            oldCapacity = dtls.batteryCapacity

            await dtls.setBatteryCapacity(capacity, batteryLevelAdded)

            self.log("%s working capacity estimate restored from %.2f to %.2f kWh",
                     dtls.displayName, oldCapacity, capacity)
