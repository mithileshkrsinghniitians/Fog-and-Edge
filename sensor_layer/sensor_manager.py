# sensor_manager.py
# This is the main entry point for the entire sensor layer.
# Its job is to read the config, spin up one instance of each sensor type
# per home, and start them all running at the same time using threads.
#
# Why threads? Because each sensor has its own loop (read → publish → sleep).
# If we ran them one at a time the first sensor would block everything else.
# With threading, all 15 sensors run "simultaneously" — or close enough for
# a simulation running on one machine.

import json
import os
import sys
import time
import threading

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()

# --- Path setup ---
# The sensor classes live in sensor_layer/sensors/, but sensor_manager.py is in
# sensor_layer/. Python won't find them unless we add that subfolder to the path.
sensors_dir = os.path.join(os.path.dirname(__file__), "sensors")
sys.path.insert(0, sensors_dir)

from solar_sensor import SolarSensor
from grid_sensor import GridSensor
from battery_sensor import BatterySensor
from ev_sensor import EVSensor
from thermostat_sensor import ThermostatSensor


# --- Load config ---
# config.json sits in the same folder as this file (sensor_layer/)
config_path = os.path.join(os.path.dirname(__file__), "config.json")

with open(config_path, "r") as f:
    config = json.load(f)

dispatch_rate = config["simulation"]["dispatch_rate_seconds"]
homes = [home["home_id"] for home in config["homes"]]


def check_broker_connection(host, port):
    # Before starting all 15 sensors, do a quick check that the MQTT broker
    # is actually reachable. Better to fail fast here with a clear message
    # than have 15 threads all fail silently in the background.
    print(f"\nChecking MQTT broker connection at {host}:{port}...")
    test_client = mqtt.Client(client_id="connection_test")

    connected = False

    def on_connect(client, userdata, flags, rc):
        nonlocal connected
        if rc == 0:
            connected = True

    test_client.on_connect = on_connect

    try:
        test_client.connect(host, port, keepalive=5)
        test_client.loop_start()
        time.sleep(1.5)  # give it a moment to connect
        test_client.loop_stop()
        test_client.disconnect()
    except Exception as e:
        print(f"ERROR: Could not reach MQTT broker — {e}")
        print("Make sure Mosquitto is running: brew services start mosquitto")
        return False

    if connected:
        print("MQTT broker is reachable. Good to go.\n")
    else:
        print("ERROR: Broker unreachable. Check your .env and that Mosquitto is running.")

    return connected


def create_sensors_for_home(home_id):
    # Create one of each sensor type for a given home.
    # Each sensor manages its own MQTT client connection internally.
    #
    # Why does each sensor have its own MQTT client instead of sharing one?
    # Because paho-mqtt clients aren't designed to be shared across threads —
    # if two threads tried to publish at the same time on the same client
    # you'd get race conditions. One client per sensor = no shared state.
    sensors = [
        SolarSensor(home_id=home_id, dispatch_rate=dispatch_rate),
        GridSensor(home_id=home_id, dispatch_rate=dispatch_rate),
        BatterySensor(home_id=home_id, dispatch_rate=dispatch_rate),
        EVSensor(home_id=home_id, dispatch_rate=dispatch_rate),
        ThermostatSensor(home_id=home_id, dispatch_rate=dispatch_rate),
    ]
    return sensors


def start_sensor_thread(sensor):
    # Each sensor's run() method loops forever, so it needs to be in its own thread.
    # daemon=True means the thread will be killed automatically when the main thread exits.
    # Without daemon=True, hitting Ctrl+C wouldn't actually stop the program —
    # the threads would keep going in the background.
    thread = threading.Thread(target=sensor.run, daemon=True)
    thread.start()
    return thread


def main():
    broker_host = os.getenv("MQTT_BROKER_HOST", "localhost")
    broker_port = int(os.getenv("MQTT_BROKER_PORT", 1883))

    # Step 1: verify the broker is up before we try to connect 15 sensors to it
    if not check_broker_connection(broker_host, broker_port):
        sys.exit(1)

    print("=" * 55)
    print("  Smart Energy Grid — Sensor Layer Starting Up")
    print("=" * 55)
    print(f"  Homes:         {len(homes)} ({', '.join(homes)})")
    print(f"  Sensors/home:  5 (solar, grid, battery, ev, thermostat)")
    print(f"  Total sensors: {len(homes) * 5}")
    print(f"  Dispatch rate: every {dispatch_rate} seconds")
    print(f"  MQTT broker:   {broker_host}:{broker_port}")
    print("=" * 55)
    print()

    # Step 2: create and start all sensors
    all_threads = []

    for home_id in homes:
        print(f"Starting sensors for {home_id}...")
        sensors = create_sensors_for_home(home_id)

        for sensor in sensors:
            thread = start_sensor_thread(sensor)
            all_threads.append(thread)
            print(f"  ✓ {sensor.sensor_type} started (thread id: {thread.ident})")

        print()

    total = len(all_threads)
    print(f"All {total} sensor threads running. Press Ctrl+C to stop.\n")

    # Step 3: keep the main thread alive
    # The child threads are all daemons, so they'll die if the main thread exits.
    # We need to keep the main thread running — the simplest way is just to loop.
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n\nStopping sensor manager...")
        print(f"All {total} sensor threads will shut down now.")
        print("Goodbye.")
        # Daemon threads are killed automatically when we exit here


if __name__ == "__main__":
    main()
