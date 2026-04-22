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

Two discovery mechanisms run in parallel
----------------------------------------
1. Local service probe  — tries http://127.0.0.1:<port>/api/health for
   each well-known NeonBeam port.  If a service is found, the returned URL
   uses the machine's LAN IP (not 127.0.0.1) so that remote clients such
   as a phone browsing over Wi-Fi can actually reach the service.

2. mDNS browse          — listens for _http._tcp.local. records tagged with
     service=hardware_comm   (Core Backend)
     service=machine_vision  (Lens Backend)
   Resolves hostnames found by Avahi/Bonjour on the LAN.

Adding future services
----------------------
Each Pi registers an Avahi/zeroconf record with a TXT key:
    service=hardware_comm   →  Core Backend URL  (neonbeam-core.local)
    service=machine_vision  →  Lens Backend URL  (neonbeam-lens.local)
When a new Pi comes online it simply sets its TXT record; no sidecar or
frontend changes are needed.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import urllib.request
from typing import Optional

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

# ── Well-known fallback hostnames ─────────────────────────────────────────────
# Used when no tagged mDNS records are found (e.g. early dev without Avahi).
_FALLBACKS: dict[str, str] = {
    "hardware_comm":  "http://neonbeam-core.local:8000",
    "machine_vision": "http://neonbeam-lens.local:8001",
}

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
def _probe_port_blocking(port: int, timeout: float = 1.5) -> bool:
    """
    Synchronous check: is there a NeonBeam service at 127.0.0.1:<port>?
    Uses urllib so we don't need an extra dependency (httpx etc.).
    """
    try:
        req = urllib.request.urlopen(
            f"http://127.0.0.1:{port}/api/health",
            timeout=timeout,
        )
        return req.status == 200
    except Exception:
        return False


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
        found = await asyncio.to_thread(_probe_port_blocking, port)
        if found:
            url = f"http://{lan_ip}:{port}"
            logger.info(f"Found {service_tag} locally at {url}")
            results.append(
                DiscoveredService(name=f"{service_tag} (this machine)", url=url, service=service_tag)
            )

    await asyncio.gather(*[_check(port, tag) for port, tag in _LOCAL_PORTS])
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


def _apply_fallbacks(
    found: list[DiscoveredService],
    already_found_tags: set[str],
) -> list[DiscoveredService]:
    """
    Append well-known .local hostname fallbacks for any service not yet discovered
    via localhost probe or mDNS, so the UI always has something to show.
    """
    for tag, url in _FALLBACKS.items():
        if tag not in already_found_tags:
            found.append(DiscoveredService(name=f"{tag} (fallback)", url=url, service=tag))
    return found


# ── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "discovery_sidecar", "lan_ip": _get_lan_ip()}


@app.get("/api/discovery", response_model=DiscoveryResponse)
async def discover(timeout: Optional[float] = 3.0, fallback: bool = True) -> DiscoveryResponse:
    """
    Discover NeonBeam services using two parallel mechanisms:

    1. Local probe  — checks 127.0.0.1 for each well-known port and returns
                      the machine's LAN IP so remote clients can reach the service.
    2. mDNS browse  — listens on the LAN for Avahi/Bonjour-registered services.

    Query params
    ------------
    timeout  : seconds to browse mDNS (local probe always uses 1.5s max)
    fallback : if true, include well-known .local hostnames for services not found
    """
    logger.info(f"Discovery request — mDNS timeout={timeout}s  fallback={fallback}")

    # Run local probe and mDNS browse concurrently
    local_task = _probe_local_services()
    mdns_task  = asyncio.to_thread(_browse_blocking, timeout)

    local_results, mdns_results = await asyncio.gather(local_task, mdns_task)

    # Merge: local results first (most reliable), then any mDNS results that
    # aren't already covered by a local probe (different host/port = real Pi).
    local_urls = {svc.url for svc in local_results}
    extra_mdns = [svc for svc in mdns_results if svc.url not in local_urls]
    combined   = local_results + extra_mdns

    found_tags = {s.service for s in combined}

    if fallback:
        combined = _apply_fallbacks(combined, found_tags)

    logger.info(f"Discovery complete — {len(combined)} service(s) returned.")
    return DiscoveryResponse(found=combined)
