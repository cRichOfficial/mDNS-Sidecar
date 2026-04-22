# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║  NeonBeam Discovery Sidecar — Dockerfile                                   ║
# ║                                                                              ║
# ║  Slim Python 3.11 image.  Runs uvicorn on port 3001.                        ║
# ║                                                                              ║
# ║  mDNS / network access note                                                  ║
# ║  ─────────────────────────────────────────────────────────────────────────  ║
# ║  Zeroconf browsing requires access to the host's network stack, not a       ║
# ║  Docker bridge network.  Use one of the following strategies:               ║
# ║                                                                              ║
# ║  Linux (prod / Pi host)                                                      ║
# ║    network_mode: host   — container shares the host's network directly.     ║
# ║    This is the only reliable method for mDNS on Linux.                      ║
# ║                                                                              ║
# ║  Windows / macOS (dev, Docker Desktop)                                       ║
# ║    Docker Desktop does not support network_mode: host.  The sidecar falls   ║
# ║    back gracefully — localhost probing still works; LAN mDNS is skipped.    ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

FROM python:3.11-slim

WORKDIR /app

# Install system deps needed by zeroconf (netifaces, ifaddr)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 3001

# Reload is intentionally disabled in this image — the sidecar has no
# hot-reload-worthy Python state; restart is handled by compose restart policy.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "3001"]
