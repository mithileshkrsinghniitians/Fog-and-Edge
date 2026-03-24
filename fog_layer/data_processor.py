# data_processor.py
#
# This is the intelligent part of the fog node — it takes the raw sensor readings
# that have been buffered over the last 30 seconds and turns them into something useful.
#
# There are three main things it does:
#   1. Validation  — throw out readings that are physically impossible
#   2. Aggregation — summarise 30 seconds of readings into one clean result
#   3. Mode detection — figure out what's actually happening in the home right now
#
# This is what makes fog computing interesting. A dumb gateway would just forward
# everything to the cloud. A fog node actually understands the data and adds context.
# By the time a result reaches AWS it already has an energy_mode label and an alert flag —
# the cloud doesn't need to re-derive that from thousands of raw numbers.

import os
from datetime import datetime, timezone


# Physical limits per sensor type.
# These are the outer bounds of what's physically possible given the hardware specs.
# Anything outside these ranges means either the sensor is faulty or we've got a
# simulation bug — either way, we shouldn't trust the reading.
VALIDATION_LIMITS = {
    "solar_panel":      {"min": 0.0,  "max": 6.0},    # kW — a typical residential system is 3-6kW
    "grid_import":      {"min": 0.0,  "max": 15.0},   # kW — 15kW would be an enormous house
    "battery_storage":  {"min": 0.0,  "max": 100.0},  # percent — SoC can't go below 0 or above 100
    "temperature":      {"min": 10.0, "max": 40.0},   # celsius — indoor ambient, so no extremes
    "ev_charger":       {"min": 0.0,  "max": 8.0},    # kW — 7.4kW charger + small headroom
}


class DataProcessor:

    def __init__(self):
        # fog_node_id identifies which fog device processed this data.
        # In a real deployment you might have multiple fog nodes (one per building floor,
        # or one per neighbourhood). The ID lets you trace where processing happened.
        self.fog_node_id = os.getenv("FOG_NODE_ID", "fog_node_01")

    # ── Validation ────────────────────────────────────────────────────────────

    def validate_reading(self, sensor_type, value):
        # Check whether a sensor reading makes physical sense.
        # Returns True if the reading is plausible, False if we should discard it.
        #
        # Why validate at the fog node and not just let the cloud handle it?
        # Because sending bad data to the cloud still costs money (IoT Core charges
        # per message) and it pollutes the database. Better to filter it here.

        if sensor_type not in VALIDATION_LIMITS:
            # We don't have rules for this sensor type — let it through rather
            # than silently dropping data from unknown sensors.
            return True

        limits = VALIDATION_LIMITS[sensor_type]

        # Solar panels physically cannot output negative power.
        # (A broken inverter might report -0.something but that's noise, not generation.)
        # They also can't exceed the panel rating — in our case we're using 6kW as the cap
        # because real 4.5kW panels can occasionally spike a bit on perfect conditions.
        if value < limits["min"] or value > limits["max"]:
            return False

        return True

    # ── Aggregation ───────────────────────────────────────────────────────────

    def aggregate(self, readings_buffer):
        # Takes the 30-second buffer and computes a summary per home per sensor.
        # The buffer looks like:
        #   { "home_1": { "solar_panel": [{value, unit, timestamp}, ...], ... }, ... }
        #
        # The output looks like:
        #   { "home_1": { "solar_panel": {avg, min, max, count, unit, invalid_count}, ... }, ... }
        #
        # Why avg/min/max instead of just avg?
        # Min/max over a 30-second window tells you a lot — a solar reading that averages
        # 2.1kW but has a max of 4.5kW shows the cloud passed over. Just the average hides that.

        aggregated = {}

        for home_id, sensors in readings_buffer.items():
            aggregated[home_id] = {}

            for sensor_type, readings in sensors.items():
                valid_values = []
                invalid_count = 0
                unit = "unknown"

                for reading in readings:
                    value = reading["value"]
                    unit  = reading.get("unit", "unknown")

                    if self.validate_reading(sensor_type, value):
                        valid_values.append(value)
                    else:
                        invalid_count += 1
                        print(f"[PROCESSOR] Discarded invalid reading — "
                              f"{home_id} / {sensor_type}: {value} {unit} "
                              f"(out of range {VALIDATION_LIMITS.get(sensor_type, {})})")

                if valid_values:
                    aggregated[home_id][sensor_type] = {
                        "avg":           round(sum(valid_values) / len(valid_values), 3),
                        "min":           round(min(valid_values), 3),
                        "max":           round(max(valid_values), 3),
                        "count":         len(valid_values),
                        "invalid_count": invalid_count,
                        "unit":          unit,
                    }
                else:
                    # All readings for this sensor were invalid — or the sensor
                    # sent nothing this window (e.g. EV charger was idle and sent 0s
                    # but they all passed validation, so this case is less common).
                    aggregated[home_id][sensor_type] = None

        return aggregated

    # ── Energy Mode Detection ─────────────────────────────────────────────────

    def detect_energy_mode(self, home_summary):
        # Look at the aggregated sensor averages for one home and decide what
        # energy "mode" the home is currently in. This is the fog node adding
        # intelligence — it's not just forwarding numbers, it's interpreting them.
        #
        # Think of it like how a Nest thermostat doesn't just report temperature —
        # it tells you "heating", "cooling", "away". We're doing the same for energy.
        #
        # Priority order matters here. A home can technically be in multiple modes
        # at once (e.g. EV charging AND battery low). We return the most important one.

        # Helper to safely pull the avg from a sensor result (handles None gracefully)
        def avg(sensor_type):
            result = home_summary.get(sensor_type)
            if result is None:
                return None
            return result.get("avg")

        solar_avg   = avg("solar_panel")
        grid_avg    = avg("grid_import")
        battery_avg = avg("battery_storage")
        ev_avg      = avg("ev_charger")

        # BATTERY_LOW — check first because it's the most urgent situation.
        # Below 15% the battery can't do much to buffer demand spikes.
        # A real energy management system (like GivEnergy or SolarEdge) would
        # trigger a "grid fallback" at around this level.
        if battery_avg is not None and battery_avg < 15.0:
            return "BATTERY_LOW"

        # EV_CHARGING — check before GRID_HEAVY because a car charging at 7.4kW
        # would naturally push grid import up. We don't want to flag GRID_HEAVY
        # when the real explanation is just an EV session that's running normally.
        if ev_avg is not None and ev_avg > 1.0:
            return "EV_CHARGING"

        # SOLAR_SURPLUS — the home is generating more from solar than it's pulling
        # from the grid. In a real system this is when you'd want to charge the battery,
        # start the dishwasher, or export to the grid for a feed-in tariff payment.
        if solar_avg is not None and grid_avg is not None and solar_avg > grid_avg:
            return "SOLAR_SURPLUS"

        # GRID_HEAVY — pulling a lot from the grid with no obvious explanation.
        # 5kW is roughly the threshold where a typical Irish home starts costing
        # noticeably on a flat tariff. On a night-rate meter you'd be less worried.
        if grid_avg is not None and grid_avg > 5.0:
            return "GRID_HEAVY"

        # NORMAL — nothing unusual, everything within expected ranges.
        return "NORMAL"

    # ── Main Process Method ───────────────────────────────────────────────────

    def process(self, readings_buffer):
        # This is what fog_node.py calls every 30 seconds.
        # It orchestrates validation → aggregation → mode detection and builds
        # the final processed payload that the cloud dispatcher will send to AWS.

        timestamp = datetime.now(timezone.utc).isoformat()

        # Step 1: aggregate all the raw readings into per-home summaries
        aggregated = self.aggregate(readings_buffer)

        results = []

        for home_id, home_summary in aggregated.items():

            # Step 2: figure out what mode this home is in right now
            energy_mode = self.detect_energy_mode(home_summary)

            # Step 3: decide whether to raise an alert.
            # Alert = something the homeowner or grid operator should know about.
            # BATTERY_LOW and GRID_HEAVY are the two situations we flag.
            alert = energy_mode in ("BATTERY_LOW", "GRID_HEAVY")

            # Step 4: check if any sensor had invalid readings this window.
            # If so, flag it — might indicate a faulty sensor worth investigating.
            has_bad_readings = any(
                s is not None and s.get("invalid_count", 0) > 0
                for s in home_summary.values()
            )

            # Build the complete payload for this home.
            # This is what ends up in DynamoDB — one record per home per 30s window.
            result = {
                "home_id":      home_id,
                "fog_node_id":  self.fog_node_id,
                "timestamp":    timestamp,
                "energy_mode":  energy_mode,
                "alert":        alert or has_bad_readings,
                "sensors":      home_summary,
            }

            results.append(result)

            # Log the mode for each home so we can see what's happening in real time
            display_home = home_id.replace("home_", "Home-")
            alert_marker = " ⚠" if result["alert"] else ""
            print(f"[FOG] {display_home} | Mode: {energy_mode}{alert_marker}")

        return results
