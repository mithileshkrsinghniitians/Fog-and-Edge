# fog_node.py
#
# This is the fog computing node — the brain that sits between the sensors and the cloud.
#
# In a real fog computing setup, this would run on a local device like a Raspberry Pi
# or an edge server in the home/building. The key idea is that NOT everything goes
# straight to the cloud. The fog node receives raw sensor data, does some local
# processing (averaging, anomaly detection, aggregation), and only then sends a
# condensed, useful result up to AWS.
#
# Why not just send every reading directly to the cloud?
#   - With 15 sensors firing every 5 seconds, that's 3 readings/second = ~260,000/day
#   - AWS IoT Core charges per message — that adds up fast
#   - Latency: local processing is instant, cloud round-trips take time
#   - Resilience: if the internet drops, the fog node keeps processing locally
#
# The 30-second window idea:
#   We collect 30 seconds worth of readings into a buffer, then process the whole
#   batch at once. So instead of sending 6 individual solar readings to AWS, we
#   send one averaged value with min/max. That's the fog computing pattern.

import json
import copy
import time
import threading
import os
from datetime import datetime

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# These are implemented in the next files — data_processor does local aggregation,
# cloud_dispatcher handles sending results up to AWS.
from data_processor import DataProcessor
from cloud_dispatcher import CloudDispatcher

load_dotenv()

# How long to buffer readings before processing and dispatching (in seconds).
# 30 seconds means we collect ~6 readings per sensor per home before aggregating.
# In a real system you'd tune this based on how time-sensitive the data is.
PROCESSING_INTERVAL = 30

# The readings buffer — this is the core data structure of the fog node.
# All incoming sensor messages get stored here until the next processing window.
#
# Structure:
#   {
#       "home_1": {
#           "solar_panel": [ {value, unit, timestamp}, {value, unit, timestamp}, ... ],
#           "grid_import":  [ ... ],
#           ...
#       },
#       "home_2": { ... },
#       ...
#   }
readings_buffer = {}

# We need a lock because the MQTT on_message callback runs in a separate thread
# (managed by paho internally), while the main thread reads and clears the buffer
# every 30 seconds. Without a lock, both could access the dict at the same time
# and corrupt the data (race condition).
buffer_lock = threading.Lock()


# ── MQTT Callbacks ────────────────────────────────────────────────────────────

def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[FOG] Connected to MQTT broker.")

        # Subscribe to home/# which matches every topic that starts with "home/"
        # So we get: home/home_1/solar_panel, home/home_2/grid_import, etc.
        # The # wildcard in MQTT means "anything from here onwards".
        client.subscribe("home/#")
        print("[FOG] Subscribed to topic: home/#  (all homes, all sensors)")
        print()
    else:
        print(f"[FOG] Failed to connect to broker. Return code: {rc}")
        print("[FOG] Is Mosquitto running? Try: docker-compose up -d (from fog_layer/)")


def on_disconnect(client, userdata, rc):
    if rc != 0:
        print(f"[FOG] Unexpected disconnection (rc={rc}). Will try to reconnect...")


def on_message(client, userdata, msg):
    # This function is called every time a sensor publishes a reading.
    # It runs in paho's network thread — keep it fast, don't do heavy work here.
    # Just parse and store. Let the main loop do the processing.
    try:
        payload = json.loads(msg.payload.decode("utf-8"))

        home_id     = payload["home_id"]
        sensor_type = payload["sensor_type"]
        value       = payload["value"]
        unit        = payload["unit"]
        timestamp   = payload["timestamp"]

        # Store the reading in the buffer under the right home and sensor.
        # Using the lock here because the main thread might be reading the buffer
        # at the same moment this callback fires.
        with buffer_lock:
            if home_id not in readings_buffer:
                readings_buffer[home_id] = {}
            if sensor_type not in readings_buffer[home_id]:
                readings_buffer[home_id][sensor_type] = []

            readings_buffer[home_id][sensor_type].append({
                "value":     value,
                "unit":      unit,
                "timestamp": timestamp
            })

        # Print a clean log line so we can watch data flowing in.
        # Format: [FOG] Home-1 | solar: 3.42 kW
        display_home   = home_id.replace("home_", "Home-")
        display_sensor = sensor_type.split("_")[0]  # "solar_panel" → "solar"
        print(f"[FOG] {display_home} | {display_sensor}: {value} {unit}")

    except (json.JSONDecodeError, KeyError) as e:
        # If a message arrives that isn't valid JSON or is missing fields, just
        # log it and move on — don't crash the whole fog node over one bad message.
        print(f"[FOG] WARNING: Could not parse message on {msg.topic} — {e}")


# ── Processing Window ─────────────────────────────────────────────────────────

def run_processing_window(processor, dispatcher):
    # Called every PROCESSING_INTERVAL seconds from the main loop.
    # Takes a snapshot of everything collected so far, clears the buffer,
    # then passes the snapshot to the data processor and cloud dispatcher.

    # Snapshot + clear must be atomic (inside the lock) so we don't lose
    # readings that arrive during the processing step itself.
    with buffer_lock:
        if not readings_buffer:
            print("[FOG] Processing window: buffer is empty, nothing to process yet.")
            return

        snapshot = copy.deepcopy(readings_buffer)
        readings_buffer.clear()

    # Count how many readings we collected across all homes and sensors
    total_readings = sum(
        len(readings)
        for home_data in snapshot.values()
        for readings in home_data.values()
    )

    now = datetime.now().strftime("%H:%M:%S")
    print()
    print(f"[FOG] ── Processing window at {now} ({total_readings} readings) ──")

    # Step 1: local aggregation on the fog node.
    # DataProcessor takes the raw buffer and returns aggregated results
    # (averages, min/max, anomaly flags) for each home and sensor type.
    processed = processor.process(snapshot)

    # Step 2: send aggregated results to AWS.
    # CloudDispatcher handles the AWS IoT / DynamoDB upload.
    # We send processed data, not raw readings — that's the whole point of fog computing.
    dispatcher.dispatch(processed)

    print(f"[FOG] ── Window complete ──────────────────────────────")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    broker_host = os.getenv("MQTT_BROKER_HOST", "localhost")
    broker_port = int(os.getenv("MQTT_BROKER_PORT", 1883))

    print("=" * 55)
    print("  Smart Energy Grid — Fog Node")
    print("=" * 55)
    print(f"  Broker:    {broker_host}:{broker_port}")
    print(f"  Topic:     home/#")
    print(f"  Interval:  process every {PROCESSING_INTERVAL}s")
    print("=" * 55)
    print()

    # Set up the data processor and cloud dispatcher.
    # These are created once and reused every processing window.
    processor  = DataProcessor()
    dispatcher = CloudDispatcher()

    # Set up the MQTT client for the fog node itself.
    # This is separate from all the sensor clients — the fog node is purely a subscriber.
    client = mqtt.Client(client_id="fog_node")
    client.on_connect    = on_connect
    client.on_disconnect = on_disconnect
    client.on_message    = on_message

    print(f"[FOG] Connecting to broker at {broker_host}:{broker_port}...")
    try:
        client.connect(broker_host, broker_port, keepalive=60)
    except ConnectionRefusedError:
        print("[FOG] ERROR: Broker refused connection. Is Mosquitto running?")
        print("[FOG]        From fog_layer/: docker-compose up -d")
        return

    # loop_start() handles the MQTT network traffic in a background thread.
    # This leaves the main thread free to manage the 30-second processing windows.
    client.loop_start()

    # Main processing loop — runs every PROCESSING_INTERVAL seconds.
    # While this sleeps, the MQTT background thread keeps receiving messages
    # and the on_message callback keeps filling the buffer.
    try:
        while True:
            time.sleep(PROCESSING_INTERVAL)
            run_processing_window(processor, dispatcher)

    except KeyboardInterrupt:
        print("\n[FOG] Shutting down fog node...")
        client.loop_stop()
        client.disconnect()
        print("[FOG] Disconnected. Goodbye.")


if __name__ == "__main__":
    main()
