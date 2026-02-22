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
# Use TARGETARCH to pick the right JRE and native library for the build platform
ARG TARGETARCH
RUN ADOPTIUM_ARCH=$(case "${TARGETARCH:-amd64}" in arm64) echo aarch64;; *) echo x64;; esac) && \
    curl -sL "https://api.adoptium.net/v3/binary/latest/21/ga/linux/${ADOPTIUM_ARCH}/jre/hotspot/normal/eclipse" \
      -o /tmp/temurin-jre.tar.gz && \
    tar xzf /tmp/temurin-jre.tar.gz -C /opt && \
    ln -s /opt/jdk-*/bin/java /usr/local/bin/java && \
    rm /tmp/temurin-jre.tar.gz && \
    curl -sL "https://github.com/AsamK/signal-cli/releases/download/v0.13.24/signal-cli-0.13.24.tar.gz" \
      -o /tmp/signal-cli.tar.gz && \
    tar xzf /tmp/signal-cli.tar.gz -C /opt && \
    ln -s /opt/signal-cli-0.13.24/bin/signal-cli /usr/local/bin/signal-cli && \
    rm /tmp/signal-cli.tar.gz
# JAVA_HOME not needed — signal-cli finds java via PATH symlink above

# Patch in aarch64 Linux native library for libsignal-client (not shipped by upstream)
# Pre-built by https://github.com/exquo/signal-libs-build
RUN if [ "$(uname -m)" = "aarch64" ]; then \
      LIBSIGNAL_VERSION=$(ls /opt/signal-cli-0.13.24/lib/libsignal-client-*.jar | sed 's/.*libsignal-client-\(.*\)\.jar/\1/') && \
      curl -sL "https://github.com/exquo/signal-libs-build/releases/download/libsignal_v${LIBSIGNAL_VERSION}/libsignal_jni.so-v${LIBSIGNAL_VERSION}-aarch64-unknown-linux-gnu.tar.gz" \
        | tar xzf - -C /tmp && \
      mv /tmp/libsignal_jni.so /tmp/libsignal_jni_aarch64.so && \
      python3 -c "import zipfile; z=zipfile.ZipFile('/opt/signal-cli-0.13.24/lib/libsignal-client-${LIBSIGNAL_VERSION}.jar','a'); z.write('/tmp/libsignal_jni_aarch64.so','libsignal_jni_aarch64.so'); z.close()" && \
      rm -f /tmp/libsignal_jni* ; \
    fi

# Create non-root user with UID 1000 to match host user
RUN useradd -m -s /bin/bash -u 1000 nanobot

# Gateway default port
EXPOSE 18790

USER nanobot
WORKDIR /home/nanobot

ENTRYPOINT ["nanobot"]
CMD ["status"]
