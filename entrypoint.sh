#!/bin/bash

# Create .ssh directory if it doesn't exist
mkdir -p /home/appuser/.ssh

# Copy keys from mounted directory if present
if [ -d "/mnt/.ssh" ] && [ "$(ls -A /mnt/.ssh)" ]; then
    echo "Copying SSH keys from /mnt/.ssh..."
    cp -R /mnt/.ssh/* /home/appuser/.ssh/
    
    # Fix permissions
    chown -R appuser:appuser /home/appuser/.ssh
    chmod 700 /home/appuser/.ssh
    find /home/appuser/.ssh -type f -exec chmod 600 {} \;
    
    echo "SSH keys copied and permissions set."
else
    echo "No SSH keys found at /mnt/.ssh or directory is empty."
fi

# Ensure directories exist and have proper ownership
mkdir -p /home/appuser/.config/gogcli /home/appuser/pkm /home/appuser/workspaces
chown -R appuser:appuser /home/appuser/.config /home/appuser/pkm /home/appuser/workspaces 2>/dev/null || true

# Symlink repo-relative paths to /home/appuser directories
if [ -d "/app" ]; then
    if [ ! -e "/app/pkm" ] || [ -L "/app/pkm" ]; then
        ln -sfn /home/appuser/pkm /app/pkm 2>/dev/null || true
    fi
    if [ ! -e "/app/workspaces" ] || [ -L "/app/workspaces" ]; then
        ln -sfn /home/appuser/workspaces /app/workspaces 2>/dev/null || true
    fi
fi

# Start Chromium in the background as appuser for the browser tool
runuser -u appuser -- chromium --headless --no-sandbox --disable-gpu --remote-debugging-port=9222 &

# Execute the command passed to docker run as appuser
exec runuser -u appuser -- "$@"
