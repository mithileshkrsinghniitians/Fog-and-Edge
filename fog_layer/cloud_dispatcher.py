# cloud_dispatcher.py
#
# Sends processed fog payloads up to AWS IoT Core over MQTT with TLS.
#
# This is the "cloud uplink" part of the fog computing architecture.
# After the fog node has aggregated 30 seconds of sensor readings and figured
# out the energy mode, this module sends that compact, processed result to AWS.
#
# How is this MQTT connection different from the local Mosquitto one?
#   Local Mosquitto (fog_node ↔ sensors):
#     - Runs on localhost, no authentication, no encryption
#     - Fine because everything is on the same local network
#     - Port 1883
#
#   AWS IoT Core (fog_node → cloud):
#     - Runs over the public internet so security is critical
#     - Uses TLS (Transport Layer Security) — the same encryption as HTTPS
#     - Uses X.509 certificates for device authentication
#     - Port 8883 (MQTT over TLS, not plain MQTT)
#
# What are TLS certificates and why does AWS require them?
#   AWS IoT Core doesn't use usernames/passwords — it uses certificates.
#   Each "Thing" (device) in IoT Core gets its own certificate and private key.
#   When the fog node connects, it presents its certificate, AWS verifies it
#   against the root CA (Certificate Authority), and only then allows the connection.
#   This proves to AWS that it's really OUR fog node connecting, not someone pretending.
#   You generate these certs in the AWS IoT Core console when you register a Thing.
#
# The three cert files needed:
#   - device certificate (.pem.crt)  → "here's my ID"
#   - private key (.pem.key)         → "here's proof the ID is mine" (keep this secret!)
#   - Amazon Root CA (.pem)          → "here's the authority that signed my ID"

import json
import os
import ssl
import time

import paho.mqtt.client as mqtt
from dotenv import load_dotenv

from price_fetcher import PriceFetcher

load_dotenv()

# AWS IoT Core always uses port 8883 for MQTT over TLS
AWS_IOT_PORT = 8883

# How many seconds to wait before retrying a failed connection
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

        # PriceFetcher handles caching so calling get_current_price() frequently
        # won't hammer the Octopus API — it only fetches a fresh price every 30 minutes.
        self.price_fetcher = PriceFetcher()

        self.client = None

        if self.certs_available and self.endpoint:
            self._setup_client()
            self.connect()
        else:
            self._print_setup_warning()

    # ── Setup ─────────────────────────────────────────────────────────────────

    def _check_certs(self):
        # Before doing anything, check that the certificate files actually exist.
        # If you've just cloned the project and haven't set up AWS IoT yet,
        # the certs won't be there. Better to catch that here with a clear message
        # than to crash with a confusing "file not found" error later.
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
        # Create the paho MQTT client and configure TLS.
        # The client_id should be unique per device — AWS IoT Core uses it to identify
        # which Thing is connecting. If two devices connect with the same ID, one gets kicked.
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

    # ── Connection management ─────────────────────────────────────────────────

    def connect(self):
        # Try to connect to AWS IoT Core. Retry a few times if it fails —
        # might just be a temporary network blip.
        if not self.certs_available or not self.endpoint:
            return

        for attempt in range(1, MAX_RETRIES + 1):
            try:
                print(f"[DISPATCH] Connecting to AWS IoT Core (attempt {attempt}/{MAX_RETRIES})...")
                self.client.connect(self.endpoint, AWS_IOT_PORT, keepalive=60)
                self.client.loop_start()

                # Give it a moment to complete the TLS handshake before checking
                time.sleep(2)

                if self.connected:
                    return  # success, done
                else:
                    print(f"[DISPATCH] Handshake did not complete on attempt {attempt}.")

            except ssl.SSLError as e:
                # SSL errors usually mean there's a problem with the certificates —
                # wrong file, expired cert, wrong CA. No point retrying.
                print(f"[DISPATCH] TLS/SSL error — check your certificate files: {e}")
                print("[DISPATCH] Make sure the certs match the Thing registered in AWS IoT Core.")
                return

            except OSError as e:
                # Network error — endpoint might be wrong or network is down
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

    # ── Dispatch ──────────────────────────────────────────────────────────────

    def dispatch(self, processed_results):
        # processed_results is a list — one dict per home — from DataProcessor.process().
        # We publish each home's result to its own IoT Core topic.
        #
        # Why a topic per home?
        #   AWS IoT Core uses topics for routing — you can set up IoT Rules to trigger
        #   different actions based on the topic. e.g. "home/+/processed" → Kinesis,
        #   "home/+/alert" → SNS notification to the homeowner's phone.

        # Fetch the current electricity price once and attach it to every payload.
        # We fetch once here (not per home) because the price is the same for everyone
        # on the same tariff at the same moment.
        current_price = self.price_fetcher.get_current_price()

        for result in processed_results:
            home_id = result.get("home_id", "unknown")

            # Attach the electricity price to the payload before sending.
            # This means the Lambda ingest handler and DynamoDB will have price
            # context alongside the energy readings — useful for cost calculations.
            result["electricity_price"] = current_price

            topic = f"home/{home_id}/processed"

            if self.connected:
                payload_str = json.dumps(result)
                self.client.publish(topic, payload_str, qos=1)

                # QoS 1 means "at least once delivery" — AWS IoT Core will acknowledge
                # the message and paho will retry if no ack is received. Good enough for
                # sensor data where occasional duplicates are fine.

                display_home = home_id.replace("home_", "Home-")
                print(f"[DISPATCH] Sent payload for {display_home} → {topic} "
                      f"| {result.get('energy_mode')} | {current_price}p/kWh")
            else:
                # Not connected to AWS — log the payload locally so we can see it
                # was processed correctly even without cloud connectivity.
                display_home = home_id.replace("home_", "Home-")
                print(f"[DISPATCH] (local only) {display_home} | {result.get('energy_mode')} "
                      f"| {current_price}p/kWh | topic would be: {topic}")
