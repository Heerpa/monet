# Python base with a non-root `vscode` user + common dev tooling.
FROM mcr.microsoft.com/devcontainers/python:3.10

# Qt (PyQt6) runs headless in the container (no display) — offscreen platform.
ENV QT_QPA_PLATFORM=offscreen

# Node (Claude Code is an npm package) + firewall tooling (iptables/ipset/dig).
RUN apt-get update && apt-get install -y --no-install-recommends \
      iptables ipset dnsutils curl ca-certificates \
      cmake build-essential \
      libegl1 libgl1 libxkbcommon0 libdbus-1-3 \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y nodejs \
 && npm install -g @anthropic-ai/claude-code \
 && rm -rf /var/lib/apt/lists/*

# Turn OFF Claude Code's per-command bwrap sandbox in this container. The
# container is the security boundary (default-deny egress firewall + non-root
# `vscode` + Docker default seccomp/proc masking + OrbStack VM), so the nested
# sandbox is redundant -- and its fresh-/proc mount fails in an unprivileged
# container anyway. Managed settings are the highest-precedence scope and live
# ONLY in the image (not the host-mounted ~/.claude), so this never disables
# the sandbox for Claude Code running on the host.
RUN mkdir -p /etc/claude-code \
 && printf '%s\n' '{ "sandbox": { "enabled": false } }' \
      > /etc/claude-code/managed-settings.json

# Shared Python dev tooling for all the repos (matches the Step-0 conventions).
RUN pip install --no-cache-dir \
      "black==25.9.0" flake8 Flake8-pyproject pre-commit pytest \
      setuptools-scm build uv

# Firewall script + allow the vscode user to run ONLY it via sudo (no password).
COPY init-firewall.sh /usr/local/bin/init-firewall.sh
RUN chmod +x /usr/local/bin/init-firewall.sh \
 && echo 'vscode ALL=(root) NOPASSWD: /usr/local/bin/init-firewall.sh' \
      > /etc/sudoers.d/claude-firewall \
 && chmod 0440 /etc/sudoers.d/claude-firewall
