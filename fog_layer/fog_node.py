import json
import copy
import time
import threading
import os
from datetime import datetime

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

from data_processor import DataProcessor
from cloud_dispatcher import CloudDispatcher

load_dotenv()

PROCESSING_INTERVAL = 30

# The readings buffer — this is the core data structure of the fog node.
# All incoming sensor messages get stored here until the next processing window.

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

buffer_lock = threading.Lock()

# MQTT Callbacks:
def on_connect(client, userdata, flags, rc):
    if rc == 0:
        print("[FOG] Connected to MQTT broker.")

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
    try:
        payload = json.loads(msg.payload.decode("utf-8"))

        home_id     = payload["home_id"]
        sensor_type = payload["sensor_type"]
        value       = payload["value"]
        unit        = payload["unit"]
        timestamp   = payload["timestamp"]

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

        display_home   = home_id.replace("home_", "Home-")
        display_sensor = sensor_type.split("_")[0]  # "solar_panel" → "solar"
        print(f"[FOG] {display_home} | {display_sensor}: {value} {unit}")

    except (json.JSONDecodeError, KeyError) as e:
        print(f"[FOG] WARNING: Could not parse message on {msg.topic} — {e}")


# Processing Window:
def run_processing_window(processor, dispatcher):
    with buffer_lock:
        if not readings_buffer:
            print("[FOG] Processing window: buffer is empty, nothing to process yet.")
            return

        snapshot = copy.deepcopy(readings_buffer)
        readings_buffer.clear()

    # Count how many readings we collected across all homes and sensors:
    total_readings = sum(
        len(readings)
        for home_data in snapshot.values()
        for readings in home_data.values()
    )

    now = datetime.now().strftime("%H:%M:%S")
    print()
    print(f"[FOG] ── Processing window at {now} ({total_readings} readings) ──")

    # Step 1: local aggregation on the fog node:
    processed = processor.process(snapshot)

    # Step 2: send aggregated results to AWS:
    dispatcher.dispatch(processed)

    print(f"[FOG] ── Window complete ──")
    print()


# Main:
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

    # Set up the data processor and cloud dispatcher:
    processor  = DataProcessor()
    dispatcher = CloudDispatcher()

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

    # loop_start() handles the MQTT network traffic in a background thread:
    client.loop_start()

    # Main processing loop — runs every PROCESSING_INTERVAL seconds:
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
