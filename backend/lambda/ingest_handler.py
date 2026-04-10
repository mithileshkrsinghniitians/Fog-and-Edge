# ====================== Task Performed By ingest_handler.py ======================
#
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
# ======================================== END ========================================

import json
import base64
import os
import time
from decimal import Decimal, InvalidOperation

import boto3
from botocore.exceptions import ClientError

# DynamoDB setup — table name comes from environment variable set in Lambda config:
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE_NAME", "smart-energy-readings")

# TTL = 7 days — DynamoDB will automatically delete records older than this. This keeps the table from growing forever:
TTL_DAYS = 7
TTL_SECONDS = TTL_DAYS * 24 * 60 * 60

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(DYNAMODB_TABLE)


# Helpers:
def to_decimal(value):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def safe_sensor_avg(sensors, sensor_type):
    sensor_data = sensors.get(sensor_type)
    if sensor_data is None:
        return None
    return to_decimal(sensor_data.get("avg"))


def decode_kinesis_record(record):
    encoded_data = record["kinesis"]["data"]
    decoded_bytes = base64.b64decode(encoded_data)
    json_string   = decoded_bytes.decode("utf-8")
    payload       = json.loads(json_string)
    return payload


# Main Handler:
def handler(event, context):
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
            print(f"[LAMBDA] ERROR: Could not parse JSON from Kinesis record — {e}")
            error_count += 1

        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            print(f"[LAMBDA] DynamoDB error ({error_code}): {e}")
            error_count += 1

        except Exception as e:
            print(f"[LAMBDA] Unexpected error processing record: {e}")
            error_count += 1

    print(f"[LAMBDA] Done — {success_count} written, {error_count} failed.")

    return {
        "statusCode": 200,
        "records_processed": success_count,
        "records_failed":    error_count,
    }


def _write_to_dynamodb(payload):
    home_id      = payload.get("home_id", "unknown")
    timestamp    = payload.get("timestamp", "")
    fog_node_id  = payload.get("fog_node_id", "unknown")
    energy_mode  = payload.get("energy_mode", "UNKNOWN")
    alert_flag   = payload.get("alert", False)
    sensors      = payload.get("sensors", {})
    elec_price   = payload.get("electricity_price")

    if alert_flag:
        display_home = home_id.replace("home_", "Home-")
        print(f"[ALERT] {display_home} | {energy_mode} detected at {timestamp}")

    ttl_value = int(time.time()) + TTL_SECONDS

    item = {
        "home_id":           home_id,
        "timestamp":         timestamp,
        "fog_node_id":       fog_node_id,
        "energy_mode":       energy_mode,
        "alert_flag":        alert_flag,
        "ttl":               ttl_value,

        "solar_avg":         safe_sensor_avg(sensors, "solar_panel"),
        "grid_avg":          safe_sensor_avg(sensors, "grid_import"),
        "battery_avg":       safe_sensor_avg(sensors, "battery_storage"),
        "ev_avg":            safe_sensor_avg(sensors, "ev_charger"),
        "temperature_avg":   safe_sensor_avg(sensors, "temperature"),
    }

    if elec_price is not None:
        item["electricity_price"] = to_decimal(elec_price)

    item = {k: v for k, v in item.items() if v is not None}

    table.put_item(Item=item)

    print(f"[LAMBDA] Written to DynamoDB — {home_id} | {energy_mode} | {timestamp}")
