# Base image:
# python:3.11-slim is a minimal Debian image with Python already installed.
FROM python:3.11-slim

# Working directory:
# WORKDIR creates the directory if it doesn't exist and sets it as the default.
WORKDIR /app

# Install dependencies:
# We copy requirements.txt BEFORE copying the rest of the code.
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copy fog layer source files:
# Copy just the Python files.
COPY fog_layer/fog_node.py        .
COPY fog_layer/data_processor.py  .
COPY fog_layer/price_fetcher.py   .
COPY fog_layer/cloud_dispatcher.py .

# Default environment variables:
# These are the defaults baked into the image. They can be overridden at runtime
ENV MQTT_BROKER_HOST=mosquitto
ENV MQTT_BROKER_PORT=1883
ENV FOG_NODE_ID=fog_node_docker_01

# Entry point:
# CMD is the command that runs when the container starts.
CMD ["python", "fog_node.py"]