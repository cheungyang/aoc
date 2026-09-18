FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    openssh-client \
    curl \
    wget \
    vim \
    tar \
    unzip \
    procps \
    chromium \
    tzdata \
    && rm -rf /var/lib/apt/lists/*

# Configure Git safe.directory and system defaults for mounted repositories
RUN git config --system --add safe.directory '*' \
    && git config --system user.name "AOC Bot" \
    && git config --system user.email "aoc@localhost" \
    && mkdir -p /etc/ssh \
    && ssh-keyscan -t rsa,ecdsa,ed25519 github.com gitlab.com >> /etc/ssh/ssh_known_hosts 2>/dev/null || true

# Set working directory
WORKDIR /app

# Copy dependency requirements first for better Docker layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip3 install --no-cache-dir -r requirements.txt

# Install visual browser bindings for visual AI automation
RUN playwright install chromium
RUN playwright install-deps

# Install GitHub CLI (gh)
RUN mkdir -p -m 755 /etc/apt/keyrings \
    && wget -qO- https://cli.github.com/packages/githubcli-archive-keyring.gpg | tee /etc/apt/keyrings/githubcli-archive-keyring.gpg > /dev/null \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null \
    && apt-get update && apt-get install -y gh \
    && rm -rf /var/lib/apt/lists/*

# Install notebooklm-mcp-cli (nlm command) ensuring mcp<2 compatibility
RUN pip install --no-cache-dir "mcp<2" "fastmcp<4" notebooklm-mcp-cli

# Install gogcli (supports amd64 and arm64)
# Upstream repository: https://github.com/openclaw/gogcli
ARG GOGCLI_VERSION=0.40.0
RUN ARCH=$(dpkg --print-architecture) \
    && mkdir -p /tmp/gogcli_extract \
    && if [ "${GOGCLI_VERSION}" = "latest" ]; then \
         TAG=$(wget -qO- "https://api.github.com/repos/openclaw/gogcli/releases/latest" 2>/dev/null | grep '"tag_name":' | sed -E 's/.*"v?([^"]+)".*/\1/'); \
         VERSION="${TAG:-0.40.0}"; \
       else \
         VERSION="${GOGCLI_VERSION#v}"; \
       fi \
    && wget "https://github.com/openclaw/gogcli/releases/download/v${VERSION}/gogcli_${VERSION}_linux_${ARCH}.tar.gz" -O /tmp/gogcli.tar.gz \
    && tar -xzf /tmp/gogcli.tar.gz -C /tmp/gogcli_extract \
    && find /tmp/gogcli_extract -type f \( -name "gog" -o -name "gog_*" \) | head -1 | xargs -I{} cp {} /usr/local/bin/gog \
    && chmod +x /usr/local/bin/gog \
    && rm -rf /tmp/gogcli.tar.gz /tmp/gogcli_extract

# Assets absent from git (see .gitignore) are provisioned by
# scripts/provision_assets.py into two directories, for two different reasons:
#
#   /opt/aoc       Large regenerable downloads. docker-compose maps a named
#                  volume here so rebuilding the image reuses them instead of
#                  re-fetching ~150MB, and so they survive `docker compose down`.
#   /opt/aoc-seed  Files destined for /app, which the bind mount hides. Kept
#                  OUT of the volume on purpose: Docker seeds a named volume
#                  from the image only while the volume is empty, so anything
#                  stored inside it would be frozen at the first image that
#                  populated it and no later build could correct it.
#
# Neither lives under /app, which docker-compose replaces with a bind mount of
# the host checkout. HF_HOME is pinned so the cache the build populates is the
# one STTEngine reads instead of downloading on the first transcription.
ENV AOC_ASSET_ROOT=/opt/aoc \
    AOC_SEED_DIR=/opt/aoc-seed \
    HF_HOME=/opt/aoc/hf \
    AOC_STT_MODEL=base.en

# Provision *before* copying the source tree: `COPY . .` is invalidated by every
# edit, and a provisioning step behind it would re-download the model on each
# build. Only these two inputs can bust the layer now.
COPY scripts/provision_assets.py scripts/
COPY assets ./assets

# Set AOC_PROVISION_STRICT=1 to make a failed download fail the build.
ARG AOC_PROVISION_STRICT=0
RUN AOC_PROVISION_STRICT=${AOC_PROVISION_STRICT} \
    python3 scripts/provision_assets.py --stage build

# Copy application source code
COPY . .

# Create non-root user and prepare required directories
RUN useradd -m appuser \
    && mkdir -p /home/appuser/.ssh \
                /home/appuser/.config/gogcli \
                /home/appuser/pkm \
                /home/appuser/workspaces \
    && chown -R appuser:appuser /home/appuser /app /opt/aoc /opt/aoc-seed

# Set environment variables for gogcli headless file keyring and non-interactive git operations
ENV GOG_KEYRING_BACKEND=file \
    GIT_TERMINAL_PROMPT=0 \
    GIT_ASKPASS="" \
    GIT_SSH_COMMAND="ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15"

# Copy entrypoint script
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Set entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Default command
CMD ["python", "main.py"]
