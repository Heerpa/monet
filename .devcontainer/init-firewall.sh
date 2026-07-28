#!/usr/bin/env bash
# Default-deny egress firewall for the DNA-PAINT Claude Code dev container.
# Allows only DNS, loopback, established connections, and a whitelist of the
# domains needed for git / pip / npm / Claude. This is what makes it safe to run
# `claude --dangerously-skip-permissions` inside the container: even a misbehaving
# agent cannot exfiltrate to, or pull code from, anywhere off the whitelist.
# Needs NET_ADMIN/NET_RAW (granted in devcontainer.json runArgs).
set -euo pipefail

iptables -F; iptables -X 2>/dev/null || true

# Loopback
iptables -A INPUT  -i lo -j ACCEPT
iptables -A OUTPUT -o lo -j ACCEPT

# DNS (needed to resolve the whitelist)
iptables -A OUTPUT -p udp --dport 53 -j ACCEPT
iptables -A OUTPUT -p tcp --dport 53 -j ACCEPT
iptables -A INPUT  -p udp --sport 53 -j ACCEPT
iptables -A INPUT  -p tcp --sport 53 -j ACCEPT

# Established / related
iptables -A INPUT  -m state --state ESTABLISHED,RELATED -j ACCEPT
iptables -A OUTPUT -m state --state ESTABLISHED,RELATED -j ACCEPT

# Whitelist set
ipset destroy allowed-domains 2>/dev/null || true
ipset create allowed-domains hash:net

add_domain() {
  local d="$1" ip
  for ip in $(dig +short A "$d" | grep -E '^[0-9.]+$'); do
    ipset add allowed-domains "$ip" 2>/dev/null || true
  done
}

# GitHub's published IP ranges (git, api, web, codeload, raw, objects).
# Best-effort: a transient failure (e.g. a 504 from api.github.com) must not
# abort the whole firewall init and brick container startup. --retry rides out
# blips; on total failure we fall back to the add_domain resolution below.
if meta=$(curl -fsSL --retry 5 --retry-all-errors --retry-delay 2 --max-time 30 \
            https://api.github.com/meta); then
  printf '%s' "$meta" \
   | python3 -c 'import json,sys; d=json.load(sys.stdin); [print(c) for k in ("web","api","git") if k in d for c in d[k]]' \
   | grep -E '^[0-9.]+/[0-9]+$' \
   | while read -r cidr; do ipset add allowed-domains "$cidr" 2>/dev/null || true; done
else
  echo "[firewall] WARN: could not fetch api.github.com/meta; falling back to DNS resolution of GitHub hosts." >&2
fi

# Package indexes + the Anthropic API
for d in \
  api.anthropic.com statsig.anthropic.com \
  registry.npmjs.org \
  pypi.org files.pythonhosted.org pypi.python.org \
  github.com api.github.com codeload.github.com \
  objects.githubusercontent.com raw.githubusercontent.com; do
  add_domain "$d"
done

# >>> ADD LAB-INTERNAL HOSTS HERE if Claude needs them, e.g. the SLURM cluster
# login node, a self-hosted picasso-registry, or your Confluence:
#   add_domain cluster.miblab.example
#   add_domain registry.miblab.example
#   add_domain confluence.miblab.example

# Permit egress only to the whitelist; drop everything else.
iptables -A OUTPUT -m set --match-set allowed-domains dst -j ACCEPT
iptables -P INPUT   DROP
iptables -P FORWARD DROP
iptables -P OUTPUT  DROP

echo "[firewall] up — allowed: GitHub, PyPI, npm, api.anthropic.com (+DNS)."
