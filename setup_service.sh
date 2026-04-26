#!/usr/bin/env bash

# setup_service.sh - Install mDNS-Sidecar as a systemd service
# This script should be run on the target Linux machine.

set -euo pipefail

SERVICE_NAME="mdns-sidecar"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_NAME="$(whoami)"

# Ensure we are on Linux
if [[ "$OSTYPE" != "linux-gnu"* ]]; then
    echo "Error: This script is intended for Linux systems."
    exit 1
fi

echo "--- mDNS-Sidecar Service Setup ---"
echo "Repo directory: $REPO_DIR"

# Check if running as root or with sudo
if [[ $EUID -ne 0 ]]; then
   echo "This script must be run with sudo to install the service file."
   echo "Example: sudo ./setup_service.sh"
   exit 1
fi

# If run with sudo, whoami returns 'root'. We might want the original user.
REAL_USER="${SUDO_USER:-$USER_NAME}"
read -p "Enter the user to run the service as [$REAL_USER]: " RUN_USER
RUN_USER="${RUN_USER:-$REAL_USER}"

SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"

echo "Creating service file at $SERVICE_FILE..."

cat <<EOF > "$SERVICE_FILE"
[Unit]
Description=NeonBeam mDNS Discovery Sidecar
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$REPO_DIR
ExecStart=/usr/bin/bash $REPO_DIR/start_sidecar.sh
Restart=always
RestartSec=10
StandardOutput=syslog
StandardError=syslog
SyslogIdentifier=$SERVICE_NAME

[Install]
WantedBy=multi-user.target
EOF

echo "Reloading systemd daemon..."
systemctl daemon-reload

echo "Enabling $SERVICE_NAME service..."
systemctl enable "$SERVICE_NAME"

echo "Starting $SERVICE_NAME service..."
systemctl start "$SERVICE_NAME"

echo "------------------------------------------------"
echo "Setup Complete!"
echo "Check status: sudo systemctl status $SERVICE_NAME"
echo "View logs:    sudo journalctl -u $SERVICE_NAME -f"
echo "------------------------------------------------"
