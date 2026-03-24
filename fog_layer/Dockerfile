# Dockerfile — Fog Node
#
# Why containerise the fog node?
#   The whole point of fog computing is that processing happens close to the sensors —
#   ideally on a small device like a Raspberry Pi sitting in the building.
#   Docker makes it easy to move the fog node from a laptop (for development) to real
#   edge hardware without changing any code. You just build the image and run it.
#   Same container works on Mac, Linux, a Pi — that's the portability argument.
#
# Build context: this Dockerfile is built from the PROJECT ROOT (not fog_layer/).
# That's because we need access to requirements.txt which lives at the root.
# In docker-compose.yml the context is set to ".." (one level up from fog_layer/).

# ── Base image ────────────────────────────────────────────────────────────────
# python:3.11-slim is a minimal Debian image with Python already installed.
# "slim" means it strips out a lot of stuff we don't need (docs, test files etc.)
# so the final image is much smaller — about 50MB vs 900MB for the full image.
FROM python:3.11-slim

# ── Working directory ─────────────────────────────────────────────────────────
# WORKDIR creates the directory if it doesn't exist and sets it as the default
# for all following commands. It's like doing "mkdir /app && cd /app".
WORKDIR /app

# ── Install dependencies ──────────────────────────────────────────────────────
# We copy requirements.txt BEFORE copying the rest of the code.
# This is a Docker caching trick — if requirements.txt hasn't changed,
# Docker reuses the cached pip install layer instead of re-downloading everything.
# Only when requirements.txt changes does it re-run pip install.
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt
# --no-cache-dir saves space in the image by not keeping pip's download cache.

# ── Copy fog layer source files ───────────────────────────────────────────────
# Copy just the Python files we need — not the whole project.
# Paths here are relative to the build context (project root).
COPY fog_layer/fog_node.py        .
COPY fog_layer/data_processor.py  .
COPY fog_layer/price_fetcher.py   .
COPY fog_layer/cloud_dispatcher.py .

# ── Default environment variables ─────────────────────────────────────────────
# These are the defaults baked into the image. They can be overridden at runtime
# by docker-compose (or by passing -e flags to docker run).
#
# IMPORTANT: MQTT_BROKER_HOST is "mosquitto" — NOT "localhost".
# Inside Docker, containers talk to each other by service name, not localhost.
# "mosquitto" is the name of the broker service defined in docker-compose.yml.
# If you ran the fog node directly on your Mac you'd use localhost, but inside
# Docker you use the service name. This tripped me up at first.
ENV MQTT_BROKER_HOST=mosquitto
ENV MQTT_BROKER_PORT=1883
ENV FOG_NODE_ID=fog_node_docker_01

# ── Entry point ───────────────────────────────────────────────────────────────
# CMD is the command that runs when the container starts.
# Using a list format (not a string) is best practice — it avoids wrapping
# the command in a shell, which means signals like Ctrl+C work properly.
CMD ["python", "fog_node.py"]
