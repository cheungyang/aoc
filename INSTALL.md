# Installation and Run Guide

This guide explains how to set up and run the LangGraph system in a Docker container on a machine that has Docker installed.

## Prerequisites

- Docker installed on the target machine.
- Git installed on the host machine.

## Files Created

- `docker-compose.yml`: Docker Compose configuration for launching the system as a managed service.
- `Dockerfile`: Defines the container environment (Python 3.11, Chromium, Playwright, gogcli, gh CLI, nlm).
- `.dockerignore`: Excludes local caches, `.venv`, and artifacts from the build context.
- `entrypoint.sh`: Handles copying SSH keys and setting permissions inside the container.
- `install.sh`: A helper script to run the container directly with `docker run`.

---

## Running with Docker Compose (Recommended)

### 1. Build and Start the Service

Make sure your `.env` file is configured in the root directory, then run:

```bash
docker compose up --build -d
```

### 2. View Live Logs

```bash
docker compose logs -f app
```

### 3. Stop the Service

```bash
docker compose down
```

### 4. Run Interactive Shell or One-Off Commands

```bash
# Open an interactive bash shell inside the container
docker compose exec app bash

# Run debug mode or tests inside the container
docker compose exec app python main.py --debug
```

---

## Alternative: Running with Helper Script (`install.sh`)

### 1. Build the Docker Image

Navigate to the root directory of the project (where the `Dockerfile` is located) and run:

```bash
docker build -t aoc .
```

### 2. Prepare Configuration Files

Ensure you have the following files/directories on your host machine if you want to use them:
- `.env` file in the project root.
- SSH keys in `~/.ssh` (to allow git operations inside the container).
- `gogcli` config files in `~/.config/gogcli`.
- PKM directory in `~/pkm` (if using PKM tasks/vaults).

### 3. Run the Container

You can use the provided `install.sh` script:

```bash
chmod +x install.sh
./install.sh
```

---

## Live Code Editing

Since the project directory is mounted as a volume (`.:/app` in `docker-compose.yml` or `-v "$(pwd)":/app`), any edits you make to the files on your host machine are immediately reflected inside the running Docker container without needing a rebuild.

## LangSmith Observability & Tracing

This LangGraph system is integrated with [LangSmith](https://smith.langchain.com) for real-time observability, tracing, debugging, and latency/cost monitoring.

### 1. Set up LangSmith in `.env`

Add your LangSmith configuration to your `.env` file (see `.env.example`):

```bash
# Enable tracing
LANGSMITH_TRACING=true

# Your LangSmith API Key (create one at https://smith.langchain.com/settings)
LANGSMITH_API_KEY=lsv2_pt_...

# Optional: Set the project name (defaults to 'default')
LANGSMITH_PROJECT=langgraph-agents

# Optional: Set regional endpoint (defaults to GCP US)
# - EU: https://eu.api.smith.langchain.com
# - APAC: https://apac.api.smith.langchain.com
# - AWS US: https://aws.api.smith.langchain.com
LANGSMITH_ENDPOINT=https://api.smith.langchain.com
```

### 2. Verify Tracing

You can run the verification script to test connectivity with LangSmith:

```bash
python scripts/verify_langsmith.py
```

### 3. Viewing Traces

Once enabled, all agent executions, subgraph runs, LLM calls, and tool invocations will be automatically tracked in your LangSmith project dashboard at [https://smith.langchain.com](https://smith.langchain.com):
- **Traces**: Full execution tree showing graph nodes, tool calls, and LLM prompts/outputs with execution duration.
- **Threads / Sessions**: Multi-turn conversation history grouped by Discord session/thread ID.
- **Metadata & Tags**: Filter traces by agent ID (e.g. `main`, `agent-designer`), source (`discord`, `tool`, `scheduled`), or role.

## Customization

If your SSH keys or `gogcli` config are in non-standard locations, you can edit the `install.sh` script to point to the correct paths before running it.
