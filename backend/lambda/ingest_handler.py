# ingest_handler.py
#
# AWS Lambda function — triggered by a Kinesis Data Stream.
#
# Overall flow:
#   Fog Node → (JSON payload) → Kinesis Data Stream → Lambda (this file) → DynamoDB
#
# Why Kinesis in the middle instead of writing directly from the fog node to DynamoDB?
#   Kinesis is a managed streaming service — it acts as a buffer. If Lambda is slow
#   or DynamoDB has a hiccup, records queue up in Kinesis rather than getting dropped.
#   It also makes it easy to add other consumers later (e.g. a real-time analytics
#   service reading from the same stream) without touching the fog node code.
#   For this scale it's a bit overkill honestly, but it's good practice to use it.
#
# Why is the Kinesis data base64 encoded?
#   Kinesis stores raw bytes — not strings. When you put a JSON string into Kinesis
#   it gets stored as bytes, and Lambda delivers those bytes base64-encoded so they
#   can be safely transmitted in JSON (the event object itself is JSON, and raw bytes
#   aren't valid JSON). So we have to reverse that: base64 decode → bytes → UTF-8 string → JSON.

import json
import base64
import os
import time
from decimal import Decimal, InvalidOperation

import boto3
from botocore.exceptions import ClientError


# DynamoDB setup — table name comes from environment variable set in Lambda config
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE_NAME", "smart-energy-readings")

# TTL = 7 days — DynamoDB will automatically delete records older than this.
# This keeps the table from growing forever. For a real system you'd archive
# old data to S3 before it expires, but for this project 7 days is plenty.
TTL_DAYS = 7
TTL_SECONDS = TTL_DAYS * 24 * 60 * 60

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMODB_TABLE)


# ── Helpers ───────────────────────────────────────────────────────────────────

def to_decimal(value):
    # DynamoDB doesn't accept Python floats — it requires Decimal for all numbers.
    # The safest way to convert is to go through a string first, because
    # float → Decimal can introduce floating point weirdness.
    # e.g. Decimal(3.42) might become 3.41999999999... but Decimal("3.42") is exact.
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def safe_sensor_avg(sensors, sensor_type):
    # Pull the average value for a sensor type out of the sensors dict.
    # Returns None if the sensor data is missing or was all invalid this window.
    sensor_data = sensors.get(sensor_type)
    if sensor_data is None:
        return None
    return to_decimal(sensor_data.get("avg"))


def decode_kinesis_record(record):
    # Kinesis delivers each record's data as a base64-encoded string.
    # Step 1: get the base64 string
    # Step 2: decode it back to raw bytes
    # Step 3: decode the bytes as UTF-8 to get a JSON string
    # Step 4: parse the JSON string into a Python dict
    encoded_data = record["kinesis"]["data"]
    decoded_bytes = base64.b64decode(encoded_data)
    json_string   = decoded_bytes.decode("utf-8")
    payload       = json.loads(json_string)
    return payload


# ── Main Handler ──────────────────────────────────────────────────────────────

def handler(event, context):
    # This is the function Lambda calls. 'event' contains the Kinesis records.
    # 'context' has metadata about the Lambda invocation (we don't need it here).

    records = event.get("Records", [])
    print(f"[LAMBDA] Received {len(records)} record(s) from Kinesis.")

    success_count = 0
    error_count   = 0

    for record in records:
        try:
            payload = decode_kinesis_record(record)
            _write_to_dynamodb(payload)
            success_count += 1

        except json.JSONDecodeError as e:
            # The data wasn't valid JSON — log it and move on.
            # Don't let one bad record fail the whole batch.
            print(f"[LAMBDA] ERROR: Could not parse JSON from Kinesis record — {e}")
            error_count += 1

        except ClientError as e:
            # Something went wrong talking to DynamoDB — log the full error.
            # AWS errors have a useful error code in the response.
            error_code = e.response["Error"]["Code"]
            print(f"[LAMBDA] DynamoDB error ({error_code}): {e}")
            error_count += 1

        except Exception as e:
            # Catch-all for anything unexpected — we still want to process
            # the rest of the records rather than crashing out entirely.
            print(f"[LAMBDA] Unexpected error processing record: {e}")
            error_count += 1

    print(f"[LAMBDA] Done — {success_count} written, {error_count} failed.")

    return {
        "statusCode": 200,
        "records_processed": success_count,
        "records_failed":    error_count,
    }


def _write_to_dynamodb(payload):
    # Extract the fields we care about from the fog node payload.
    # Using .get() with fallbacks everywhere because a partially malformed
    # payload shouldn't crash the function.
    home_id      = payload.get("home_id", "unknown")
    timestamp    = payload.get("timestamp", "")
    fog_node_id  = payload.get("fog_node_id", "unknown")
    energy_mode  = payload.get("energy_mode", "UNKNOWN")
    alert_flag   = payload.get("alert", False)
    sensors      = payload.get("sensors", {})
    elec_price   = payload.get("electricity_price")

    # Log a clear warning for any alert so it shows up easily in CloudWatch Logs.
    # CloudWatch Logs Insights can filter on "[ALERT]" to find all alert records.
    if alert_flag:
        display_home = home_id.replace("home_", "Home-")
        print(f"[ALERT] {display_home} | {energy_mode} detected at {timestamp}")

    # TTL: current time + 7 days, as a Unix timestamp integer.
    # DynamoDB's TTL feature periodically scans for expired items and deletes them.
    # The attribute must be a number (Unix epoch seconds) — DynamoDB handles the rest.
    ttl_value = int(time.time()) + TTL_SECONDS

    # Build the DynamoDB item.
    # home_id = partition key, timestamp = sort key.
    # Together they uniquely identify one processing window for one home.
    item = {
        "home_id":           home_id,
        "timestamp":         timestamp,
        "fog_node_id":       fog_node_id,
        "energy_mode":       energy_mode,
        "alert_flag":        alert_flag,
        "ttl":               ttl_value,

        # Flatten sensor averages into top-level attributes for easy querying.
        # Storing the full nested sensors dict would work too, but flat attributes
        # are simpler to use in DynamoDB queries and dashboard code.
        "solar_avg":         safe_sensor_avg(sensors, "solar_panel"),
        "grid_avg":          safe_sensor_avg(sensors, "grid_import"),
        "battery_avg":       safe_sensor_avg(sensors, "battery_storage"),
        "ev_avg":            safe_sensor_avg(sensors, "ev_charger"),
        "temperature_avg":   safe_sensor_avg(sensors, "temperature"),
    }

    # Add electricity price if the fog node included it — it's optional
    if elec_price is not None:
        item["electricity_price"] = to_decimal(elec_price)

    # Remove any keys where the value is None — DynamoDB doesn't accept None attributes.
    # (It accepts a null type, but boto3's put_item doesn't auto-convert None to null.)
    item = {k: v for k, v in item.items() if v is not None}

    table.put_item(Item=item)

    print(f"[LAMBDA] Written to DynamoDB — {home_id} | {energy_mode} | {timestamp}")
