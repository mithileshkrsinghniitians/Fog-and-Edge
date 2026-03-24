# grid_sensor.py
# Simulates a smart electricity meter (like the Hildebrand Glow or SMETS2 meter).
# Grid import varies a lot depending on time of day — people use more power in the
# morning getting ready and in the evening cooking/watching TV.
# At night it drops to basically just standby loads.

import random
from datetime import datetime

from base_sensor import BaseSensor


class GridSensor(BaseSensor):
    def __init__(self, home_id, dispatch_rate=5):
        super().__init__(
            home_id=home_id,
            sensor_type="grid_import",
            unit="kW",
            dispatch_rate=dispatch_rate
        )

    def get_reading(self):
        now = datetime.now()
        hour = now.hour

        # Figure out what time period we're in and pick a realistic demand range.
        # These ranges are rough but they reflect typical Irish household demand patterns.

        if 0 <= hour < 6:
            # Late night / early morning — most things are off.
            # Just standby loads like fridges, routers, phone charging.
            low, high = 0.3, 1.0

        elif 6 <= hour < 9:
            # Morning peak — showers, kettles, toasters all going at once.
            low, high = 3.5, 6.0

        elif 9 <= hour < 17:
            # Daytime — quieter if people are at work.
            # Still some base load from heating, appliances etc.
            low, high = 1.5, 3.0

        elif 17 <= hour < 21:
            # Evening peak — cooking dinner, TV, lights, dishwasher.
            # This is usually the highest demand period of the day.
            low, high = 4.0, 7.0

        else:
            # Late evening winding down — 9pm onwards
            low, high = 0.8, 2.5

        base_reading = random.uniform(low, high)

        # Add a tiny bit of noise to make it look like a real meter reading
        # rather than just jumping between fixed values. Real meters have
        # slight fluctuations even when demand is stable.
        noise = random.uniform(-0.1, 0.1)

        reading = base_reading + noise

        # Make sure we never return a negative value — grid import can't go negative
        # (that would be export, which is a different sensor/meter)
        return max(0.0, reading)


if __name__ == "__main__":
    sensor = GridSensor(home_id="home_1")
    sensor.run()
