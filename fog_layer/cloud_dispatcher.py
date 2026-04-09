import json
import os
import ssl
import time

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

from price_fetcher import PriceFetcher

load_dotenv()

# AWS IoT Core always uses port 8883 for MQTT over TLS:
AWS_IOT_PORT = 8883

# How many seconds to wait before retrying a failed connection:
RETRY_DELAY = 5
MAX_RETRIES = 3


class CloudDispatcher:

    def __init__(self):
        self.endpoint     = os.getenv("AWS_IOT_ENDPOINT", "")
        self.cert_path    = os.getenv("AWS_IOT_CERT_PATH", "certs/device-certificate.pem.crt")
        self.key_path     = os.getenv("AWS_IOT_KEY_PATH",  "certs/private.pem.key")
        self.ca_path      = os.getenv("AWS_IOT_CA_PATH",   "certs/AmazonRootCA1.pem")

        self.connected      = False
        self.certs_available = self._check_certs()

        # PriceFetcher handles caching so calling get_current_price() frequently.
        # won't hammer the Octopus API — it only fetches a fresh price every 30 minutes.
        self.price_fetcher = PriceFetcher()

        self.client = None

        if self.certs_available and self.endpoint:
            self._setup_client()
            self.connect()
        else:
            self._print_setup_warning()

    # Setup:
    def _check_certs(self):
        missing = []
        for label, path in [("cert", self.cert_path), ("key", self.key_path), ("CA", self.ca_path)]:
            if not os.path.isfile(path):
                missing.append(f"  {label}: {path}")

        if missing:
            self._missing_cert_paths = missing
            return False

        return True

    def _print_setup_warning(self):
        print("\n[DISPATCH] WARNING: AWS IoT Core connection not available.")

        if not self.endpoint:
            print("[DISPATCH] AWS_IOT_ENDPOINT is not set in your .env file.")
            print("[DISPATCH] Add: AWS_IOT_ENDPOINT=your-endpoint.iot.us-east-1.amazonaws.com")

        if not self.certs_available:
            print("[DISPATCH] Missing certificate files:")
            for line in getattr(self, "_missing_cert_paths", []):
                print(f"[DISPATCH] {line}")
            print("[DISPATCH] To fix this:")
            print("[DISPATCH]   1. Go to AWS IoT Core console → Manage → Things")
            print("[DISPATCH]   2. Create a Thing and download its certificates")
            print("[DISPATCH]   3. Place them in the certs/ folder at the project root")
            print("[DISPATCH]   4. Update the paths in your .env file")

        print("[DISPATCH] Running in LOCAL ONLY mode — payloads will be logged but not sent to AWS.\n")

    def _setup_client(self):
        self.client = mqtt.Client(client_id="smart-grid-fog-node")

        # tls_set() configures paho to use TLS with our three certificate files.
        # PROTOCOL_TLS lets Python pick the best available TLS version (currently TLS 1.3).
        self.client.tls_set(
            ca_certs=self.ca_path,
            certfile=self.cert_path,
            keyfile=self.key_path,
            tls_version=ssl.PROTOCOL_TLS_CLIENT,
        )

        self.client.on_connect    = self._on_connect
        self.client.on_disconnect = self._on_disconnect

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            self.connected = True
            print(f"[DISPATCH] Connected to AWS IoT Core at {self.endpoint}")
        else:
            self.connected = False
            print(f"[DISPATCH] AWS IoT Core connection failed (rc={rc})")

    def _on_disconnect(self, client, userdata, rc):
        self.connected = False
        if rc != 0:
            print(f"[DISPATCH] Unexpected disconnect from AWS IoT Core (rc={rc})")

    # Connection management:
    def connect(self):
        # Try to connect to AWS IoT Core. Retry a few times if it fails:
        if not self.certs_available or not self.endpoint:
            return

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                print(f"[DISPATCH] Connecting to AWS IoT Core (attempt {attempt}/{MAX_RETRIES})...")
                self.client.connect(self.endpoint, AWS_IOT_PORT, keepalive=60)
                self.client.loop_start()

                # Give it a moment to complete the TLS handshake before checking:
                time.sleep(2)

                if self.connected:
                    return  # success, done
                else:
                    print(f"[DISPATCH] Handshake did not complete on attempt {attempt}.")

            except ssl.SSLError as e:
                # SSL errors usually mean there's a problem with the certificates:
                print(f"[DISPATCH] TLS/SSL error — check your certificate files: {e}")
                print("[DISPATCH] Make sure the certs match the Thing registered in AWS IoT Core.")
                return

            except OSError as e:
                # Network error — endpoint might be wrong or network is down:
                print(f"[DISPATCH] Network error on attempt {attempt}: {e}")
                if attempt < MAX_RETRIES:
                    print(f"[DISPATCH] Retrying in {RETRY_DELAY}s...")
                    time.sleep(RETRY_DELAY)

        print("[DISPATCH] Could not connect to AWS IoT Core after all attempts.")
        print("[DISPATCH] Continuing in local-only mode — cloud dispatch disabled.")

    def disconnect(self):
        if self.client and self.connected:
            self.client.loop_stop()
            self.client.disconnect()
            print("[DISPATCH] Disconnected from AWS IoT Core.")

    # Dispatch:
    def dispatch(self, processed_results):
        current_price = self.price_fetcher.get_current_price()

        for result in processed_results:
            home_id = result.get("home_id", "unknown")

            result["electricity_price"] = current_price

            topic = f"home/{home_id}/processed"

            if self.connected:
                payload_str = json.dumps(result)
                self.client.publish(topic, payload_str, qos=1)

                display_home = home_id.replace("home_", "Home-")
                print(f"[DISPATCH] Sent payload for {display_home} → {topic} "
                      f"| {result.get('energy_mode')} | {current_price}p/kWh")
            else:
                display_home = home_id.replace("home_", "Home-")
                print(f"[DISPATCH] (local only) {display_home} | {result.get('energy_mode')} "
                      f"| {current_price}p/kWh | topic would be: {topic}")
