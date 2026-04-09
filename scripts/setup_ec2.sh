#!/bin/bash
# scripts/setup_ec2.sh
#
# First-time EC2 instance setup for the Smart Energy Grid.
# Run this from your LOCAL machine after provisioning EC2.
# It SSHs into the instance and installs Docker + Docker Compose.
#
# Usage (run from project root):
#   chmod +x scripts/setup_ec2.sh
#   ./scripts/setup_ec2.sh
#
# Requires .ec2_instance file created by provision_ec2.sh, OR
# set these environment variables manually:
#   EC2_HOST=<public-ip>
#   KEY_FILE=~/.ssh/smart-energy-grid.pem

set -e

# Load instance details if available
if [ -f ".ec2_instance" ]; then
    source .ec2_instance
    HOST="${PUBLIC_IP}"
else
    HOST="${EC2_HOST}"
    KEY_FILE="${KEY_FILE:-$HOME/.ssh/smart-energy-grid.pem}"
fi

if [ -z "$HOST" ]; then
    echo "ERROR: Cannot determine EC2 host."
    echo "Either run provision_ec2.sh first, or set: export EC2_HOST=<ip>"
    exit 1
fi

USER="${EC2_USER:-ubuntu}"

echo "============================================="
echo "  Smart Energy Grid — EC2 First-Time Setup"
echo "  Host: $HOST | User: $USER"
echo "============================================="
echo ""
echo "Connecting to EC2 and installing Docker..."
echo "(This takes 1-2 minutes on a fresh instance)"
echo ""

ssh -i "$KEY_FILE" \
    -o StrictHostKeyChecking=no \
    -o ConnectTimeout=30 \
    "$USER@$HOST" << 'REMOTE_SCRIPT'

set -e

echo "── [1/5] Updating apt package list ──"
sudo apt-get update -q

echo "── [2/5] Installing Docker ──"
# Install Docker using the official convenience script
curl -fsSL https://get.docker.com -o /tmp/get-docker.sh
sudo sh /tmp/get-docker.sh
rm /tmp/get-docker.sh

echo "── [3/5] Adding ubuntu user to docker group ──"
# This allows running docker without sudo
sudo usermod -aG docker ubuntu

echo "── [4/5] Installing Docker Compose plugin ──"
# Docker Compose v2 is included with Docker Engine — no separate install needed.
# Verify it works:
docker compose version 2>/dev/null || echo "Note: docker compose will be available after re-login"

echo "── [5/5] Creating deployment directory structure ──"
mkdir -p ~/smart-energy-grid/fog_layer
mkdir -p ~/smart-energy-grid/certs

echo "── Enabling Docker to start on boot ──"
sudo systemctl enable docker
sudo systemctl start docker

echo ""
echo "============================================="
echo "  EC2 SETUP COMPLETE"
echo "============================================="
echo ""
echo "  Docker version:"
docker --version || sudo docker --version
echo ""
echo "  Directory structure created:"
echo "    ~/smart-energy-grid/"
echo "    ~/smart-energy-grid/fog_layer/"
echo "    ~/smart-energy-grid/certs/"
echo ""
echo "  NEXT: Run scripts/copy_secrets.sh to upload .env and certs"

REMOTE_SCRIPT

echo ""
echo "  EC2 setup complete."
echo ""
echo "  NEXT STEP:"
echo "    ./scripts/copy_secrets.sh"