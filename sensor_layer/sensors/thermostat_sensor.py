# thermostat_sensor.py
# Simulates a smart thermostat like a Nest or Tado.
# The temperature doesn't jump instantly — it drifts slowly toward the target
# temperature set by the schedule. This is more realistic because it takes
# time for a room to heat up or cool down.
#
# Tado and Nest both use time-based schedules to set target temperatures,
# which is exactly what I'm modelling here with morning/daytime/evening/night targets.

import random
from datetime import datetime

from base_sensor import BaseSensor


class ThermostatSensor(BaseSensor):
    def __init__(self, home_id, dispatch_rate=5):
        super().__init__(
            home_id=home_id,
            sensor_type="temperature",
            unit="celsius",
            dispatch_rate=dispatch_rate
        )

        # Start at a neutral temperature — the house might be at whatever
        # temperature it was when we began the simulation.
        self.current_temp = 19.0

        # How fast the temperature drifts toward the target each cycle.
        # A factor of 0.05 means it moves 5% of the gap per cycle — so it
        # approaches the target gradually rather than jumping there instantly.
        self.drift_factor = 0.05

        # Temperature schedule (same idea as how Tado/Nest schedules work)
        self.schedule = {
            "night":   {"hours": range(0, 7),   "target": 17.0},  # sleeping — turn it down
            "morning": {"hours": range(7, 9),    "target": 21.0},  # getting up — warm it up
            "day":     {"hours": range(9, 17),   "target": 19.0},  # out at work — no need to heat
            "evening": {"hours": range(17, 23),  "target": 21.0},  # home and relaxing
            "late":    {"hours": range(23, 24),  "target": 17.0},  # going to bed
        }

    def _get_target_temp(self):
        # Look up what the target temperature should be for the current hour
        hour = datetime.now().hour
        for period, settings in self.schedule.items():
            if hour in settings["hours"]:
                return settings["target"]
        # Fallback — shouldn't happen but just in case
        return 19.0

    def get_reading(self):
        target = self._get_target_temp()

        # Drift the current temperature toward the target.
        # The further away we are from the target, the bigger the step.
        gap = target - self.current_temp
        self.current_temp += gap * self.drift_factor

        # Add a tiny bit of sensor noise — real thermistors have measurement variation.
        # ±0.1°C is realistic for a decent smart thermostat sensor.
        noise = random.uniform(-0.1, 0.1)
        self.current_temp += noise

        return self.current_temp


if __name__ == "__main__":
    sensor = ThermostatSensor(home_id="home_1")
    sensor.run()
