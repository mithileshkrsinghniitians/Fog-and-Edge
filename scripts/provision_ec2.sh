#!/bin/bash
# scripts/provision_ec2.sh
#
# Provisions a new EC2 instance for the Smart Energy Grid fog layer.
# Uses the AWS CLI. Run this once from your local machine.
#
# What it does:
#   1. Creates an EC2 key pair → saves to ~/.ssh/smart-energy-grid.pem
#   2. Creates a security group with ports 22, 1883, 9001 open
#   3. Launches an Ubuntu 22.04 t2.micro instance (free-tier eligible)
#   4. Waits for the instance to be running
#   5. Prints the public IP and SSH command
#
# Prerequisites:
#   - AWS CLI installed and configured (aws configure)
#   - Default region: us-east-1
#
# Usage:
#   chmod +x scripts/provision_ec2.sh
#   ./scripts/provision_ec2.sh

set -e

REGION="us-east-1"
INSTANCE_TYPE="t2.micro"
KEY_NAME="smart-energy-grid-key"
SG_NAME="smart-energy-grid-sg"
KEY_FILE="$HOME/.ssh/smart-energy-grid.pem"

echo "============================================="
echo "  Smart Energy Grid — EC2 Provisioning"
echo "  Region: $REGION | Type: $INSTANCE_TYPE"
echo "============================================="

# ── Step 1: Create key pair ─────────────────────────────────────
echo ""
echo "[1/5] Creating EC2 key pair: $KEY_NAME"

# Check if key pair already exists
if aws ec2 describe-key-pairs --key-names "$KEY_NAME" --region "$REGION" &>/dev/null; then
    echo "      Key pair '$KEY_NAME' already exists. Skipping creation."
    echo "      Assuming private key is at: $KEY_FILE"
else
    aws ec2 create-key-pair \
        --key-name "$KEY_NAME" \
        --query "KeyMaterial" \
        --output text \
        --region "$REGION" > "$KEY_FILE"

    chmod 400 "$KEY_FILE"
    echo "      Private key saved to: $KEY_FILE"
fi

# ── Step 2: Create security group ───────────────────────────────
echo ""
echo "[2/5] Creating security group: $SG_NAME"

# Check if security group already exists
SG_ID=$(aws ec2 describe-security-groups \
    --filters "Name=group-name,Values=$SG_NAME" \
    --query "SecurityGroups[0].GroupId" \
    --output text \
    --region "$REGION" 2>/dev/null || echo "None")

if [ "$SG_ID" != "None" ] && [ -n "$SG_ID" ]; then
    echo "      Security group '$SG_NAME' already exists: $SG_ID"
else
    SG_ID=$(aws ec2 create-security-group \
        --group-name "$SG_NAME" \
        --description "Smart Energy Grid: SSH, MQTT, WebSocket MQTT" \
        --query "GroupId" \
        --output text \
        --region "$REGION")

    echo "      Created security group: $SG_ID"

    # Port 22 — SSH access (restricted to your IP)
    MY_IP=$(curl -s https://checkip.amazonaws.com)
    echo "      Your public IP: $MY_IP (SSH access granted for this IP only)"

    aws ec2 authorize-security-group-ingress \
        --group-id "$SG_ID" \
        --protocol tcp --port 22 \
        --cidr "${MY_IP}/32" \
        --region "$REGION"

    # Port 1883 — MQTT (open so sensors can connect)
    aws ec2 authorize-security-group-ingress \
        --group-id "$SG_ID" \
        --protocol tcp --port 1883 \
        --cidr 0.0.0.0/0 \
        --region "$REGION"

    # Port 9001 — WebSocket MQTT (open for browser clients)
    aws ec2 authorize-security-group-ingress \
        --group-id "$SG_ID" \
        --protocol tcp --port 9001 \
        --cidr 0.0.0.0/0 \
        --region "$REGION"

    echo "      Opened ports: 22 (SSH, $MY_IP only), 1883 (MQTT), 9001 (WS-MQTT)"
fi

# ── Step 3: Get latest Ubuntu 22.04 LTS AMI ─────────────────────
echo ""
echo "[3/5] Finding latest Ubuntu 22.04 LTS AMI..."

AMI_ID=$(aws ec2 describe-images \
    --owners 099720109477 \
    --filters \
        "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
        "Name=state,Values=available" \
    --query "sort_by(Images, &CreationDate)[-1].ImageId" \
    --output text \
    --region "$REGION")

echo "      AMI: $AMI_ID (Ubuntu 22.04 LTS)"

# ── Step 4: Launch EC2 instance ─────────────────────────────────
echo ""
echo "[4/5] Launching EC2 instance ($INSTANCE_TYPE)..."

INSTANCE_ID=$(aws ec2 run-instances \
    --image-id "$AMI_ID" \
    --instance-type "$INSTANCE_TYPE" \
    --key-name "$KEY_NAME" \
    --security-group-ids "$SG_ID" \
    --count 1 \
    --tag-specifications \
        "ResourceType=instance,Tags=[{Key=Name,Value=smart-energy-grid-fog-node},{Key=Project,Value=SmartEnergyGrid}]" \
    --query "Instances[0].InstanceId" \
    --output text \
    --region "$REGION")

echo "      Instance ID: $INSTANCE_ID"
echo "      Waiting for instance to reach 'running' state..."

aws ec2 wait instance-running \
    --instance-ids "$INSTANCE_ID" \
    --region "$REGION"

echo "      Instance is running."

# ── Step 5: Get public IP and print instructions ─────────────────
echo ""
echo "[5/5] Retrieving public IP address..."

PUBLIC_IP=$(aws ec2 describe-instances \
    --instance-ids "$INSTANCE_ID" \
    --query "Reservations[0].Instances[0].PublicIpAddress" \
    --output text \
    --region "$REGION")

PUBLIC_DNS=$(aws ec2 describe-instances \
    --instance-ids "$INSTANCE_ID" \
    --query "Reservations[0].Instances[0].PublicDnsName" \
    --output text \
    --region "$REGION")

# Save instance details for use by other scripts
cat > .ec2_instance << EOF
INSTANCE_ID=$INSTANCE_ID
PUBLIC_IP=$PUBLIC_IP
PUBLIC_DNS=$PUBLIC_DNS
EC2_USER=ubuntu
KEY_FILE=$KEY_FILE
EOF

echo ""
echo "============================================="
echo "  EC2 INSTANCE READY"
echo "============================================="
echo ""
echo "  Instance ID : $INSTANCE_ID"
echo "  Public IP   : $PUBLIC_IP"
echo "  Public DNS  : $PUBLIC_DNS"
echo "  SSH Key     : $KEY_FILE"
echo ""
echo "  SSH command:"
echo "    ssh -i $KEY_FILE ubuntu@$PUBLIC_IP"
echo ""
echo "  NEXT STEPS:"
echo "  ─────────────────────────────────────────"
echo "  1. Wait ~30 seconds for SSH to become ready, then run:"
echo "     ./scripts/setup_ec2.sh"
echo ""
echo "  2. Copy your .env and certs to EC2:"
echo "     ./scripts/copy_secrets.sh"
echo ""
echo "  3. Add these secrets to GitHub (Settings > Secrets > Actions):"
echo "     EC2_HOST    = $PUBLIC_IP"
echo "     EC2_USER    = ubuntu"
echo "     EC2_SSH_KEY = (paste contents of $KEY_FILE)"
echo ""
echo "  4. Add Docker Hub secrets:"
echo "     DOCKERHUB_USERNAME = mithileshkrsinghniitians"
echo "     DOCKERHUB_TOKEN    = (your Docker Hub access token)"
echo ""
echo "  5. Add AWS secret:"
echo "     AWS_IOT_ENDPOINT   = (from AWS Console > IoT Core > Settings)"
echo "============================================="