#!/bin/bash

# Align appuser UID and GID with the owner of the mounted /app volume
TARGET_UID=$(stat -c '%u' /app 2>/dev/null || stat -f '%u' /app 2>/dev/null || echo "")
TARGET_GID=$(stat -c '%g' /app 2>/dev/null || stat -f '%g' /app 2>/dev/null || echo "")

if [ -n "$TARGET_UID" ] && [ "$TARGET_UID" -ne 0 ] && [ "$TARGET_UID" != "$(id -u appuser 2>/dev/null)" ]; then
    groupmod -g "$TARGET_GID" appuser 2>/dev/null || true
    usermod -u "$TARGET_UID" -g "$TARGET_GID" appuser 2>/dev/null || usermod -u "$TARGET_UID" appuser 2>/dev/null || true
    chown -R appuser:appuser /home/appuser 2>/dev/null || true
fi

# Ensure /app and its files are readable and directories are executable
chmod -R a+rX /app 2>/dev/null || true

# If appuser still cannot read /app/main.py, ensure ownership
if ! runuser -u appuser -- test -r /app/main.py 2>/dev/null; then
    chown -R appuser:appuser /app 2>/dev/null || true
fi

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
