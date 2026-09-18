#!/bin/bash

# Script to run the LangGraph container with required volume mounts and environment files.
# This script assumes you are running it from the root of the project directory.

# Configuration
IMAGE_NAME="aoc"
CONTAINER_WORKDIR="/app"

# Check if .env file exists
ENV_FILE=".env"
if [ ! -f "$ENV_FILE" ]; then
    echo "Warning: $ENV_FILE file not found in current directory. Proceeding without --env-file."
    ENV_OPT=""
else
    ENV_OPT="--env-file $ENV_FILE"
fi

# Check for SSH keys directory
SSH_DIR="$HOME/.ssh"
if [ ! -d "$SSH_DIR" ]; then
    echo "Warning: $SSH_DIR not found. SSH keys will not be mounted."
    SSH_OPT=""
else
    SSH_OPT="-v $SSH_DIR:/mnt/.ssh"
fi

# Gogcli config directory lives in project root (./.gogcli)
mkdir -p "$(pwd)/.gogcli"
GOG_KEYRING_BACKEND_VAL="${GOG_KEYRING_BACKEND:-file}"
GOG_KEYRING_PROVIDER_VAL="${GOG_KEYRING_PROVIDER:-file}"
GOG_OPT="-e GOG_HOME=${GOG_HOME:-/app/.gogcli} -e GOG_KEYRING_BACKEND=${GOG_KEYRING_BACKEND_VAL} -e GOG_KEYRING_PROVIDER=${GOG_KEYRING_PROVIDER_VAL}"
if [ -n "$GOG_KEYRING_PASSWORD" ]; then
    GOG_OPT="$GOG_OPT -e GOG_KEYRING_PASSWORD=${GOG_KEYRING_PASSWORD}"
fi

# Check for PKM directory (defaults to ../pkm on host machine)
PKM_HOST_DIR="${PKM_HOST_DIR:-$(cd "$(pwd)/../pkm" 2>/dev/null && pwd || echo "$(pwd)/../pkm")}"
if [ -d "$PKM_HOST_DIR" ]; then
    PKM_OPT="-v $PKM_HOST_DIR:/home/appuser/pkm"
else
    echo "Warning: PKM directory not found at $PKM_HOST_DIR."
    PKM_OPT=""
fi

# Check for workspaces directory (defaults to ../workspaces on host machine)
WORKSPACES_HOST_DIR="${WORKSPACES_HOST_DIR:-$(cd "$(pwd)/../workspaces" 2>/dev/null && pwd || echo "$(pwd)/../workspaces")}"
if [ -d "$WORKSPACES_HOST_DIR" ]; then
    WORKSPACES_OPT="-v $WORKSPACES_HOST_DIR:/home/appuser/workspaces"
else
    echo "Warning: Workspaces directory not found at $WORKSPACES_HOST_DIR."
    WORKSPACES_OPT=""
fi

# Git environment options for automated non-interactive sync
GIT_OPT="-e GIT_TERMINAL_PROMPT=0 -e GIT_ASKPASS= -e GIT_SSH_COMMAND=ssh\ -o\ BatchMode=yes\ -o\ StrictHostKeyChecking=accept-new\ -o\ ConnectTimeout=15 -e CODEBASE_DIR=/app"

# Check for host .gitconfig
if [ -f "$HOME/.gitconfig" ]; then
    GITCONFIG_OPT="-v $HOME/.gitconfig:/mnt/.gitconfig:ro"
else
    GITCONFIG_OPT=""
fi

# Run the container
echo "Starting Docker container $IMAGE_NAME..."
docker run -it \
  -v "$(pwd)":$CONTAINER_WORKDIR \
  $ENV_OPT \
  $SSH_OPT \
  $GITCONFIG_OPT \
  $GIT_OPT \
  $GOG_OPT \
  $PKM_OPT \
  $WORKSPACES_OPT \
  $IMAGE_NAME "$@"
