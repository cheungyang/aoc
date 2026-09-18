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
if [ -d "/mnt/.ssh" ] && [ "$(ls -A /mnt/.ssh 2>/dev/null)" ]; then
    echo "Copying SSH keys from /mnt/.ssh..."
    cp -R /mnt/.ssh/* /home/appuser/.ssh/
    
    # Fix permissions
    chown -R appuser:appuser /home/appuser/.ssh
    chmod 700 /home/appuser/.ssh
    find /home/appuser/.ssh -type f -exec chmod 600 {} \;
    chmod 644 /home/appuser/.ssh/*.pub /home/appuser/.ssh/known_hosts 2>/dev/null || true
    chmod 600 /home/appuser/.ssh/config 2>/dev/null || true
    
    echo "SSH keys copied and permissions set."
else
    echo "No SSH keys found at /mnt/.ssh or directory is empty."
fi

# Ensure SSH client config allows non-interactive host key acceptance for automated git sync
if [ ! -f /home/appuser/.ssh/config ] || ! grep -qi "StrictHostKeyChecking" /home/appuser/.ssh/config 2>/dev/null; then
    cat >> /home/appuser/.ssh/config << 'EOF'

Host *
    StrictHostKeyChecking accept-new
    BatchMode yes
    ConnectTimeout 15
EOF
    chown appuser:appuser /home/appuser/.ssh/config
    chmod 600 /home/appuser/.ssh/config
fi

# Configure Git safe.directory for both root and appuser to support mounted volumes
git config --system --add safe.directory '*'
runuser -u appuser -- git config --global --add safe.directory '*' 2>/dev/null || true

# Copy host .gitconfig if mounted, or ensure default user identity for commits
if [ -f "/mnt/.gitconfig" ]; then
    cp /mnt/.gitconfig /home/appuser/.gitconfig
    chown appuser:appuser /home/appuser/.gitconfig
fi

GIT_USER_NAME="${GIT_USER_NAME:-${GIT_AUTHOR_NAME:-AOC Bot}}"
GIT_USER_EMAIL="${GIT_USER_EMAIL:-${GIT_AUTHOR_EMAIL:-aoc@localhost}}"
git config --system user.name "$GIT_USER_NAME" 2>/dev/null || true
git config --system user.email "$GIT_USER_EMAIL" 2>/dev/null || true
if ! runuser -u appuser -- git config --global user.name >/dev/null 2>&1; then
    runuser -u appuser -- git config --global user.name "$GIT_USER_NAME" 2>/dev/null || true
fi
if ! runuser -u appuser -- git config --global user.email >/dev/null 2>&1; then
    runuser -u appuser -- git config --global user.email "$GIT_USER_EMAIL" 2>/dev/null || true
fi

# Configure GitHub CLI git credential helper if token is present
if [ -n "$GITHUB_TOKEN" ] || [ -n "$GH_TOKEN" ]; then
    runuser -u appuser -- gh auth setup-git 2>/dev/null || true
fi

# Ensure gogcli home, PKM, and workspaces exist and have proper ownership
export GOG_HOME="${GOG_HOME:-/app/.gogcli}"
export GOG_KEYRING_BACKEND="${GOG_KEYRING_BACKEND:-file}"
export GOG_KEYRING_PROVIDER="${GOG_KEYRING_PROVIDER:-file}"
if [ -n "$GOG_KEYRING_PASSWORD" ]; then
    export GOG_KEYRING_PASSWORD
fi
GOG_DIR="$GOG_HOME"
mkdir -p "$GOG_DIR/config" "$GOG_DIR/data" "$GOG_DIR/state" "$GOG_DIR/cache" /home/appuser/pkm /home/appuser/workspaces /home/appuser/.config
chown -R appuser:appuser "$GOG_DIR" /home/appuser/pkm /home/appuser/workspaces /home/appuser/.config 2>/dev/null || true

# Symlink legacy ~/.config/gogcli to project gogcli config directory
ln -sfn "$GOG_DIR/config" /home/appuser/.config/gogcli 2>/dev/null || true

# Symlink and resolve repository paths between /app/pkm and /home/appuser/pkm
if [ -d "/app" ]; then
    # If /app/pkm is an existing git repo or real directory and /home/appuser/pkm has no .git, link /home/appuser/pkm -> /app/pkm
    if [ -d "/app/pkm/.git" ] && [ ! -d "/home/appuser/pkm/.git" ]; then
        rm -rf /home/appuser/pkm 2>/dev/null || true
        ln -sfn /app/pkm /home/appuser/pkm 2>/dev/null || true
    elif [ ! -e "/app/pkm" ] || [ -L "/app/pkm" ]; then
        ln -sfn /home/appuser/pkm /app/pkm 2>/dev/null || true
    fi

    if [ ! -e "/app/workspaces" ] || [ -L "/app/workspaces" ]; then
        ln -sfn /home/appuser/workspaces /app/workspaces 2>/dev/null || true
    fi

    # Ensure scripts in /app/scripts are executable
    chmod +x /app/scripts/*.py 2>/dev/null || true
fi

# Start Chromium in the background as appuser for the browser tool
runuser -u appuser -- chromium --headless --no-sandbox --disable-gpu --remote-debugging-port=9222 &

# Execute the command passed to docker run as appuser
exec runuser -u appuser -- "$@"
