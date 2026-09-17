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

# Copy application source code
COPY . .

# Create non-root user and prepare required directories
RUN useradd -m appuser \
    && mkdir -p /home/appuser/.ssh \
                /home/appuser/.config/gogcli \
                /home/appuser/pkm \
                /home/appuser/workspaces \
    && chown -R appuser:appuser /home/appuser /app

# Set environment variables for gogcli headless file keyring and non-interactive git operations
ENV GOG_KEYRING_BACKEND=file \
    GOG_KEYRING_PROVIDER=file \
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
