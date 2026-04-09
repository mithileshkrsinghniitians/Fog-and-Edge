import os
from datetime import datetime, timezone

VALIDATION_LIMITS = {
    "solar_panel":      {"min": 0.0,  "max": 6.0},    # kW — a typical residential system is 3-6kW
    "grid_import":      {"min": 0.0,  "max": 15.0},   # kW — 15kW would be an enormous house
    "battery_storage":  {"min": 0.0,  "max": 100.0},  # percent — SoC can't go below 0 or above 100
    "temperature":      {"min": 10.0, "max": 40.0},   # celsius — indoor ambient, so no extremes
    "ev_charger":       {"min": 0.0,  "max": 8.0},    # kW — 7.4kW charger + small headroom
}


class DataProcessor:

    def __init__(self):
        self.fog_node_id = os.getenv("FOG_NODE_ID", "fog_node_01")

    # Validation:
    def validate_reading(self, sensor_type, value):
        if sensor_type not in VALIDATION_LIMITS:
            return True

        limits = VALIDATION_LIMITS[sensor_type]

        if value < limits["min"] or value > limits["max"]:
            return False

        return True

    # Aggregation:
    def aggregate(self, readings_buffer):
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
                    aggregated[home_id][sensor_type] = None

        return aggregated

    # Energy Mode Detection:
    def detect_energy_mode(self, home_summary):
        def avg(sensor_type):
            result = home_summary.get(sensor_type)
            if result is None:
                return None
            return result.get("avg")

        solar_avg   = avg("solar_panel")
        grid_avg    = avg("grid_import")
        battery_avg = avg("battery_storage")
        ev_avg      = avg("ev_charger")

        if battery_avg is not None and battery_avg < 15.0:
            return "BATTERY_LOW"

        if ev_avg is not None and ev_avg > 1.0:
            return "EV_CHARGING"

        if solar_avg is not None and grid_avg is not None and solar_avg > grid_avg:
            return "SOLAR_SURPLUS"

        if grid_avg is not None and grid_avg > 5.0:
            return "GRID_HEAVY"

        return "NORMAL"

    # Main Process Method:

    def process(self, readings_buffer):
        timestamp = datetime.now(timezone.utc).isoformat()

        # Step 1: aggregate all the raw readings into per-home summaries:
        aggregated = self.aggregate(readings_buffer)

        results = []

        for home_id, home_summary in aggregated.items():

            # Step 2: figure out what mode this home is in right now:
            energy_mode = self.detect_energy_mode(home_summary)

            # Step 3: decide whether to raise an alert:
            # Alert = something the homeowner or grid operator should know about.
            # BATTERY_LOW and GRID_HEAVY are the two situations we flag.
            alert = energy_mode in ("BATTERY_LOW", "GRID_HEAVY")

            # Step 4: check if any sensor had invalid readings this window:
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

            # Log the mode for each home so we can see what's happening in real time:
            display_home = home_id.replace("home_", "Home-")
            alert_marker = " ⚠" if result["alert"] else ""
            print(f"[FOG] {display_home} | Mode: {energy_mode}{alert_marker}")

        return results
