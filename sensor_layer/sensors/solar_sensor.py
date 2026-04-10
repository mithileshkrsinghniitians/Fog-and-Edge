# ========== solar_sensor.py ==========
# Simulates a solar inverter (like the ones made by SMA or Fronius).
# The key thing about solar output is it follows the sun — roughly a sine curve
# throughout the day, peaking around noon and dropping to zero at night.

import math
import random
from datetime import datetime

from base_sensor import BaseSensor


class SolarSensor(BaseSensor):
    def __init__(self, home_id, dispatch_rate=5):
        super().__init__(
            home_id=home_id,
            sensor_type="solar_panel",
            unit="kW",
            dispatch_rate=dispatch_rate
        )

        self.peak_output = 4.5  # max kW output at solar noon (from config):

        # Solar is only producing between these hours. 6am sunrise, 8pm it's basically dark:
        self.sunrise_hour = 6
        self.sunset_hour = 20

    def get_reading(self):
        now = datetime.now()
        hour = now.hour + now.minute / 60

        # If it's outside daylight hours, output is zero:
        if hour < self.sunrise_hour or hour >= self.sunset_hour:
            return 0.0

        # Use a sine curve to model the sun's position in the sky:
        # At sunrise (hour 6) the angle is 0, at noon (hour 13) it peaks, at sunset (hour 20) it's back to 0. The formula maps the current hour into a 0–π range.
        daylight_length = self.sunset_hour - self.sunrise_hour  # = 14 hours
        angle = math.pi * (hour - self.sunrise_hour) / daylight_length
        sun_intensity = math.sin(angle)  # goes from 0 at sunrise to 1 at noon to 0 at sunset

        # Cloud factor adds some randomness — on a partly cloudy day you get 75-100% of max:
        # A value of 1.0 means clear sky, 0.75 means some cloud cover cutting output.
        cloud_factor = random.uniform(0.75, 1.0)

        output = self.peak_output * sun_intensity * cloud_factor

        return output


# Quick test — run this file directly to see sample readings throughout the day:
if __name__ == "__main__":
    sensor = SolarSensor(home_id="home_1")
    sensor.run()
