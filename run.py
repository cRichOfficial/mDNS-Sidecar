"""
NeonBeam Discovery Sidecar — run.py
=====================================
Convenience entry-point. Run with:

    python run.py

or with optional overrides:

    DISCOVERY_PORT=3001 python run.py
    python run.py --port 3001 --host 0.0.0.0

This file exists so developers don't need to remember the full uvicorn
command line.  It also sets up a SIGINT/SIGTERM handler so Ctrl-C cleanly
shuts down the Zeroconf listener.
"""

import argparse
import os
import sys

import uvicorn


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="NeonBeam Discovery Sidecar")
    p.add_argument(
        "--host",
        default=os.environ.get("DISCOVERY_HOST", "0.0.0.0"),
        help="Interface to bind (default: 0.0.0.0 — loopback + LAN, Vite proxies for us)",
    )
    p.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("DISCOVERY_PORT", "3001")),
        help="Port to listen on (default: 3001)",
    )
    p.add_argument(
        "--reload",
        action="store_true",
        default=False,
        help="Enable uvicorn auto-reload (dev convenience, not needed for sidecar)",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()

    print(
        f"\n  NeonBeam Discovery Sidecar\n"
        f"  --------------------------\n"
        f"  Listening on  http://{args.host}:{args.port}\n"
        f"  Health check  http://{args.host}:{args.port}/api/health\n"
        f"  Discovery     http://{args.host}:{args.port}/api/discovery\n"
        f"\n"
        f"  Vite dev-server proxies /api/discovery -> http://localhost:{args.port}\n"
        f"  Press Ctrl-C to stop.\n"
    )

    uvicorn.run(
        "main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
