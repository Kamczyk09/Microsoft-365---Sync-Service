#!/bin/bash

# Define the user ID (email) of the authenticated user. 
# *** YOU MUST CHANGE THIS TO YOUR ACTUAL AUTHENTICATED EMAIL ***
USER_ID="stanislaw@thalamind.onmicrosoft.com"

echo "--- 1. Reloading Systemd Daemon ---"
sudo systemctl daemon-reload

echo "--- 2. Enabling and Starting Timer for user: $USER_ID ---"
# Enable the timer to start automatically on boot
sudo systemctl enable "onedrive-sync@$USER_ID.timer"

# Start the timer immediately
sudo systemctl start "onedrive-sync@$USER_ID.timer"

echo "--- 3. Setup Complete ---"
echo "Timer status:"
sudo systemctl status "onedrive-sync@$USER_ID.timer" | grep -E "Active:|next"
echo ""
echo "Check the logs using: sudo journalctl -u onedrive-sync@$USER_ID.service -f"