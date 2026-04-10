# ========== battery_sensor.py ==========
# Simulates the Battery Management System (BMS) of a home battery like a Tesla Powerwall.
# The BMS tracks the state of charge (SoC) — basically how full the battery is as a %.
# In a real system the BMS would talk to the inverter over CAN bus or Modbus,
# but here we just model the charge/discharge behaviour over time.

import random
from datetime import datetime

from base_sensor import BaseSensor


class BatterySensor(BaseSensor):
    def __init__(self, home_id, dispatch_rate=5):
        super().__init__(
            home_id=home_id,
            sensor_type="battery_storage",
            unit="percent",
            dispatch_rate=dispatch_rate
        )

        # Start at 50% — a reasonable assumption for the beginning of a simulation:
        self.current_charge = 50.0

        self.charge_rate = 0.3    # % gained per cycle during daylight (solar charging)
        self.discharge_rate = 0.2  # % lost per cycle at night (household loads)

        # Safety limits — real Powerwalls don't go below ~5% or above ~95-100% to protect battery health. I'm using 5-98 as my limits:
        self.min_charge = 5.0
        self.max_charge = 98.0

    def get_reading(self):
        now = datetime.now()
        hour = now.hour

        # During daylight hours (6am-8pm) assume the solar panels are charging the battery. At night the battery is discharging to cover household loads:
        if 6 <= hour < 20:
            # Charging — add charge_rate per cycle:
            self.current_charge += self.charge_rate
        else:
            # Discharging overnight:
            self.current_charge -= self.discharge_rate

        # Add a small random fluctuation to make it look more realistic. In a real BMS there's always some measurement noise:
        fluctuation = random.uniform(-0.05, 0.05)
        self.current_charge += fluctuation

        # Clamp to the safe operating range:
        self.current_charge = max(self.min_charge, min(self.max_charge, self.current_charge))

        return self.current_charge


if __name__ == "__main__":
    sensor = BatterySensor(home_id="home_1")
    sensor.run()
