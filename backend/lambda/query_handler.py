# query_handler.py
#
# AWS Lambda function — serves sensor data as a JSON API for Grafana.
#
# How this works:
#   Grafana is configured to use a "JSON API" data source plugin that polls this
#   endpoint every 30 seconds. It calls GET /?home_id=home_1&hours=1 and we return
#   a JSON array of readings. Grafana plots them on time-series panels.
#
# What is a Lambda Function URL?
#   Normally to expose a Lambda as an HTTP endpoint you'd put API Gateway in front of it.
#   Lambda Function URLs are a simpler option AWS added in 2022 — you get a direct HTTPS
#   URL for your function without setting up API Gateway. The event structure is similar
#   to API Gateway proxy events. Good enough for a project like this.
#
# Why separate ingest_handler and query_handler?
#   Single responsibility — each function does one thing. The ingest function has IAM
#   permissions to write to DynamoDB. The query function only needs read permissions.
#   If Grafana gets compromised, it can't write or delete data. Also, you can scale
#   them independently — reads and writes have different traffic patterns.

import json
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError


DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE_NAME", "smart-energy-readings")

# Known home IDs — comma-separated in env var so it's easy to add homes later.
# e.g. HOME_IDS=home_1,home_2,home_3
HOME_IDS = [h.strip() for h in os.environ.get("HOME_IDS", "home_1,home_2,home_3").split(",")]

# Max hours a caller can request — prevent someone asking for 10 years of data
MAX_HOURS = 24

dynamodb = boto3.resource("dynamodb")
table    = dynamodb.Table(DYNAMODB_TABLE)


# ── Response helpers ──────────────────────────────────────────────────────────

# CORS headers are needed because Grafana runs in a browser and makes cross-origin
# requests to this Lambda URL. Without these headers the browser blocks the response.
CORS_HEADERS = {
    "Content-Type":                 "application/json",
    "Access-Control-Allow-Origin":  "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
}


def make_response(status_code, body):
    # Helper to build a properly formatted Lambda Function URL response.
    # statusCode, headers, and body are all required by the Lambda URL spec.
    return {
        "statusCode": status_code,
        "headers":    CORS_HEADERS,
        "body":       json.dumps(body, default=_json_serialiser),
    }


def _json_serialiser(obj):
    # boto3 returns DynamoDB numbers as Decimal, which json.dumps can't handle by default.
    # This custom serialiser converts Decimal to float before encoding.
    # We pass it as the 'default' argument to json.dumps so it's only called for
    # types that json doesn't know how to serialise on its own.
    if isinstance(obj, Decimal):
        return float(obj)
    raise TypeError(f"Object of type {type(obj)} is not JSON serialisable")


def format_item(item):
    # Convert a raw DynamoDB item into a clean dict for the API response.
    # Only include fields Grafana actually needs — strip internal stuff like ttl.
    return {
        "timestamp":          item.get("timestamp"),
        "home_id":            item.get("home_id"),
        "energy_mode":        item.get("energy_mode"),
        "alert_flag":         item.get("alert_flag", False),
        "solar_avg":          float(item["solar_avg"])       if "solar_avg"       in item else None,
        "grid_avg":           float(item["grid_avg"])        if "grid_avg"        in item else None,
        "battery_avg":        float(item["battery_avg"])     if "battery_avg"     in item else None,
        "ev_avg":             float(item["ev_avg"])          if "ev_avg"          in item else None,
        "temperature_avg":    float(item["temperature_avg"]) if "temperature_avg" in item else None,
        "electricity_price":  float(item["electricity_price"]) if "electricity_price" in item else None,
    }


# ── DynamoDB query helpers ────────────────────────────────────────────────────

def query_home_range(home_id, from_ts_str):
    # Query DynamoDB for all readings from a specific home within the time range.
    #
    # DynamoDB query() uses the partition key (home_id) to go straight to
    # the right slice of the table, then filters on the sort key (timestamp).
    # Because our timestamps are ISO 8601 strings, lexicographic comparison
    # gives us the correct chronological order — "2024-01" < "2024-02" etc.
    #
    # ScanIndexForward=True means results come back oldest-first, which is
    # what Grafana expects for time-series data.
    items = []

    # handle_readings() to handle pagination — DynamoDB returns max 1MB per call.
    # LastEvaluatedKey being present means there are more pages to fetch.
    kwargs = {
        "KeyConditionExpression": (
            Key("home_id").eq(home_id) &
            Key("timestamp").gte(from_ts_str)
        ),
        "ScanIndexForward": True,  # oldest first
    }

    while True:
        response = table.query(**kwargs)
        items.extend(response.get("Items", []))

        # If LastEvaluatedKey is in the response, there are more pages
        last_key = response.get("LastEvaluatedKey")
        if not last_key:
            break
        kwargs["ExclusiveStartKey"] = last_key

    return items


def query_home_latest(home_id):
    # Get just the most recent reading for a home.
    # ScanIndexForward=False reverses the sort order so the newest item comes first.
    # Limit=1 means we only fetch one item — no point loading more if we just want current state.
    response = table.query(
        KeyConditionExpression=Key("home_id").eq(home_id),
        ScanIndexForward=False,
        Limit=1,
    )
    items = response.get("Items", [])
    return items[0] if items else None


# ── Route handlers ────────────────────────────────────────────────────────────

def handle_readings(params):
    # Main data endpoint — returns time-series readings for one or all homes.
    #
    # Query params:
    #   home_id  (optional) — if omitted, returns data for all homes
    #   hours    (optional, default 1) — how many hours back to look

    # Parse 'hours' param — validate it's a sensible number
    try:
        hours = int(params.get("hours", 1))
        if hours < 1 or hours > MAX_HOURS:
            return make_response(400, {
                "error": f"'hours' must be between 1 and {MAX_HOURS}."
            })
    except ValueError:
        return make_response(400, {"error": "'hours' must be an integer."})

    # Calculate the start of the query window as an ISO string.
    # DynamoDB timestamp comparison is lexicographic so the format must be consistent —
    # both stored timestamps and this query timestamp use the same isoformat() output.
    from_dt     = datetime.now(timezone.utc) - timedelta(hours=hours)
    from_ts_str = from_dt.isoformat()

    # Decide which homes to query
    requested_home = params.get("home_id")
    if requested_home:
        if requested_home not in HOME_IDS:
            return make_response(400, {
                "error": f"Unknown home_id '{requested_home}'. Valid: {HOME_IDS}"
            })
        homes_to_query = [requested_home]
    else:
        homes_to_query = HOME_IDS  # query all homes

    # Run the queries and collect results
    all_readings = []
    for home_id in homes_to_query:
        items = query_home_range(home_id, from_ts_str)
        all_readings.extend([format_item(item) for item in items])

    # Sort everything by timestamp — needed when combining multiple homes
    all_readings.sort(key=lambda r: r["timestamp"])

    return make_response(200, {
        "status":   "ok",
        "query": {
            "home_id": requested_home or "all",
            "hours":   hours,
            "from":    from_ts_str,
        },
        "count":    len(all_readings),
        "readings": all_readings,
    })


def handle_summary(params):
    # Summary endpoint — returns only the latest reading for each home.
    # Grafana stat panels use this: "what is the battery level RIGHT NOW?".
    # Much cheaper than fetching an hour of history just to show the latest value.

    summary = []
    for home_id in HOME_IDS:
        item = query_home_latest(home_id)
        if item:
            summary.append(format_item(item))

    return make_response(200, {
        "status":  "ok",
        "summary": summary,
        "count":   len(summary),
    })


# ── Main handler ──────────────────────────────────────────────────────────────

def handler(event, context):
    # Entry point — Lambda calls this for every HTTP request to the Function URL.

    # Extract request method and path from the event.
    # Lambda Function URL puts these inside requestContext.http.
    http_context = event.get("requestContext", {}).get("http", {})
    method       = http_context.get("method", "GET").upper()
    path         = http_context.get("path", "/")
    params       = event.get("queryStringParameters") or {}

    print(f"[QUERY] {method} {path} | params: {params}")

    # Handle CORS preflight — browsers send OPTIONS before cross-origin requests.
    # We just confirm we accept the request method and headers, return 200.
    if method == "OPTIONS":
        return make_response(200, {})

    if method != "GET":
        return make_response(405, {"error": "Method not allowed. Use GET."})

    try:
        if path == "/summary":
            return handle_summary(params)
        else:
            # Treat any other path (including /) as the readings endpoint
            return handle_readings(params)

    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        print(f"[QUERY] DynamoDB error ({error_code}): {e}")
        return make_response(500, {
            "error": "Database error. Check CloudWatch logs for details."
        })

    except Exception as e:
        print(f"[QUERY] Unexpected error: {e}")
        return make_response(500, {
            "error": "Internal server error. Check CloudWatch logs."
        })
