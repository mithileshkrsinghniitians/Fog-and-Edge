# ========== base_sensor.py ==========
# This is the abstract base class that all my sensor classes will inherit from.
# The idea is to keep the common stuff (connecting to MQTT, building the payload,
# publishing) in one place so I don't repeat it in every sensor file.

import json
import time
import os
from abc import ABC, abstractmethod
from datetime import datetime, timezone

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

load_dotenv()


class BaseSensor(ABC):
    def __init__(self, home_id, sensor_type, unit, dispatch_rate=5):
        self.home_id = home_id
        self.sensor_type = sensor_type
        self.unit = unit
        self.dispatch_rate = dispatch_rate  # how often to send a reading, in seconds

        # Build the MQTT topic this sensor will publish to. e.g. home/home_1/solar_panel:
        self.topic = f"home/{self.home_id}/{self.sensor_type}"

        # Set up the MQTT client and connect to the broker. The broker address comes from .env so it's easy to change:
        broker_host = os.getenv("MQTT_BROKER_HOST", "localhost")
        broker_port = int(os.getenv("MQTT_BROKER_PORT", 1883))

        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect

        print(f"[{self.home_id}] [{self.sensor_type}] Connecting to MQTT broker at {broker_host}:{broker_port}")
        self.client.connect(broker_host, broker_port, keepalive=60)

        # loop_start() runs the MQTT network loop in a background thread. This way the main loop can just focus on reading and publishing:
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, rc):
        # rc = 0 means connected successfully.
        if rc == 0:
            print(f"[{self.home_id}] [{self.sensor_type}] Connected to MQTT broker.")
        else:
            print(f"[{self.home_id}] [{self.sensor_type}] Failed to connect. Return code: {rc}")

    def _on_disconnect(self, client, userdata, rc):
        print(f"[{self.home_id}] [{self.sensor_type}] Disconnected from MQTT broker.")

    @abstractmethod
    def get_reading(self):
        pass

    def publish(self):
        value = self.get_reading()

        value = round(value, 2)

        payload = {
            "home_id": self.home_id,
            "sensor_type": self.sensor_type,
            "value": value,
            "unit": self.unit,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        payload_str = json.dumps(payload)
        self.client.publish(self.topic, payload_str)

        print(f"[{self.home_id}] [{self.sensor_type}] Published: {value} {self.unit}")

    def run(self):
        # Keep reading and publishing until the script is killed:
        print(f"[{self.home_id}] [{self.sensor_type}] Starting sensor loop (every {self.dispatch_rate}s)...")
        try:
            while True:
                self.publish()
                time.sleep(self.dispatch_rate)
        except KeyboardInterrupt:
            print(f"[{self.home_id}] [{self.sensor_type}] Stopped.")
            self.client.loop_stop()
            self.client.disconnect()
