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
