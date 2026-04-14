FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    wget \
    vim \
    tar \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy everything into the container
COPY . .

# Install Python dependencies
RUN pip3 install --no-cache-dir -r requirements.txt

# Install visual browser bindings for visual AI automation
RUN playwright install chromium
RUN playwright install-deps

# Install notebooklm-mcp-cli (nlm command)
RUN pip install notebooklm-mcp-cli

# Install gogcli
RUN mkdir -p /tmp/gogcli_extract \
    && wget https://github.com/steipete/gogcli/releases/download/v0.12.0/gogcli_0.12.0_linux_amd64.tar.gz -O /tmp/gogcli.tar.gz \
    && tar -xzf /tmp/gogcli.tar.gz -C /tmp/gogcli_extract \
    && find /tmp/gogcli_extract -type f \( -name "gog" -o -name "gog_*" \) | head -1 | xargs -I{} cp {} /usr/local/bin/gog \
    && chmod +x /usr/local/bin/gog \
    && rm -rf /tmp/gogcli.tar.gz /tmp/gogcli_extract

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
