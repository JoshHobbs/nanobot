FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Install Node.js 20 for the WhatsApp bridge
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl ca-certificates gnupg git && \
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_20.x nodistro main" > /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get purge -y gnupg && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (cached layer)
COPY pyproject.toml README.md LICENSE ./
RUN mkdir -p nanobot bridge && touch nanobot/__init__.py && \
    uv pip install --system --no-cache . && \
    rm -rf nanobot bridge

# Copy the full source and install
COPY nanobot/ nanobot/
COPY bridge/ bridge/
RUN uv pip install --system --no-cache .

# Build the WhatsApp bridge
WORKDIR /app/bridge
RUN npm install && npm run build
WORKDIR /app

# Install Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Install signal-cli (Java-based, requires JRE 21+)
RUN curl -sL "https://api.adoptium.net/v3/binary/latest/21/ga/linux/x64/jre/hotspot/normal/eclipse" \
      -o /tmp/temurin-jre.tar.gz && \
    tar xzf /tmp/temurin-jre.tar.gz -C /opt && \
    ln -s /opt/jdk-*/bin/java /usr/local/bin/java && \
    rm /tmp/temurin-jre.tar.gz && \
    curl -sL "https://github.com/AsamK/signal-cli/releases/download/v0.13.24/signal-cli-0.13.24.tar.gz" \
      -o /tmp/signal-cli.tar.gz && \
    tar xzf /tmp/signal-cli.tar.gz -C /opt && \
    ln -s /opt/signal-cli-0.13.24/bin/signal-cli /usr/local/bin/signal-cli && \
    rm /tmp/signal-cli.tar.gz
ENV JAVA_HOME=/opt/jdk-21.0.10+7-jre

# Create non-root user with UID 1000 to match host user
RUN useradd -m -s /bin/bash -u 1000 nanobot

# Gateway default port
EXPOSE 18790

USER nanobot
WORKDIR /home/nanobot

ENTRYPOINT ["nanobot"]
CMD ["status"]
