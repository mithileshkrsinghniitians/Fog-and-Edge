#!/bin/bash
# scripts/copy_secrets.sh
#
# Copies sensitive files (.env and TLS certificates) to the EC2 instance.
# This is a one-time manual step — run it once after provisioning.
# Certificates and credentials are NEVER stored in Git.
#
# What it copies:
#   .env         → ~/smart-energy-grid/fog_layer/.env  (AWS credentials)
#   certs/       → ~/smart-energy-grid/certs/          (TLS certificates)
#
# The fog_layer/docker-compose.yml mounts certs from ../certs (one level up),
# which maps to ~/smart-energy-grid/certs/ on EC2.
#
# Usage (run from project root):
#   chmod +x scripts/copy_secrets.sh
#   ./scripts/copy_secrets.sh

set -e

# Load instance details if available
if [ -f ".ec2_instance" ]; then
    source .ec2_instance
    HOST="${PUBLIC_IP}"
else
    HOST="${EC2_HOST}"
    KEY_FILE="${KEY_FILE:-$HOME/.ssh/smart-energy-grid.pem}"
fi

USER="${EC2_USER:-ubuntu}"

if [ -z "$HOST" ]; then
    echo "ERROR: Cannot determine EC2 host."
    echo "Run provision_ec2.sh first, or set: export EC2_HOST=<ip>"
    exit 1
fi

echo "============================================="
echo "  Smart Energy Grid — Copy Secrets to EC2"
echo "  Host: $HOST | User: $USER"
echo "============================================="
echo ""

# ── Check .env exists ───────────────────────────────────────────
if [ ! -f ".env" ]; then
    echo "ERROR: .env file not found in project root."
    echo "Copy .env.example to .env and fill in your AWS credentials."
    exit 1
fi

# ── Check certs exist ───────────────────────────────────────────
CERT_DIR="certs"
REQUIRED_CERTS=(
    "AmazonRootCA1.pem"
    "device-certificate.pem.crt"
    "private.pem.key"
)

echo "Checking required certificates..."
for cert in "${REQUIRED_CERTS[@]}"; do
    if [ ! -f "$CERT_DIR/$cert" ]; then
        echo "ERROR: Missing certificate: $CERT_DIR/$cert"
        echo "Download TLS certs from AWS Console > IoT Core > Security > Certificates"
        exit 1
    fi
    echo "  Found: $CERT_DIR/$cert"
done

echo ""

# ── Copy .env to EC2 ────────────────────────────────────────────
echo "[1/2] Copying .env to EC2..."
scp -i "$KEY_FILE" \
    -o StrictHostKeyChecking=no \
    .env \
    "$USER@$HOST:~/smart-energy-grid/fog_layer/.env"

echo "      .env copied to ~/smart-energy-grid/fog_layer/.env"

# ── Copy certs to EC2 ───────────────────────────────────────────
echo ""
echo "[2/2] Copying TLS certificates to EC2..."
scp -i "$KEY_FILE" \
    -o StrictHostKeyChecking=no \
    -r certs/ \
    "$USER@$HOST:~/smart-energy-grid/"

echo "      certs/ copied to ~/smart-energy-grid/certs/"

# ── Verify on EC2 ───────────────────────────────────────────────
echo ""
echo "Verifying files on EC2..."
ssh -i "$KEY_FILE" \
    -o StrictHostKeyChecking=no \
    "$USER@$HOST" << 'VERIFY'

echo "  .env:"
ls -la ~/smart-energy-grid/fog_layer/.env

echo ""
echo "  certs/:"
ls -la ~/smart-energy-grid/certs/
VERIFY

echo ""
echo "============================================="
echo "  SECRETS COPIED SUCCESSFULLY"
echo "============================================="
echo ""
echo "  Directory structure on EC2:"
echo "    ~/smart-energy-grid/"
echo "    ├── .env (copied)"
echo "    ├── certs/ (copied)"
echo "    │   ├── AmazonRootCA1.pem"
echo "    │   ├── device-certificate.pem.crt"
echo "    │   └── private.pem.key"
echo "    └── fog_layer/"
echo "        ├── docker-compose.yml (deployed by GitHub Actions)"
echo "        └── mosquitto.conf     (deployed by GitHub Actions)"
echo ""
echo "  NEXT: Push to main branch to trigger GitHub Actions deployment."
echo "  Or run manually: ./scripts/build_and_push.sh"