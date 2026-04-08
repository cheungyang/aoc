#!/bin/bash

# Script to run the LangGraph container with required volume mounts and environment files.
# This script assumes you are running it from the root of the project directory.

# Configuration
IMAGE_NAME="langgraph-app"
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

# Check for gogcli config directory
GOG_CONFIG_DIR="$HOME/.config/gogcli"
if [ ! -d "$GOG_CONFIG_DIR" ]; then
    echo "Warning: $GOG_CONFIG_DIR not found. Creating a placeholder directory to mount."
    mkdir -p "$GOG_CONFIG_DIR"
fi
GOG_OPT="-v $GOG_CONFIG_DIR:/home/appuser/.config/gogcli"

# Run the container
echo "Starting Docker container $IMAGE_NAME..."
docker run -it \
  -v "$(pwd)":$CONTAINER_WORKDIR \
  $ENV_OPT \
  $SSH_OPT \
  $GOG_OPT \
  $IMAGE_NAME "$@"
