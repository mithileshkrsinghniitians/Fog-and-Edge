# Running the Fog Layer with Docker

## What Docker is doing here

Docker takes our fog node Python code and packages it into a **container** — a
self-contained box that includes Python, all the pip packages, and our code.
The container runs the same way on any machine, which is the whole point.

In a real smart grid deployment, you'd copy this container onto edge hardware
(a Raspberry Pi, an industrial gateway, or a mini-PC in a comms cabinet) and
run it there. Docker makes that easy because you don't have to install Python
or any dependencies on the target device — just Docker.

Our `docker-compose.yml` defines **two services** that start together:

```
┌─────────────────────────────────────────┐
│              Docker Network             │
│                                         │
│  ┌─────────────┐    ┌────────────────┐  │
│  │  mosquitto  │◄───│   fog-node     │  │
│  │ (MQTT broker│    │ (fog_node.py)  │  │
│  │  port 1883) │    │                │  │
│  └─────────────┘    └────────────────┘  │
│                                         │
└─────────────────────────────────────────┘
        ▲
        │ sensors connect here from your Mac
   localhost:1883
```

The fog node connects to mosquitto using the hostname `mosquitto` (the service name)
rather than `localhost` — inside Docker, containers find each other by service name.

---

## Before you start

Make sure Docker Desktop is running on your Mac.
Check by running: `docker --version`

Also make sure your `.env` file exists at the project root.
The compose file reads AWS credentials from it at startup.
(If you haven't set up AWS yet, that's fine — the fog node runs in local-only mode.)

---

## Start everything — one command

From the `fog_layer/` folder:

```bash
docker-compose up
```

This will:
1. Pull the `eclipse-mosquitto:2.0` image (first time only — takes ~10 seconds)
2. Build the fog node image from the Dockerfile (first time ~30 seconds, cached after)
3. Start both containers
4. Stream logs from both services into your terminal

You'll see output like:
```
smart_grid_mosquitto  | Starting Mosquitto MQTT broker
smart_grid_fog_node   | [FOG] Connecting to MQTT broker at mosquitto:1883
smart_grid_fog_node   | [FOG] Connected to MQTT broker.
smart_grid_fog_node   | [FOG] Subscribed to topic: home/#
```

To run in the background instead (you get your terminal back):
```bash
docker-compose up -d
```

---

## Checking logs

Both services running, want to see what's happening?

```bash
# Tail logs from BOTH services at once
docker-compose logs -f

# Tail logs from fog node only
docker-compose logs -f fog-node

# Tail logs from broker only
docker-compose logs -f mosquitto

# See the last 50 lines from fog node
docker-compose logs --tail=50 fog-node
```

The `-f` flag means "follow" — it keeps streaming new log lines as they appear.
Press Ctrl+C to stop following (the containers keep running).

---

## Rebuild after code changes

If you change any Python file in `fog_layer/`, you need to rebuild the image:

```bash
docker-compose up --build
```

The `--build` flag tells Docker to rebuild the fog-node image even if it already exists.
Without it, Docker would use the cached old image and your changes wouldn't appear.

You don't need to rebuild if you only change `.env` — those values are read at startup.

---

## Stop everything cleanly

```bash
docker-compose down
```

This stops both containers and removes them. The named volumes (mosquitto data and logs)
are kept so you don't lose retained MQTT messages between restarts.

To also delete the volumes (full clean slate):
```bash
docker-compose down -v
```

---

## Useful container commands

```bash
# See which containers are running
docker-compose ps

# Open a shell inside the fog node container (useful for debugging)
docker exec -it smart_grid_fog_node bash

# See resource usage (CPU/memory) for both containers
docker stats

# View Docker images on your machine
docker images | grep smart
```

---

## Connecting sensors to the containerised broker

The sensors run on your Mac (outside Docker), connecting to `localhost:1883`.
This works because the mosquitto service maps its internal port 1883 to
`localhost:1883` on your Mac (the `ports:` section in docker-compose.yml).

So the flow is:
```
sensor_manager.py (Mac)
       │  publishes to localhost:1883
       ▼
Docker port mapping (1883:1883)
       │
       ▼
mosquitto container (port 1883 inside Docker)
       │  routes message
       ▼
fog-node container (subscribed to home/#)
```

Your `.env` file should have `MQTT_BROKER_HOST=localhost` for the sensors
(they're on your Mac), while the fog node uses `MQTT_BROKER_HOST=mosquitto`
(it's inside Docker, so it uses the service name).

---

## Troubleshooting

**"Cannot connect to Docker daemon"**
Docker Desktop isn't running. Open it from Applications.

**"Port 1883 already in use"**
You have a local Mosquitto installation running alongside Docker.
Stop it: `brew services stop mosquitto`

**Fog node keeps restarting**
Check the logs: `docker-compose logs fog-node`
Usually means it can't connect to the broker. Make sure mosquitto started first
(it should — depends_on handles this).

**Build fails with "requirements.txt not found"**
The docker-compose build context must be the project root (`context: ..`).
Double-check the docker-compose.yml has `context: ..` under the fog-node build section.
