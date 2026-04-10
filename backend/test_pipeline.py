import os
import sys
import json
import time
import threading
import subprocess

import requests
import paho.mqtt.client as mqtt
from dotenv import load_dotenv

# Load .env from the project root:
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(project_root, ".env"))

# Paths to the two layers we'll run as subprocesses:
sensor_layer_dir = os.path.join(project_root, "sensor_layer")
fog_layer_dir    = os.path.join(project_root, "fog_layer")

MQTT_HOST         = os.getenv("MQTT_BROKER_HOST", "localhost")
MQTT_PORT         = int(os.getenv("MQTT_BROKER_PORT", 1883))
DYNAMODB_TABLE    = os.getenv("DYNAMODB_TABLE_NAME", "smart-energy-readings")
AWS_REGION        = os.getenv("AWS_REGION", "us-east-1")
LAMBDA_URL        = "https://cefq7vq5wv2ppdn3iao4jyiwny0iglzy.lambda-url.us-east-1.on.aws/"

PIPELINE_RUN_DURATION = 65

# Just terminal escape codes so the output is easier to read at a glance:
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"

def ok(msg):    print(f"  {GREEN} {msg}{RESET}")
def fail(msg):  print(f"  {RED} {msg}{RESET}")
def info(msg):  print(f"  {YELLOW}   {msg}{RESET}")
def header(msg): print(f"\n{'─'*55}\n  {msg}\n{'─'*55}")

# We collect pass/fail for each check and print the summary at the end:
results = {
    "broker":     None,
    "sensors":    None,
    "mqtt_msgs":  None,
    "fog":        None,
    "dynamodb":   None,
    "lambda_api": None,
}
details = {}


# Step 1: Check MQTT broker:
def check_broker():
    header("Step 1 / 6 — MQTT Broker")
    print("  Trying to connect to Mosquitto on localhost:1883 ...")

    connected = threading.Event()

    def on_connect(client, userdata, flags, rc):
        if rc == 0:
            connected.set()

    client = mqtt.Client(client_id="pipeline_test_probe")
    client.on_connect = on_connect

    try:
        client.connect(MQTT_HOST, MQTT_PORT, keepalive=5)
        client.loop_start()
        reachable = connected.wait(timeout=5)
        client.loop_stop()
        client.disconnect()
    except Exception as e:
        details["broker"] = str(e)
        results["broker"] = False
        fail(f"Could not reach broker: {e}")
        fail("Make sure Mosquitto is running: docker-compose up -d (in fog_layer/)")
        return False

    if reachable:
        results["broker"] = True
        ok(f"Broker reachable at {MQTT_HOST}:{MQTT_PORT}")
        return True
    else:
        results["broker"] = False
        fail("Broker connection timed out after 5 seconds")
        return False


# Step 2 & 3: Run sensors and monitor MQTT messages:
captured_messages = []
messages_lock = threading.Lock()


def check_sensors_and_mqtt():
    header("Step 2 & 3 / 6 — Sensor Manager + MQTT Messages")
    print(f"  Starting sensor_manager.py for {PIPELINE_RUN_DURATION}s ...")
    print("  Simultaneously subscribing to home/# to count arriving messages.")
    print()

    # Set up a test MQTT subscriber that just counts incoming messages:
    def on_message_capture(client, userdata, msg):
        with messages_lock:
            try:
                payload = json.loads(msg.payload.decode("utf-8"))
                captured_messages.append({
                    "topic":   msg.topic,
                    "home_id": payload.get("home_id"),
                    "sensor":  payload.get("sensor_type"),
                    "value":   payload.get("value"),
                })
            except Exception:
                pass

    subscriber = mqtt.Client(client_id="pipeline_test_subscriber")
    subscriber.on_message = on_message_capture

    try:
        subscriber.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
        subscriber.subscribe("home/#")
        subscriber.loop_start()
    except Exception as e:
        fail(f"Could not start MQTT subscriber: {e}")
        results["sensors"]   = False
        results["mqtt_msgs"] = False
        return False

    sensor_proc = subprocess.Popen(
        [sys.executable, "sensor_manager.py"],
        cwd=sensor_layer_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if sensor_proc.poll() is not None:
        fail("sensor_manager.py failed to start (check for import errors)")
        results["sensors"]   = False
        results["mqtt_msgs"] = False
        subscriber.loop_stop()
        return False

    results["sensors"] = True
    ok("sensor_manager.py started (pid: {})".format(sensor_proc.pid))
    print()

    sensor_output_lines = []

    def capture_output(proc, lines):
        for line in proc.stdout:
            lines.append(line.rstrip())
            if len(lines) <= 8:
                info(f"[sensor] {line.rstrip()}")

    output_thread = threading.Thread(
        target=capture_output, args=(sensor_proc, sensor_output_lines), daemon=True
    )
    output_thread.start()

    # Wait and periodically show how many MQTT messages have arrived:
    print(f"\n  Waiting {PIPELINE_RUN_DURATION}s for sensors to publish readings ...")
    check_points = [10, 20, 35, 50, PIPELINE_RUN_DURATION]

    for elapsed in range(1, PIPELINE_RUN_DURATION + 1):
        time.sleep(1)
        if elapsed in check_points:
            with messages_lock:
                count = len(captured_messages)
            print(f"  t={elapsed:>2}s — {count} MQTT messages received so far")

    # Shut down the sensor manager cleanly:
    sensor_proc.terminate()
    try:
        sensor_proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        sensor_proc.kill()

    subscriber.loop_stop()
    subscriber.disconnect()

    with messages_lock:
        final_count = len(captured_messages)
        unique_homes   = len({m["home_id"] for m in captured_messages if m.get("home_id")})
        unique_sensors = len({m["sensor"]  for m in captured_messages if m.get("sensor")})

    details["mqtt_msgs"] = final_count

    print()
    if final_count > 0:
        results["mqtt_msgs"] = True
        ok(f"{final_count} messages received across {unique_homes} homes, {unique_sensors} sensor types")
    else:
        results["mqtt_msgs"] = False
        fail("No MQTT messages arrived — sensors may not have connected to broker")

    return final_count > 0


# Step 4: Verify fog node processing cycle:
def check_fog_node():
    header("Step 4 / 6 — Fog Node Processing")

    try:
        result = subprocess.run(
            ["docker", "logs", "--tail", "200", "smart_grid_fog_node"],
            capture_output=True, text=True, timeout=10,
        )
        combined = result.stdout + result.stderr
        if "Processing window" in combined:
            results["fog"] = True
            # Extract and show the most recent processing line as evidence:
            for line in reversed(combined.splitlines()):
                if "Processing window" in line:
                    ok("Fog node processing window confirmed (Docker container)")
                    info(line.strip())
                    break
            return True
        elif combined.strip():
            # Container is running but no window yet — wait briefly and retry:
            print("  Docker fog node running — waiting up to 35s for first window ...")
            deadline = time.time() + 35
            while time.time() < deadline:
                time.sleep(5)
                result = subprocess.run(
                    ["docker", "logs", "--tail", "200", "smart_grid_fog_node"],
                    capture_output=True, text=True, timeout=10,
                )
                combined = result.stdout + result.stderr
                if "Processing window" in combined:
                    results["fog"] = True
                    for line in reversed(combined.splitlines()):
                        if "Processing window" in line:
                            ok("Fog node processing window confirmed (Docker container)")
                            info(line.strip())
                            break
                    return True
    except FileNotFoundError:
        pass
    except Exception:
        pass

    # Fallback: run fog_node.py as a local subprocess (no Docker):
    fog_run_time = 65
    print(f"  Starting fog_node.py locally for up to {fog_run_time}s ...")
    print("  Watching for '[FOG] Processing window' in output ...")
    print()

    found_processing = threading.Event()

    def capture_fog(proc):
        for line in proc.stdout:
            line = line.rstrip()
            info(f"[fog]  {line}")
            if "Processing window" in line or "Mode:" in line:
                found_processing.set()

    sensor_proc = subprocess.Popen(
        [sys.executable, "sensor_manager.py"],
        cwd=sensor_layer_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    fog_proc = subprocess.Popen(
        [sys.executable, "fog_node.py"],
        cwd=fog_layer_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    if fog_proc.poll() is not None:
        fail("fog_node.py failed to start (check imports in fog_layer/)")
        sensor_proc.terminate()
        results["fog"] = False
        return False

    threading.Thread(target=capture_fog, args=(fog_proc,), daemon=True).start()
    found_processing.wait(timeout=fog_run_time)

    fog_proc.terminate()
    sensor_proc.terminate()
    for proc in [fog_proc, sensor_proc]:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()

    print()
    if found_processing.is_set():
        results["fog"] = True
        ok("Fog node ran a processing window successfully")
    else:
        results["fog"] = False
        fail(f"No processing window output seen in {fog_run_time} seconds")
        info("Check fog_layer/fog_node.py can import data_processor and cloud_dispatcher")

    return results["fog"]


# Step 5: Check DynamoDB:
def check_dynamodb():
    header("Step 5 / 6 — DynamoDB Records")
    print(f"  Checking table '{DYNAMODB_TABLE}' in region {AWS_REGION} ...")

    try:
        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError

        dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
        table    = dynamodb.Table(DYNAMODB_TABLE)

        response = table.scan(Limit=10)
        items    = response.get("Items", [])
        count    = len(items)
        details["dynamodb"] = count

        if count > 0:
            results["dynamodb"] = True
            ok(f"{count} record(s) found in DynamoDB")
            sample = items[-1]
            info(f"Latest: home={sample.get('home_id')} | mode={sample.get('energy_mode')} | {sample.get('timestamp', '')[:19]}")
        else:
            results["dynamodb"] = False
            fail("Table exists but has no records — fog node may not have dispatched yet")
            info("If AWS credentials aren't set up, cloud_dispatcher runs in local-only mode")

    except ImportError:
        results["dynamodb"] = False
        fail("boto3 not installed — run: pip install boto3")

    except Exception as e:
        results["dynamodb"] = False
        err = str(e)
        details["dynamodb_error"] = err
        if "NoCredentialsError" in type(e).__name__ or "credentials" in err.lower():
            fail("AWS credentials not configured — DynamoDB check skipped")
            info("Add AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY to your .env file")
        elif "ResourceNotFoundException" in type(e).__name__:
            fail(f"Table '{DYNAMODB_TABLE}' not found — has Terraform been applied?")
        else:
            fail(f"DynamoDB error: {err[:80]}")


# Step 6: Check Lambda query API:
def check_lambda_api():
    header("Step 6 / 6 — Lambda Query API")
    print(f"  Calling: GET {LAMBDA_URL}?hours=1")

    try:
        response = requests.get(
            LAMBDA_URL,
            params={"hours": "1"},
            timeout=10
        )

        if response.status_code == 200:
            try:
                data    = response.json()
                status  = data.get("status")
                count   = data.get("count", 0)
                details["lambda_api"] = count

                if status == "ok":
                    results["lambda_api"] = True
                    ok(f"API responded 200 OK — {count} reading(s) returned")
                    if count > 0:
                        sample = data["readings"][0]
                        info(f"Sample: home={sample.get('home_id')} | mode={sample.get('energy_mode')}")
                else:
                    results["lambda_api"] = False
                    fail(f"API returned 200 but status field is '{status}' (expected 'ok')")

            except json.JSONDecodeError:
                results["lambda_api"] = False
                fail(f"API returned 200 but response is not valid JSON")
                info(f"Response body: {response.text[:100]}")

        elif response.status_code == 404:
            results["lambda_api"] = False
            fail("404 — Lambda Function URL not found. Check the URL is correct and the function is deployed.")

        elif response.status_code == 500:
            results["lambda_api"] = False
            fail("500 — Lambda internal error. Check CloudWatch logs for the query_handler function.")
            info(f"Response: {response.text[:100]}")

        else:
            results["lambda_api"] = False
            fail(f"Unexpected status code: {response.status_code}")

    except requests.exceptions.ConnectionError:
        results["lambda_api"] = False
        fail("Connection error — check your internet connection and the Lambda URL")

    except requests.exceptions.Timeout:
        results["lambda_api"] = False
        fail("Lambda request timed out after 10 seconds")

    except Exception as e:
        results["lambda_api"] = False
        fail(f"Unexpected error: {e}")


# Final summary:
def print_summary():
    print(f"\n{'═'*55}")
    print("  PIPELINE TEST SUMMARY")
    print(f"{'═'*55}\n")

    checks = [
        ("broker",     "MQTT broker reachable"),
        ("sensors",    "Sensor manager started"),
        ("mqtt_msgs",  f"MQTT messages arriving  ({details.get('mqtt_msgs', 0)} received)"),
        ("fog",        "Fog node processing cycle"),
        ("dynamodb",   f"DynamoDB records found  ({details.get('dynamodb', '?')} records)"),
        ("lambda_api", f"Lambda query API        ({details.get('lambda_api', '?')} readings)"),
    ]

    passed = 0
    failed = 0

    for key, label in checks:
        result = results.get(key)
        if result is True:
            ok(label)
            passed += 1
        elif result is False:
            fail(label)
            failed += 1
        else:
            print(f"  ⚪ {label}  (not run)")

    print()
    if failed == 0:
        print(f"  {GREEN}All checks passed! The pipeline is working end-to-end.{RESET}")
    else:
        print(f"  {RED}{failed} check(s) failed. See output above for details.{RESET}")
        if results.get("broker") is False:
            info("Tip: start the broker first → cd fog_layer && docker-compose up -d")
        if results.get("dynamodb") is False and results.get("fog") is True:
            info("Tip: DynamoDB is only written to when AWS credentials are configured in .env")

    print(f"\n{'═'*55}\n")


# Main:
def main():
    print(f"\n{'═'*55}")
    print("  Smart Energy Grid — End-to-End Pipeline Test")
    print(f"{'═'*55}")
    print(f"  Estimated duration: ~90 seconds")
    print(f"  Project root: {project_root}")
    print(f"  MQTT broker:  {MQTT_HOST}:{MQTT_PORT}")
    print(f"  DynamoDB:     {DYNAMODB_TABLE} ({AWS_REGION})")
    print(f"  Lambda URL:   {LAMBDA_URL}")

    # Step 1: broker must be up before anything else makes sense:
    broker_ok = check_broker()
    if not broker_ok:
        fail("Broker is not reachable — stopping test here.")
        info("Fix: cd fog_layer && docker-compose up -d, then re-run this test.")
        print_summary()
        sys.exit(1)

    # Steps 2 & 3: sensors + MQTT message capture (runs for PIPELINE_RUN_DURATION seconds):
    check_sensors_and_mqtt()

    # Step 4: fog node processing:
    check_fog_node()

    # Step 5: DynamoDB check:
    check_dynamodb()

    # Step 6: Lambda query API:
    check_lambda_api()

    # Print the final summary table:
    print_summary()


if __name__ == "__main__":
    main()
