# mDNS Sidecar
### Local Network Discovery — Host-Native Service

The mDNS Sidecar is a lightweight Python FastAPI process that runs **natively on the host OS** (not inside Docker) and exposes a single HTTP endpoint used by NeonBeam OS to automatically discover NeonBeam Core and NeonBeam Lens on the local network via Zeroconf / mDNS.

> **Part of the NeonBeam Suite.** See the [root README](../README.md) for the full system architecture.

---

## Why Native (Not Docker)?

mDNS (Zeroconf / Bonjour) relies on multicast UDP packets that are broadcast at the network-interface level. Docker bridge networking does not forward multicast traffic to containers, so a containerized sidecar cannot browse the LAN's mDNS announcements. Running it natively on the host gives it direct access to the real network stack.

> On Linux, `network_mode: host` in Docker Compose is a functional alternative — see the Production section.

---

## Stack

| Layer | Technology |
|---|---|
| API Framework | Python 3.11 + FastAPI |
| Runtime | uvicorn |
| mDNS | `zeroconf` |
| Network Probing | `httpx` (async) |

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DISCOVERY_PORT` | `3001` | Port the sidecar listens on |
| `DISCOVERY_HOST` | `0.0.0.0` | Bind address |

---

## Development — Full Stack on One Host

### Prerequisites

- Python 3.11+
- Access to the `./discovery_sidecar/` directory

### Windows

```powershell
# From the repo root:
.\discovery_sidecar\start_sidecar.ps1
```

The script creates a `.venv` virtual environment on first run, installs dependencies, and starts uvicorn on port 3001.

### Linux / macOS

```bash
# From the repo root:
./discovery_sidecar/start_sidecar.sh
```

### Manual Start (any OS)

```bash
cd discovery_sidecar
python -m venv .venv
source .venv/bin/activate          # Linux/macOS
# .venv\Scripts\activate           # Windows

pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 3001
```

### Verify It Is Running

```bash
curl http://localhost:3001/api/discovery/scan
```

You should receive a JSON list of discovered services (or an empty list if none are on the network).

---

## Production (Linux Host — Raspberry Pi or Server)

On Linux, the sidecar can optionally run inside Docker using `network_mode: host`, which gives the container full access to the host's network interfaces for mDNS browsing.

### Option A — Docker with Host Networking (recommended for Pi)

```yaml
# In docker-compose.prod.yml — already configured:
discovery-sidecar:
  build:
    context: ./discovery_sidecar
    dockerfile: Dockerfile
  network_mode: host       # Container shares the host network stack
  restart: unless-stopped
```

```bash
# Build and start from repo root:
docker compose -f docker-compose.prod.yml up -d --build discovery-sidecar
```

### Option B — Native systemd Service (Pi)

For maximum reliability, run the sidecar as a systemd unit so it starts on boot independently of Docker.

```bash
# 1. Install to system Python or a dedicated venv
cd /opt/neonbeam-sidecar
python3 -m venv .venv
.venv/bin/pip install -r /path/to/discovery_sidecar/requirements.txt

# 2. Create the systemd unit file
sudo tee /etc/systemd/system/neonbeam-sidecar.service > /dev/null <<EOF
[Unit]
Description=NeonBeam mDNS Discovery Sidecar
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=pi
WorkingDirectory=/opt/neonbeam-sidecar
ExecStart=/opt/neonbeam-sidecar/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 3001
Restart=on-failure

[Install]
WantedBy=multi-user.target
EOF

# 3. Enable and start
sudo systemctl daemon-reload
sudo systemctl enable neonbeam-sidecar
sudo systemctl start neonbeam-sidecar
sudo systemctl status neonbeam-sidecar
```

---

## Git Repository

The mDNS Sidecar is designed to be maintained as its own standalone git repository.

```bash
cd discovery_sidecar
git init
git remote add origin <your-remote-url>
git add .
git commit -m "Initial commit — NeonBeam mDNS Sidecar"
git push -u origin main
```

---

## Project Layout

```
discovery_sidecar/
├── main.py             # FastAPI app — /api/discovery/scan endpoint
├── requirements.txt
├── Dockerfile          # Used only for Linux production (network_mode: host)
├── start_sidecar.ps1   # Windows launcher script
└── start_sidecar.sh    # Linux / macOS launcher script
```
