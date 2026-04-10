Smart Energy Grid — IoT Monitoring System
==========================================
Fog & Edge Computing — NCI MSc Cloud Computing

This project simulates a smart energy grid across 3 homes.
15 sensors generate energy data (solar, grid, battery, EV, temperature), a fog node processes it locally, and the cloud (AWS) stores and visualizes the data via a Grafana Cloud dashboard.

There are three ways to run this application. Choose the method that fits your setup:

METHOD 1 — RUN FROM IDE (source code already open):
===================================================
Use this if you already have the project open in IntelliJ IDEA or any other IDE.

Prerequisites:
  - Python 3.11+
  - Docker Desktop running

=> Step 1: Set up Python environment:
Open a terminal in the IDE at the project root:

  python3 -m venv .venv
  source .venv/bin/activate          (Mac/Linux)
  .venv\Scripts\activate             (Windows)
  pip install -r requirements.txt

=> Step 2: Configure IntelliJ Python Interpreter:
  File > Settings > Project > Python Interpreter
  > Add Interpreter > Add Local Interpreter
  > Virtualenv Environment > Existing
  > Select: <project_root>/.venv/bin/python
  > OK

=> Step 3: Set up environment variables:
  cp .env.example .env

  Open .env and fill in:
    AWS_IOT_ENDPOINT  — your IoT Core endpoint from AWS console
    AWS_IOT_CERT_PATH — certs/device-certificate.pem.crt
    AWS_IOT_KEY_PATH  — certs/private.pem.key
    AWS_IOT_CA_PATH   — certs/AmazonRootCA1.pem
    AWS_REGION        — us-east-1
    DYNAMODB_TABLE_NAME — smart-energy-readings
    MQTT_BROKER_HOST  — localhost
    MQTT_BROKER_PORT  — 1883

=> Step 4: Start MQTT broker (Terminal 1):
  cd fog_layer
  docker-compose up -d mosquitto
  cd ..

  Expected: Container smart_grid_mosquitto  Running

=> Step 5: Start fog node (Terminal 2):
  source .venv/bin/activate
  python fog_layer/fog_node.py

  Expected:
    [FOG] Connected to MQTT broker.
    [FOG] Subscribed to topic: home/#

=> Step 6: Start sensors (Terminal 3):
  source .venv/bin/activate
  python sensor_layer/sensor_manager.py

  Expected:
    MQTT broker is reachable. Good to go.
    All 15 sensor threads running.

After 30 seconds the fog node processes its first window:
  [FOG] Processing window at HH:MM:SS (75 readings)
  [FOG] Home-1 | Mode: SOLAR_SURPLUS
  [DISPATCH] Sent payload for Home-1 | 14.3p/kWh


METHOD 2 — CLONE FROM GITHUB AND RUN IN IDE:
============================================
Use this to set up the project fresh on a new machine.

Prerequisites:
  - Python 3.11+
  - Git
  - Docker Desktop
  - IntelliJ IDEA (or any IDE with Python support)

=> Step 1: Clone the repository:
  git clone https://github.com/mithileshkrsinghniitians/Fog-and-Edge.git
  cd "Smart Energy Grid"

=> Step 2: Open in IntelliJ IDEA:
  File > Open > select the "Smart Energy Grid" folder > OK

=> Step 3: Set up Python environment:
  Open the built-in terminal in IntelliJ (View > Tool Windows > Terminal):

  python3 -m venv .venv
  source .venv/bin/activate          (Mac/Linux)
  .venv\Scripts\activate             (Windows)
  pip install -r requirements.txt

=> Step 4: Configure Python interpreter in IntelliJ:
  File > Settings > Project > Python Interpreter
  > Add Interpreter > Add Local Interpreter
  > Virtualenv Environment > Existing
  > Select: <project_root>/.venv/bin/python     (Mac/Linux)
            <project_root>/.venv\Scripts\python.exe  (Windows)
  > OK

=> Step 5: Configure environment variables:
  cp .env.example .env

  Open .env in the IDE and fill in all values.
  (See ENVIRONMENT VARIABLES section at the bottom of this file.)

=> Step 6: Add TLS certificates:
  Place AWS IoT certificate files in the certs/ folder:
    certs/AmazonRootCA1.pem
    certs/device-certificate.pem.crt
    certs/private.pem.key

  Download from AWS Console > IoT Core > Security > Certificates
  when creating a Thing, or use the certificates provided with
  this project.

=> Step 7: Start the application (3 terminals):
  Terminal 1 — MQTT broker:
    cd fog_layer
    docker-compose up -d mosquitto
    cd ..

  Terminal 2 — Fog node:
    source .venv/bin/activate
    python fog_layer/fog_node.py

  Terminal 3 — Sensors:
    source .venv/bin/activate
    python sensor_layer/sensor_manager.py

The full pipeline is now running. Open Grafana Cloud to see the live dashboard.


METHOD 3 — RUN VIA DOCKER:
==========================
Use this to run on any machine: EC2, Windows Server, or any computer with Docker installed. No Python, no pip, no source code required. Docker pulls everything from Docker Hub:

Prerequisites:
  - Docker Desktop only
    Download: https://www.docker.com/products/docker-desktop/

=> Step 1: Create a folder on the target machine:
Create a folder called smart-energy-grid and add these 4 items:

  smart-energy-grid/
  ├── .env
  ├── certs/
  │   ├── AmazonRootCA1.pem
  │   ├── device-certificate.pem.crt
  │   └── private.pem.key
  └── fog_layer/
      ├── docker-compose.yml
      └── mosquitto.conf

Copy .env and certs/ from your development machine.
Copy docker-compose.yml and mosquitto.conf from fog_layer/.

=> Step 2: Run the application:
  cd smart-energy-grid/fog_layer
  docker-compose up

Docker automatically pulls:
  eclipse-mosquitto:2.0                             (MQTT broker)
  mithileshsingh/smart-energy-fog-node:latest       (fog node)
  mithileshsingh/smart-energy-sensor-manager:latest (15 sensors)

All three services start. The full pipeline runs immediately.

=> Step 3: Run in background (optional):
  docker-compose up -d

=> Useful Docker commands:
  docker-compose logs -f              View logs from all services
  docker-compose logs -f fog-node     Fog node logs only
  docker-compose logs -f sensor-manager  Sensor logs only
  docker-compose ps                   Check running containers
  docker-compose down                 Stop everything

-- Expected output --
  smart_grid_mosquitto  | Starting Mosquitto MQTT broker
  smart_grid_fog_node   | [FOG] Connected to MQTT broker.
  smart_grid_fog_node   | [FOG] Subscribed to topic: home/#
  smart_grid_sensors    | MQTT broker is reachable. Good to go.
  smart_grid_sensors    | All 15 sensor threads running.
  smart_grid_fog_node   | [FOG] Processing window at HH:MM:SS
  smart_grid_fog_node   | [FOG] Home-1 | Mode: SOLAR_SURPLUS
  smart_grid_fog_node   | [DISPATCH] Sent payload for Home-1


END-TO-END TEST:
================
With Mosquitto running and AWS credentials configured in .env:
  source .venv/bin/activate
  python backend/test_pipeline.py

Runs for 90 seconds and checks 6 steps:
  MQTT broker reachable
  Sensor manager started
  MQTT messages arriving  (195 received)
  Fog node processing cycle
  DynamoDB records found  (6 records)
  Lambda query API working (6 readings returned)


VERIFY CLOUD BACKEND:
=====================
The AWS backend is live. Verify it with a single curl command:

  curl "https://cefq7vq5wv2ppdn3iao4jyiwny0iglzy.lambda-url.us-east-1.on.aws/?hours=1"

A successful response returns JSON with sensor readings from DynamoDB.


DASHBOARD:
==========
The dashboard runs on Grafana Cloud (free tier). Full setup guide: backend/dashboard/grafana_setup.md
Dashboard JSON:   backend/dashboard/dashboard_config.json

Quick steps:
  1. Sign up at https://grafana.com
  2. Install Infinity datasource plugin
  3. Point datasource at your Lambda query Function URL
  4. Import backend/dashboard/dashboard_config.json
  5. Set auto-refresh to 30 seconds

5 panels: Solar Output | Grid Consumption | Battery Level | EV Charger | Temperature


ENVIRONMENT VARIABLES:
======================
Copy .env.example to .env and fill in these values:

  MQTT_BROKER_HOST      MQTT broker address       (default: localhost)
  MQTT_BROKER_PORT      MQTT broker port          (default: 1883)
  AWS_IOT_ENDPOINT      Your IoT Core endpoint from AWS console
  AWS_IOT_CERT_PATH     Path to device certificate
  AWS_IOT_KEY_PATH      Path to private key
  AWS_IOT_CA_PATH       Path to Amazon Root CA
  AWS_REGION            AWS region                (us-east-1)
  DYNAMODB_TABLE_NAME   DynamoDB table name       (smart-energy-readings)