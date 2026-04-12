# ========== ev_sensor.py ==========
# Simulates an EV charger (like a Zappi or Ohme wall box).
# The interesting thing about EV charging is it's not continuous — the car
# is only plugged in when someone's home. So I need to model a "session":
# the car arrives, charges for a few hours, then the session ends.
#
# In a real Zappi the charger talks to the home energy manager via CT clamps
# and MQTT. Here we're just simulating the power draw.

import random
from datetime import datetime, timedelta

from base_sensor import BaseSensor


class EVSensor(BaseSensor):
    def __init__(self, home_id, dispatch_rate=5):
        super().__init__(
            home_id=home_id,
            sensor_type="ev_charger",
            unit="kW",
            dispatch_rate=dispatch_rate
        )

        self.charging_rate = 7.4  # kW — standard 7.4kW single-phase home charger

        # Session tracking — is the car currently plugged in and charging:
        self.is_charging = False  # Tweak EV Charging to "True" -> [Actual value "False"]
        self.session_end_time = None  # Tweak with Session to "datetime.now() + timedelta(hours=999)" -> [Actual value "None"]

    def _try_start_session(self, hour):
        # The car might arrive home between 5pm and 8pm.
        # Each time we check (every dispatch_rate seconds), there's a 3% chance
        # the car arrives if it's within the arrival window and we're not already charging.
        if 17 <= hour <= 20: # Tweak for EV charging actual value [if 17 <= hour <= 20:]
            if random.random() < 0.03:  # 3% probability per cycle
                # Car just arrived — start a new charging session.
                # Session lasts between 2 and 8 hours (depends on how low the battery was).
                session_hours = random.uniform(2, 8)
                self.session_end_time = datetime.now() + timedelta(hours=session_hours)
                self.is_charging = True
                print(f"[{self.home_id}] [ev_charger] Car arrived! Charging session started for {session_hours:.1f} hours.")

    def get_reading(self):
        now = datetime.now()
        hour = now.hour

        if self.is_charging:
            # Check if the session has finished:
            if now >= self.session_end_time:
                self.is_charging = False
                self.session_end_time = None
                print(f"[{self.home_id}] [ev_charger] Charging session ended.")
                return 0.0
            else:
                # Still charging — drawing full power:
                return self.charging_rate

        else:
            # Not currently charging — check if car might arrive:
            self._try_start_session(hour)

            # If the car just arrived this cycle, start returning the charging rate:
            if self.is_charging:
                return self.charging_rate
            else:
                return 0.0  # charger is idle.


if __name__ == "__main__":
    sensor = EVSensor(home_id="home_1")
    sensor.run()
