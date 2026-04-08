FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    git \
    curl \
    wget \
    vim \
    tar \
    && rm -rf /var/lib/apt/lists/*

# Install notebooklm-cli
RUN pip install notebooklm-cli

# Set working directory
WORKDIR /app

# Copy requirements.txt first to leverage Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Install gogcli
RUN wget https://github.com/steipete/gogcli/releases/download/v0.12.0/gogcli_0.12.0_linux_amd64.tar.gz -O /tmp/gogcli.tar.gz \
    && tar -xzf /tmp/gogcli.tar.gz -C /tmp \
    && find /tmp -type f -name gogcli -exec mv {} /usr/local/bin/gogcli \; \
    && chmod +x /usr/local/bin/gogcli \
    && rm -rf /tmp/gogcli.tar.gz

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
