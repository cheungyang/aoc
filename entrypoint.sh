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

# Ensure gogcli home, PKM, and workspaces exist and have proper ownership
GOG_DIR="${GOG_HOME:-/app/.gogcli}"
mkdir -p "$GOG_DIR/config" "$GOG_DIR/data" "$GOG_DIR/state" "$GOG_DIR/cache" /home/appuser/pkm /home/appuser/workspaces /home/appuser/.config
chown -R appuser:appuser "$GOG_DIR" /home/appuser/pkm /home/appuser/workspaces /home/appuser/.config 2>/dev/null || true

# Symlink legacy ~/.config/gogcli to project gogcli config directory
ln -sfn "$GOG_DIR/config" /home/appuser/.config/gogcli 2>/dev/null || true

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
