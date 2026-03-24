# Dockerfile — Sensor Layer
#
# Packages the sensor_manager and all 5 sensor classes into a container.
# Once built and pushed to Docker Hub, this runs identically on any machine
# without needing Python or pip installed locally.
#
# Build context: project root (one level up from sensor_layer/).
# This is needed so we can copy requirements.txt which lives at the root.
# In docker-compose.yml the context is set to ".." (project root).
#
# IMPORTANT: Inside Docker, sensors connect to the Mosquitto broker using
# the service name "mosquitto" (not "localhost"). The MQTT_BROKER_HOST
# environment variable is set to "mosquitto" by docker-compose at runtime.

FROM python:3.11-slim

WORKDIR /app

# Copy and install dependencies first — Docker caches this layer.
# If requirements.txt hasn't changed, Docker reuses the cached layer
# and skips re-downloading packages on every build.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire sensor_layer folder into the container.
# This preserves the sensors/ subfolder structure that sensor_manager.py
# expects when it adds sensors/ to sys.path at startup.
COPY sensor_layer/ ./sensor_layer/

# Default environment — overridden at runtime by docker-compose.
# "mosquitto" is the Docker service name, not localhost.
# Inside Docker, containers reach each other by service name.
ENV MQTT_BROKER_HOST=mosquitto
ENV MQTT_BROKER_PORT=1883

# Run sensor_manager.py which starts all 15 sensor threads.
CMD ["python", "sensor_layer/sensor_manager.py"]
