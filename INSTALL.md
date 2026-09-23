# Installation and Run Guide

This guide explains how to set up and run the LangGraph system in a Docker container on a machine that has Docker installed.

## Prerequisites

- Docker installed on the target machine.
- Git installed on the host machine.

## Files Created

- `docker-compose.yml`: Docker Compose configuration for launching the system as a managed service.
- `Dockerfile`: Defines the container environment (Python 3.11, Node.js 22 + npm/corepack, Chromium, Playwright, gogcli, gh CLI, nlm).
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
See sections 9 and 10 of `.env.example` for the available knobs.

---

## Knowledge Backend (CPU Compatibility)

The vault index runs on one of two interchangeable backends, chosen by
`KNOWLEDGE_BACKEND`:

| Value | Storage | Requires |
|---|---|---|
| `auto` (default) | whichever of the two is usable | — |
| `lancedb` | Lance columnar store with an ANN index | a CPU with AVX2 |
| `numpy` | `chunks.parquet` + `vectors.npy` + a tantivy BM25 index | any CPU |

Both expose the same search modes — hybrid, semantic, and keyword — and return
identical result shapes, so `vault_search` and every script behave the same
either way. The numpy backend scans exhaustively instead of using an ANN index,
which is a non-issue at vault scale (a few tens of thousands of chunks is one
matrix multiply per query).

> [!IMPORTANT]
> LanceDB's published wheels are compiled for AVX2. On an older CPU — the Intel
> Celeron J4025 in a Synology DS220+, for instance — `import lancedb` does not
> raise `ImportError`. It executes an illegal instruction and the kernel kills
> the process with SIGILL, which no `try`/`except` can catch. That is why the
> default is `auto`: it attempts the import in a **child** process and reads the
> exit status, so a crash costs a subprocess rather than the bot.

Check which one is active:

```bash
docker compose exec app python3 -c \
    "from core.knowledge.vector.db import get_active_backend_name; print(get_active_backend_name())"
```

The two use different on-disk layouts, so switching requires a rebuild. There is
no migration — re-indexing from the vault is both simpler and faster than
converting:

```bash
docker compose exec app python3 scripts/regenerate_lancedb.py --clean
```

First boot handles this automatically: the provisioner looks for the *active*
backend's own files, so a host still carrying an index from the other backend is
treated as un-indexed and rebuilt rather than silently starting up empty.

---

## Using an On-Device Model

Individual agents can run against a local OpenAI-compatible server (LiteRT-LM,
llama.cpp, LM Studio, or similar) instead of Gemini. This is opt-in per agent —
everything else keeps using Gemini.

### 1. Start the server and point AOC at it

```bash
# .env -- no API key: the server does not authenticate.
LOCAL_LLM_BASE_URL=http://localhost:9379/v1
LOCAL_LLM_TIMEOUT=300
```

Verify it before wiring an agent to it:

```bash
curl http://localhost:9379/v1/models
```

The server must support **tool calling**. Agents in this system are ReAct agents
and bind their tools on every turn; a server that accepts the `tools` parameter
and ignores it produces an agent that never calls a tool and merely looks
unhelpful. Check for a `tool_calls` array in the response to:

```bash
curl http://localhost:9379/v1/chat/completions -H "Content-Type: application/json" -d '{
  "model": "<your-model-id>",
  "messages": [{"role": "user", "content": "What is the weather in Paris? Use the tool."}],
  "tools": [{"type": "function", "function": {
    "name": "get_weather",
    "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}
  }}]
}'
```

### 2. Measure the context window, and size the history budget to it

> [!IMPORTANT]
> Do this before anything else. It is the single most common reason a local
> agent fails, and the failure is a hard rejection rather than a degradation.

The window is set **when the server starts**, not by the model file. Loading a
model that advertises a larger window does not raise it. Measure it rather than
trusting the model card — overflow the endpoint and the server states its own
limit. Use `"stream": true`: the non-streaming path may return an HTML 500
instead of a diagnostic.

```bash
curl -sN http://localhost:9379/v1/chat/completions -H "Content-Type: application/json" \
  -d "{\"model\":\"<your-model-id>\",\"stream\":true,\"messages\":[{\"role\":\"user\",
       \"content\":\"$(python3 -c 'print("word "*20000)')\"}]}" | grep -o 'Exceeding[^"]*'
# Exceeding the maximum number of tokens allowed: 20009 >= 4096
#                                                          ^^^^ the real window
```

Then record it, so the context pruner can size history against it:

```bash
# .env
LOCAL_LLM_CONTEXT_TOKENS=4096      # match the number above
# LOCAL_LLM_HISTORY_TOKENS=2048    # optional; defaults to half the window
```

`CONTEXT_MAX_TOKENS` (30000) is sized for Gemini's window and is over seven
times a 4096-token server's entire capacity, so without a separate budget the
pruner never fires before the server rejects the request. Local agents use
`LOCAL_LLM_HISTORY_TOKENS`; remote agents are unaffected.

Half the window is reserved by default because the pruner measures
**conversation history only**. The system prompt and tool schemas are charged to
the same window but counted nowhere, and for these agents they are the larger
share:

| Agent | System prompt | Tool schemas | Total before any conversation |
|---|---:|---:|---:|
| topic-researcher | 4,455 | 2,721 | 7,176 |
| day-planner | 4,466 | 2,285 | 6,751 |
| main | 3,930 | 1,703 | 5,633 |
| meal-planner | 1,950 | 636 | 2,586 |
| graph-worker | 1,429 | 636 | 2,065 |
| script-executor | 798 | 636 | 1,434 |

An agent whose static cost already exceeds the window cannot run locally at any
history budget. Note also that the three PKM vault files in an agent's persona
grow by roughly 300 tokens per month, so leave headroom rather than fitting
exactly.

### 3. Register the model as a tier

Add the server's model id to `LOCAL_TIERS` in
[`core/util/models.py`](core/util/models.py), mapping each tier to whichever
build serves that intent. With a single on-device model, all three point at it.
The id stays here rather than in agent configs because it is a date-stamped
build artifact specific to one machine's weights.

### 4. Move an agent on-device

Add one key to its `agent.json`:

```json
{
  "model": "FLASH_LITE",
  "provider": "local"
}
```

Both providers answer the same tier names, so the `model` line is untouched.
Deleting the `provider` key moves the agent back to Gemini.

### Constraints worth knowing first

- **The server handles one request at a time.** Two local agents queue behind
  each other, and the effect compounds once a queue forms. Keep the local roster
  small and prefer low-volume, single-purpose agents. This is also why
  `LOCAL_LLM_TIMEOUT` defaults to 300s: a turn's latency includes everything
  queued ahead of it, and the OpenAI client's 60s default would abort turns that
  were merely waiting.
- **Do not move `graph-worker-low` or the voice verbalizer.** Both call a model
  *inside* another turn — `graph-worker-low` summarizes context before the user's
  request is processed, and the verbalizer runs mid-speech. On a serializing
  server they queue behind the very turn they are serving. `graph-worker-low`
  looks like an ideal candidate (toolless, stateless, cheapest tier) and is
  precisely the wrong one.
- **`IMAGE` is not available locally.** Configuring it raises at graph-build
  time rather than failing later at an image call site.
- **In Docker, `localhost` is the container.** `docker-compose.yml` overrides
  `LOCAL_LLM_BASE_URL` to `host.docker.internal` and declares the
  `host-gateway` alias. The server must then bind `0.0.0.0` rather than
  `127.0.0.1` — which also exposes it to the LAN, so add a firewall rule.
  Note the deployment NAS (Synology DS220+) cannot host a model of this size;
  that deployment stays on Gemini.

---

## Home Assistant Integration

AOC integrates directly with Home Assistant to provide safe, agentic smart home observation, device control, automation authoring, and health auditing.

### 1. Architecture & Security Rails

- **Single-Tool Facade (`home_assistant`)**: All agents interact via a single structured tool that routes internally between REST (states, configs, services), WebSocket (entity/device/area registries), and internal MCP (live Assist context snapshot).
- **Physical Safety Deny-List**: Hardcoded in `guards.py`. Critical security domains (`lock`, `alarm_control_panel`, `cover`, `garage_door`, `valve`, `water_heater`) are blocked outright in code and cannot be controlled or embedded in automations.
- **Climate Safety Band**: Target temperatures outside 12.0°C – 28.0°C are rejected.
- **Propose → Confirm → Apply Protocol**: Any configuration modification (`upsert_automation`, `upsert_script`, `upsert_scene`, `reload`) or opaque trigger (`script.turn_on`, `automation.trigger`) generates a rendered diff and a single-use SHA-256 bound confirmation token. The agent must obtain explicit user approval before applying.
- **Automatic Before-Image Rollbacks**: Before applying any config write, an API before-image snapshot is persisted to `pkm/agents/home-steward/ha_snapshots/`. If post-write verification (`check_config`, error log inspection, entity read-back) fails, the prior state is rolled back automatically.

### 2. Setup & Credentials

1. **Long-Lived Access Token**:
   - Create a long-lived access token in Home Assistant (**Profile > Security > Long-Lived Access Tokens**).
   - Write the token to `./home_assistant_token` at the project root with mode `600`:
     ```bash
     install -m 600 /dev/null ./home_assistant_token
     pbpaste > ./home_assistant_token
     ```
   - *Note*: Both `.gitignore` and `.dockerignore` exclude `home_assistant_token`. The file is passed into the container via the `.:/app` volume mount.

2. **Configure Environment in `.env`**:
   ```bash
   # Base URL without trailing slash
   HA_BASE_URL=https://ha.yngnas220.synology.me

   # Path to token inside container (default: /app/home_assistant_token)
   # HA_TOKEN_FILE=/app/home_assistant_token

   # Master kill switch: set to true to enable mutating actions (service calls, automations)
   HA_WRITE_ENABLED=false

   # Request timeout in seconds
   HA_REQUEST_TIMEOUT=20
   ```

3. **Butler Agent (`home-steward`)**:
   - Butler is the dedicated smart home steward agent hosted on the `#home-automation` Discord channel.
   - Equipped with 3 specialized skills:
     - `ha_inventory`: Area × domain matrix and filtered device/entity searches.
     - `ha_automation_authoring`: Rigorous design, validation, and propose-confirm-verify authoring.
     - `ha_config_audit`: Daily health and configuration integrity checks.
   - Schedules:
     - Daily 8:00 AM: Configuration audit & unavailable device scan.
     - Sunday 8:00 PM: Weekly inventory summary.

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

