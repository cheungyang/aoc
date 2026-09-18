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
- `gogcli` auth & configuration: stored automatically in `./.gogcli` inside the project root (no `~/.config/gogcli` required). Configure `GOG_KEYRING_BACKEND=file` and `GOG_KEYRING_PASSWORD` in `.env` for headless automated authentication.
- PKM directory in `../pkm` (external directory mapped into container at `/home/appuser/pkm`).
- Workspaces directory in `../workspaces` (external directory mapped into container at `/home/appuser/workspaces`).

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

## Periodic Git Resolution (Dual Repositories)

The system automatically schedules periodic Git resolution for two separate Git repositories:
1. **PKM Obsidian Vault** (`PKM_DIR`, default `/home/appuser/pkm` or `pkm/`): Automatically stages new files, commits local updates, pulls remote changes (resolving conflicts with `-X theirs`), and pushes back to remote.
2. **Main Codebase** (`CODEBASE_DIR`, default `/app`): Pulls latest upstream updates and resolves conflicts with remote content without pushing local uncommitted development changes.

### Configuration & Credentials

- **SSH Authentication**: Host keys are mounted **read-only** at `/mnt/.ssh`, then copied by the entrypoint into `/home/appuser/.ssh` (owned by `appuser`) and `/root/.ssh` (owned by `root`), with permissions and non-interactive settings applied to each copy. They are not bind-mounted onto `~/.ssh` directly: a bind mount preserves the host UID, so `/root/.ssh/config` would appear owned by `appuser` and OpenSSH would fail with `Bad owner or permissions`. Because the copies are made at container start, re-run `docker compose up -d --force-recreate` (or restart the container) after changing keys on the host.
- **Non-Interactive SSH**: Non-interactive `BatchMode` and `StrictHostKeyChecking=accept-new` are enabled via `GIT_SSH_COMMAND` to prevent hanging during automated scheduled jobs.
- **Git Safe Directories**: `safe.directory '*'` is configured system-wide to permit Git operations across mounted Docker volumes.
- **Git Identity**: `GIT_USER_NAME` and `GIT_USER_EMAIL` are configured in `docker-compose.yml` (and copied from host `~/.gitconfig` if mounted).
- **One-off Git Sync Run**:
  ```bash
  docker compose exec app python scripts/sync_git.py
  # Or dry-run
  docker compose exec app python scripts/sync_git.py --dry-run
  ```

## Customization

If your SSH keys or PKM directory are in non-standard locations, or you want to configure gogcli keyring settings, you can set `PKM_HOST_DIR`, `WORKSPACES_HOST_DIR`, `CODEBASE_DIR`, `GOG_HOME`, `GOG_KEYRING_BACKEND`, or `GOG_KEYRING_PASSWORD` in your `.env` file before running.

