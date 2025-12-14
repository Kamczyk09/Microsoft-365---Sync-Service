
- using delegated permissions (why?)

Running continous sync:
- use user: thala_user (because 'sudo chown -R stanley_sync:stanley_sync /opt/thalamind/')

- Service Unit File Path: /etc/systemd/system/onedrive-sync@.service
[Unit]
Description=OneDrive Sync Service for %i
After=network.target

[Service]
Type=oneshot
# IMPORTANT: Change User to the actual Linux user who owns the files
User=thala_user
# Set the working directory to the project root
WorkingDirectory=/opt/thalamind/
# Point ExecStart to the Python interpreter inside the virtual environment
ExecStart=/opt/thalamind/.venv/bin/python /opt/thalamind/sync_service.py --sync %i

StandardOutput=journal
StandardError=journal


- Timer Unit File Path: /etc/systemd/system/onedrive-sync@.timer
[Unit]
Description=Run OneDrive Sync for %i every minute

[Timer]
# Run the service one minute after the previous run finished.
OnUnitActiveSec=1min
# Run once 15 seconds after system boot.
OnBootSec=15s
# Ensure scheduled run is triggered after a power-off period.
Persistent=true

[Install]
WantedBy=timers.target

