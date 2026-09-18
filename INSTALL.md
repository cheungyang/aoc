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
- `scripts/provision_assets.py`: Downloads or generates the assets that are not checked into git.

---

## Assets Not Checked Into Git

Several things the app needs at run time are excluded by `.gitignore` and therefore
absent from a fresh clone or a fresh image: the faster-whisper STT weights, the
generated audio cues, the LanceDB vector index, and the ignored working
directories (`sessions/`, `workspaces/`, `.gogcli/`). `scripts/provision_assets.py`
provisions all of them in two stages.

| Stage | Runs from | Does |
| --- | --- | --- |
| `build` | `Dockerfile` | Downloads the faster-whisper model, verifies the Silero VAD graph, stages audio cues (generating any that are missing via edge-tts) |
| `runtime` | `entrypoint.sh` | Re-fetches the model if the volume lacks it, restores staged files the bind mount hides, creates ignored directories, warns on a missing `.env`, bootstraps the vector index |

### Where assets live, and why

Nothing is provisioned into `/app`: `docker-compose.yml` mounts the host checkout
there, which hides anything the build wrote. Instead there are two directories,
and the distinction between them matters.

| Path | Mapped to | Holds | Why there |
| --- | --- | --- | --- |
| `/opt/aoc` | named volume `aoc-assets` | faster-whisper weights (~150MB) under `hf/` | Persists across image rebuilds and `docker compose down`, so the download happens once |
| `/opt/aoc-seed` | image layer only | audio cues destined for `/app` | Must track the image — see below |

> [!WARNING]
> Do not move the seed directory inside `/opt/aoc`. Docker copies image content
> into a named volume **only while that volume is empty**. A seed stored inside
> the volume would be frozen at the first image that ever populated it, and no
> later build could correct it.

The same rule cuts the other way for the model cache: a volume created by an
older image will not pick up a newer one's weights. The runtime stage therefore
treats the cache as possibly-empty and re-fetches when needed, so a stale volume
costs a slow first start rather than a failed transcription.

Two things keep rebuilds cheap:

- Provisioning runs **before** `COPY . .` in the Dockerfile. Editing application
  code no longer invalidates the download layer; only `provision_assets.py` or
  `assets/` can.
- The `aoc-assets` volume outlives the image, so even a fully rebuilt image
  reuses the weights already on the host.

```bash
# Inspect the persisted cache
docker volume inspect aoc_aoc-assets

# Force a clean re-download (e.g. after changing AOC_STT_MODEL)
docker compose down && docker volume rm aoc_aoc-assets
```

`HF_HOME` is pinned to `/opt/aoc/hf` for build and run alike, so `STTEngine`
reads the cache the build populated rather than pulling it again on the first
transcription.

> [!IMPORTANT]
> The first start against a PKM that has no `.lancedb` blocks while the index is
> built, which can take several minutes on a large vault. This happens once.
> Set `AOC_BOOTSTRAP_INDEX=never` to start immediately and index separately with
> `scripts/regenerate_lancedb.py`.

Run it manually to inspect or re-run a stage:

```bash
# See what a stage would do without writing anything
docker compose exec app python3 scripts/provision_assets.py --stage runtime --dry-run

# Force a vector index rebuild
docker compose exec app env AOC_BOOTSTRAP_INDEX=always \
    python3 scripts/provision_assets.py --stage runtime --only index
```

Provisioning failures are logged but non-fatal by default, so a flaky mirror
cannot break an otherwise good image. Build with
`--build-arg AOC_PROVISION_STRICT=1` to make them fail the build instead.
See sections 8 and 9 of `.env.example` for the available knobs.

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
- **Choosing the key source (`SSH_HOST_DIR`)**: defaults to `$HOME/.ssh`. When launching with `sudo docker compose`, `$HOME` is `/root`, so the default stages the **root account's** keys rather than yours. Set `SSH_HOST_DIR` in `.env` to the absolute path of the intended directory — on Synology DSM that is `/var/services/homes/<user>/.ssh`. The entrypoint logs the fingerprint of every staged key at startup (`docker compose logs app | grep "SSH key"`) so a wrong source is visible immediately instead of surfacing later as `Permission denied (publickey)`.
- **Non-standard key filenames**: keys not named `id_rsa`/`id_ecdsa`/`id_ed25519` are never offered by SSH automatically. The entrypoint appends an `aoc-managed defaults` block to each `~/.ssh/config` listing an `IdentityFile` for every private key it finds, and rewrites `IdentityFile` paths that point at host-side home directories.
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

