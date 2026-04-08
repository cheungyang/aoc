# Installation and Run Guide

This guide explains how to set up and run the LangGraph system in a Docker container on a machine that has Docker installed.

## Prerequisites

- Docker installed on the target machine.
- Git installed on the host machine.

## Files Created

- `Dockerfile`: Defines the container environment.
- `entrypoint.sh`: Handles copying SSH keys and setting permissions inside the container.
- `install.sh`: A helper script to run the container with correct volume mounts.

## Steps

### 1. Build the Docker Image

Navigate to the root directory of the project (where the `Dockerfile` is located) and run:

```bash
docker build -t langgraph-app .
```

### 2. Prepare Configuration Files

Ensure you have the following files/directories on your host machine if you want to use them:
- `.env` file in the project root.
- SSH keys in `~/.ssh` (to allow git operations inside the container).
- `gogcli` config files in `~/.config/gogcli`.

### 3. Run the Container

You can use the provided `install.sh` script to run the container. Make it executable first if it isn't:

```bash
chmod +x install.sh
./install.sh
```

The script will automatically:
- Mount the current project directory to `/app` in the container.
- Pass the `.env` file if it exists.
- Mount your host's `~/.ssh` to `/mnt/.ssh` in the container, where `entrypoint.sh` will copy them to the container user's home and fix permissions.
- Mount `~/.config/gogcli` to `/home/appuser/.config/gogcli`.

### 4. Editing Files

Since the project directory is mounted as a volume (`-v "$(pwd)":/app`), any edits you make to the files on your host machine will be immediately reflected inside the Docker instance. You do not need to enter the Docker instance to make code changes.

## Customization

If your SSH keys or `gogcli` config are in non-standard locations, you can edit the `install.sh` script to point to the correct paths before running it.
