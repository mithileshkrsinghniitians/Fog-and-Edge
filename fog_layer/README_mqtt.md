# MQTT Broker — Fog Layer

## What is MQTT?

MQTT stands for Message Queuing Telemetry Transport. It's a lightweight messaging
protocol designed for devices that need to send small amounts of data frequently
over potentially unreliable networks.

The basic idea is **publish/subscribe**:
- A **sensor** (publisher) sends a message to a **topic** — e.g. `home/home_1/solar_panel`
- Anything interested in that data (a subscriber) connects to the broker and says
  "give me everything on that topic"
- The **broker** sits in the middle and routes messages from publishers to subscribers

Neither side needs to know about the other. The sensor doesn't care who's listening.
The subscriber doesn't care where the data comes from. The broker handles it all.

---

## Why MQTT instead of HTTP?

My first instinct was to use HTTP — just POST the sensor reading to an endpoint every
5 seconds. That would work, but it's not ideal for IoT for a few reasons:

| | HTTP | MQTT |
|---|---|---|
| Connection | Opens and closes every request | Stays connected (persistent) |
| Overhead | Headers, handshake each time | Very small packet size |
| Direction | One request, one response | One message, many subscribers |
| Power use | Higher (reconnecting constantly) | Lower (connection stays open) |
| Designed for | Web pages, APIs | Sensors, IoT, low-bandwidth |

For 15 sensors sending data every 5 seconds, MQTT is much more efficient.
It also makes it easy to add new subscribers later (like a fog node, a dashboard,
a cloud uplink) without touching the sensors at all.

---

## Architecture in this project

```
[solar_sensor]  ──┐
[grid_sensor]   ──┤
[battery_sensor]──┼──► MQTT Broker (Mosquitto) ──► [fog_node]
[ev_sensor]     ──┤         port 1883               [dashboard]
[thermostat]    ──┘         port 9001 (WS)          [AWS IoT Core]
     × 3 homes
```

Topics follow the pattern: `home/{home_id}/{sensor_type}`
For example: `home/home_2/ev_charger`

---

## Starting the broker

Make sure Docker Desktop is running, then from the `fog_layer/` folder:

```bash
docker-compose up -d
```

The `-d` flag runs it in the background (detached mode) so it doesn't block
your terminal. The broker will now be listening on `localhost:1883`.

To check it's running:

```bash
docker-compose ps
```

To watch the live logs (useful for debugging):

```bash
docker-compose logs -f mosquitto
```

To stop the broker:

```bash
docker-compose down
```

---

## Testing the broker manually

If you have the Mosquitto client tools installed (`brew install mosquitto`),
you can test publish/subscribe from the terminal without running any Python.

**Terminal 1 — subscribe to everything:**
```bash
mosquitto_sub -h localhost -p 1883 -t "home/#" -v
```
The `#` is a wildcard — it matches any topic that starts with `home/`.

**Terminal 2 — publish a test message:**
```bash
mosquitto_pub -h localhost -p 1883 -t "home/home_1/solar_panel" -m '{"value": 3.2, "unit": "kW"}'
```

You should see the message appear in Terminal 1 immediately. That confirms
the broker is working and routing messages correctly.

---

## Troubleshooting

**"Connection refused" when starting sensors:**
The broker isn't running. Run `docker-compose up -d` from the `fog_layer/` folder.

**Port 1883 already in use:**
You might have a local Mosquitto installation running.
Stop it with: `brew services stop mosquitto`
Then start the Docker version again.

**Container starts but sensors can't connect:**
Check the logs: `docker-compose logs mosquitto`
Make sure `MQTT_BROKER_HOST=localhost` is set in your `.env` file.
