FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    wget \
    vim \
    tar \
    unzip \
    procps \
    chromium \
    && rm -rf /var/lib/apt/lists/*

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

# Install notebooklm-mcp-cli (nlm command)
RUN pip install --no-cache-dir notebooklm-mcp-cli

# Install gogcli (supports amd64 and arm64)
RUN ARCH=$(dpkg --print-architecture) \
    && mkdir -p /tmp/gogcli_extract \
    && wget "https://github.com/steipete/gogcli/releases/download/v0.12.0/gogcli_0.12.0_linux_${ARCH}.tar.gz" -O /tmp/gogcli.tar.gz \
    && tar -xzf /tmp/gogcli.tar.gz -C /tmp/gogcli_extract \
    && find /tmp/gogcli_extract -type f \( -name "gog" -o -name "gog_*" \) | head -1 | xargs -I{} cp {} /usr/local/bin/gog \
    && chmod +x /usr/local/bin/gog \
    && rm -rf /tmp/gogcli.tar.gz /tmp/gogcli_extract

# Copy application source code
COPY . .

# Create non-root user
RUN useradd -m appuser

# Create directory for SSH keys
RUN mkdir -p /home/appuser/.ssh && chown appuser:appuser /home/appuser/.ssh

# Copy entrypoint script
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# Set entrypoint
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

# Default command
CMD ["python", "main.py"]
