FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive

# Layer 1: system packages (tools available to agents inside the sandbox)
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl wget jq git vim nano \
    python3 python3-pip python3-venv \
    htop net-tools iputils-ping dnsutils \
    ca-certificates sudo \
    && rm -rf /var/lib/apt/lists/*

# Layer 2: sudo + user isolation setup
# - passwordless sudo for root (needed for lazy user creation via sudo -u)
# - mcpusers group: every provisioned user joins it; per-group resource
#   limits are rendered into /etc/security/limits.d by entrypoint.sh
# - pam_limits on sudo sessions so those limits apply to sudo -u commands
RUN echo "root ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/sandbox \
    && groupadd mcpusers \
    && sh -c 'grep -q pam_limits.so /etc/pam.d/sudo || echo "session    required   pam_limits.so" >> /etc/pam.d/sudo'

# Layer 3: Python deps (cached separately from app code)
COPY server/requirements.txt /opt/server/requirements.txt
RUN pip3 install --no-cache-dir --break-system-packages -r /opt/server/requirements.txt

# Layer 4: app code
COPY server/ /opt/server/
# Built-in skills: synced into /workspace/public_skills by entrypoint.sh on every start
COPY skills/ /opt/skills/
COPY entrypoint.sh /opt/entrypoint.sh
RUN chmod +x /opt/entrypoint.sh

# Layer 5: working directory
RUN mkdir -p /workspace

WORKDIR /workspace
EXPOSE 8080
ENTRYPOINT ["/opt/entrypoint.sh"]
