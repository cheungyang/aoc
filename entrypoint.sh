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

# Ensure appuser owns the app directory (if needed, but usually handled by mount permissions or build)
# chown -R appuser:appuser /app

# Execute the command passed to docker run as appuser
exec runuser -u appuser -- "$@"
