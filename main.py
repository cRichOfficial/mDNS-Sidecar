"""
NeonBeam Discovery Sidecar — port 3001
======================================
Browses the local mDNS/DNS-SD network for NeonBeam services and returns
JSON so the browser-based frontend can autodiscover hardware_comm and
machine_vision hosts without any manual IP entry.

Architecture note
-----------------
This sidecar MUST run co-resident with the frontend's HTTP/Vite server
(i.e. on the user's PC, not on a Pi).  It bridges the gap: the browser
has no access to the local network's mDNS stack or to localhost on the
server machine, but this process does.
Vite dev-server (and nginx in production) proxy /api/discovery here.

Three discovery mechanisms run in parallel
------------------------------------------
1. Local service probe  — tries http://127.0.0.1:<port>/api/health for
   each well-known NeonBeam port.  If a service is found, the returned URL
   uses the machine's LAN IP (not 127.0.0.1) so that remote clients such
   as a phone browsing over Wi-Fi can actually reach the service.

2. mDNS browse          — listens for _http._tcp.local. records tagged with
     service=hardware_comm   (Core Backend)
     service=machine_vision  (Lens Backend)
   Works best when NeonBeam services run with network_mode: host on Linux
   so their Zeroconf registration reaches the LAN multicast group.

3. /24 subnet scan      — concurrent TCP probe of every host on the local
   subnet followed by HTTP /api/health verification.  Finds any NeonBeam
   service regardless of mDNS advertising or Docker network mode.  This is
   the primary mechanism for Docker Desktop (Windows/macOS) where multicast
   does not cross the container NAT.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import urllib.request

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
from pydantic import BaseModel

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
logger = logging.getLogger("discovery_sidecar")

# ── FastAPI app ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="NeonBeam Discovery Sidecar",
    description="mDNS browser + localhost prober that exposes NeonBeam services to the frontend.",
    version="1.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ── Well-known localhost ports to probe ───────────────────────────────────────
# In port order: (<port>, <service_tag>)
_LOCAL_PORTS: list[tuple[int, str]] = [
    (8000, "hardware_comm"),
    (8001, "machine_vision"),
]

# ── Known NeonBeam service tags ───────────────────────────────────────────────
_KNOWN_SERVICES = frozenset({"hardware_comm", "machine_vision"})

# ── Response schema ───────────────────────────────────────────────────────────
class DiscoveredService(BaseModel):
    name: str       # mDNS service name or descriptor
    url: str        # full http://host:port  — always LAN-reachable
    service: str    # "hardware_comm" | "machine_vision" | "unknown"

class DiscoveryResponse(BaseModel):
    found: list[DiscoveredService]


# ── LAN IP helper ─────────────────────────────────────────────────────────────
def _get_lan_ip() -> str:
    """
    Return the machine's primary LAN IP address — the IP that other devices
    on the same network can use to reach this machine.

    Uses a UDP connect-without-sending-data trick: the OS assigns the source
    IP for the route to 8.8.8.8 (Google DNS), revealing the LAN interface IP.
    No actual packet is sent.

    Falls back to '127.0.0.1' if no network is available.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


# ── Local service probe (runs in a thread) ────────────────────────────────────
def _probe_health_blocking(ip: str, port: int, timeout: float = 1.5) -> bool:
    """
    Synchronous HTTP check: is there a NeonBeam service at ip:port/api/health?
    """
    try:
        req = urllib.request.urlopen(
            f"http://{ip}:{port}/api/health",
            timeout=timeout,
        )
        return req.status == 200
    except Exception:
        return False


def _tcp_open(ip: str, port: int, timeout: float = 0.2) -> bool:
    """
    Lightweight TCP handshake check — does not send any data.
    Used for the subnet sweep stage 1 before committing to a full HTTP check.
    """
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def _reverse_hostname(ip: str) -> str:
    """Try to resolve an IP to its mDNS / DNS hostname.  Falls back to the IP."""
    try:
        return socket.gethostbyaddr(ip)[0]   # e.g. 'roto-laser.local'
    except Exception:
        return ip


async def _probe_local_services() -> list[DiscoveredService]:
    """
    Probe all well-known local ports in parallel.

    IMPORTANT: returns LAN IP (e.g. 192.168.1.50), NOT 127.0.0.1.
    This ensures that URLs sent back to remote clients (phone on Wi-Fi)
    are actually reachable — 'localhost' in the browser means the phone's
    own loopback, so we must return the server's real LAN address.
    """
    lan_ip = _get_lan_ip()
    logger.info(f"Local probe — LAN IP detected as {lan_ip}")

    results: list[DiscoveredService] = []

    async def _check(port: int, service_tag: str) -> None:
        found = await asyncio.to_thread(_probe_health_blocking, "127.0.0.1", port)
        if found:
            url = f"http://{lan_ip}:{port}"
            logger.info(f"Found {service_tag} locally at {url}")
            results.append(
                DiscoveredService(name=f"{service_tag} (this machine)", url=url, service=service_tag)
            )

    await asyncio.gather(*[_check(port, tag) for port, tag in _LOCAL_PORTS])
    return results


# ── /24 subnet scan ───────────────────────────────────────────────────────────
async def _scan_subnet_services() -> list[DiscoveredService]:
    """
    Concurrent /24 subnet sweep to find NeonBeam services on the LAN.

    Stage 1 — TCP handshake across all 254 hosts (semaphore-limited to 50
               concurrent probes, 0.2 s timeout each).  Only IPs that respond
               on the target port proceed to stage 2.

    Stage 2 — HTTP /api/health verification on the few survivors.  This
               confirms the service is actually a NeonBeam endpoint, not just
               any open port.

    Skips the local machine's own IP (already covered by _probe_local_services).
    Covers Docker Desktop environments where cross-NAT multicast is unavailable.
    """
    lan_ip = _get_lan_ip()
    if lan_ip == "127.0.0.1":
        return []   # no LAN interface — can't scan

    subnet_prefix = ".".join(lan_ip.split(".")[:3])   # e.g. "192.168.1"
    sem = asyncio.Semaphore(50)                        # max 50 simultaneous TCP probes

    # Stage 1: rapid TCP handshake sweep
    open_hosts: list[tuple[str, int, str]] = []
    lock = asyncio.Lock()

    async def _tcp_check(ip: str, port: int, service_tag: str) -> None:
        if ip == lan_ip:
            return   # skip self — covered by local probe
        async with sem:
            reachable = await asyncio.to_thread(_tcp_open, ip, port)
        if reachable:
            async with lock:
                open_hosts.append((ip, port, service_tag))

    await asyncio.gather(*[
        _tcp_check(f"{subnet_prefix}.{i}", port, tag)
        for i in range(1, 255)
        for port, tag in _LOCAL_PORTS
    ])

    if not open_hosts:
        return []

    logger.info(f"Subnet scan — {len(open_hosts)} open port(s) found, verifying…")

    # Stage 2: HTTP health check on survivors
    results: list[DiscoveredService] = []
    for ip, port, service_tag in open_hosts:
        ok = await asyncio.to_thread(_probe_health_blocking, ip, port)
        if ok:
            hostname = await asyncio.to_thread(_reverse_hostname, ip)
            display  = hostname if hostname != ip else ip
            url      = f"http://{ip}:{port}"
            logger.info(f"Subnet scan — confirmed {service_tag} at {url} ({display})")
            results.append(
                DiscoveredService(
                    name=f"{service_tag} ({display})",
                    url=url,
                    service=service_tag,
                )
            )

    return results


# ── mDNS listener ─────────────────────────────────────────────────────────────
class _NeonBeamListener(ServiceListener):
    """Accumulates mDNS records during the browse window."""

    def __init__(self) -> None:
        self.results: list[DiscoveredService] = []

    def add_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        info = zc.get_service_info(type_, name)
        if not info:
            return

        # Decode TXT records
        txt: dict[str, str] = {}
        for key_bytes, val_bytes in (info.properties or {}).items():
            try:
                k = key_bytes.decode() if isinstance(key_bytes, bytes) else key_bytes
                v = val_bytes.decode() if isinstance(val_bytes, bytes) else (val_bytes or "")
                txt[k] = v
            except Exception:
                pass

        service_tag = txt.get("service", "unknown")
        if service_tag not in _KNOWN_SERVICES:
            # Ignore unrelated services (printers, Chromecasts, etc.)
            return

        host = info.server or name
        port = info.port or 80
        url  = f"http://{host}:{port}"

        logger.info(f"mDNS — discovered {service_tag} at {url}")
        self.results.append(DiscoveredService(name=name, url=url, service=service_tag))

    def remove_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        pass

    def update_service(self, zc: Zeroconf, type_: str, name: str) -> None:
        self.add_service(zc, type_, name)


# ── mDNS browse helper (runs in a thread) ─────────────────────────────────────
def _browse_blocking(timeout_s: float = 3.0) -> list[DiscoveredService]:
    """Synchronous mDNS browse call, meant to run in asyncio.to_thread."""
    import time
    zc       = Zeroconf()
    listener = _NeonBeamListener()
    browser  = ServiceBrowser(zc, "_http._tcp.local.", listener)  # noqa: F841
    time.sleep(timeout_s)
    zc.close()
    return listener.results



# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "discovery_sidecar", "lan_ip": _get_lan_ip()}


@app.get("/api/discovery", response_model=DiscoveryResponse)
async def discover(timeout: float = 3.0) -> DiscoveryResponse:
    """
    Discover NeonBeam services using three parallel mechanisms:

    1. Local probe   — checks 127.0.0.1 for each well-known port and returns
                       the machine's LAN IP so remote clients can reach the service.
    2. mDNS browse   — listens on the LAN for Avahi/Bonjour-registered services
                       (works when NeonBeam services use network_mode: host on Pi).
    3. Subnet scan   — concurrent TCP sweep of the local /24 followed by HTTP
                       verification.  Finds any NeonBeam service on the LAN
                       regardless of mDNS configuration or Docker network mode.

    Returns an empty list if nothing is found.  No fallback hostnames are ever
    injected — if a service isn’t genuinely reachable it won’t appear here.

    Query params
    ------------
    timeout : seconds to browse mDNS  (local probe and subnet scan run concurrently)
    """
    logger.info(f"Discovery request — mDNS timeout={timeout}s")

    # Run all three mechanisms concurrently
    local_results, mdns_results, subnet_results = await asyncio.gather(
        _probe_local_services(),
        asyncio.to_thread(_browse_blocking, timeout),
        _scan_subnet_services(),
    )

    # Merge, deduplicating by URL.  Priority: local > mDNS > subnet scan.
    seen_urls: set[str] = set()
    combined: list[DiscoveredService] = []

    for svc in local_results + mdns_results + subnet_results:
        if svc.url not in seen_urls:
            seen_urls.add(svc.url)
            combined.append(svc)

    logger.info(f"Discovery complete — {len(combined)} service(s) found.")
    return DiscoveryResponse(found=combined)
