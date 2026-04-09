#!/bin/bash
# scripts/build_and_push.sh
#
# Builds both Docker images from the project root and pushes them to Docker Hub.
# Run this locally when you want to push images manually (without GitHub Actions).
#
# Both Dockerfiles need the project root as build context because they
# copy requirements.txt from the root. The -f flag points to the specific
# Dockerfile inside each layer's subfolder.
#
# Usage (run from project root):
#   chmod +x scripts/build_and_push.sh
#   ./scripts/build_and_push.sh
#
# You must be logged in to Docker Hub:
#   docker login

set -e

DOCKERHUB_USERNAME="${DOCKERHUB_USERNAME:-mithileshkrsinghniitians}"
FOG_IMAGE="$DOCKERHUB_USERNAME/smart-energy-fog-node"
SENSOR_IMAGE="$DOCKERHUB_USERNAME/smart-energy-sensor-manager"

echo "============================================="
echo "  Smart Energy Grid — Build & Push to Docker Hub"
echo "  Docker Hub: $DOCKERHUB_USERNAME"
echo "============================================="
echo ""

# ── Verify Docker is running ────────────────────────────────────
if ! docker info &>/dev/null; then
    echo "ERROR: Docker is not running. Start Docker Desktop first."
    exit 1
fi

# ── Verify logged in to Docker Hub ──────────────────────────────
if ! docker system info | grep -q "Username"; then
    echo "Not logged in to Docker Hub. Running docker login..."
    docker login
fi

# ── Build fog-node ───────────────────────────────────────────────
echo "[1/4] Building fog-node image..."
echo "      Context: . (project root)"
echo "      Dockerfile: fog_layer/Dockerfile"
echo ""

docker build \
    --file fog_layer/Dockerfile \
    --tag "$FOG_IMAGE:latest" \
    --tag "$FOG_IMAGE:$(git rev-parse --short HEAD 2>/dev/null || echo manual)" \
    .

echo ""
echo "      fog-node image built."

# ── Build sensor-manager ─────────────────────────────────────────
echo ""
echo "[2/4] Building sensor-manager image..."
echo "      Context: . (project root)"
echo "      Dockerfile: sensor_layer/Dockerfile"
echo ""

docker build \
    --file sensor_layer/Dockerfile \
    --tag "$SENSOR_IMAGE:latest" \
    --tag "$SENSOR_IMAGE:$(git rev-parse --short HEAD 2>/dev/null || echo manual)" \
    .

echo ""
echo "      sensor-manager image built."

# ── Push fog-node ────────────────────────────────────────────────
echo ""
echo "[3/4] Pushing fog-node to Docker Hub..."
docker push "$FOG_IMAGE:latest"
echo "      Pushed: $FOG_IMAGE:latest"

# ── Push sensor-manager ──────────────────────────────────────────
echo ""
echo "[4/4] Pushing sensor-manager to Docker Hub..."
docker push "$SENSOR_IMAGE:latest"
echo "      Pushed: $SENSOR_IMAGE:latest"

echo ""
echo "============================================="
echo "  BUILD & PUSH COMPLETE"
echo "============================================="
echo ""
echo "  Images on Docker Hub:"
echo "    $FOG_IMAGE:latest"
echo "    $SENSOR_IMAGE:latest"
echo ""
echo "  To deploy to EC2, GitHub Actions will pull these automatically"
echo "  on the next push to main. Or trigger manually from GitHub UI."
echo ""
echo "  To test locally:"
echo "    cd fog_layer && docker compose up"