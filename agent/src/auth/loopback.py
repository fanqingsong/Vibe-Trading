"""Shared loopback-client detection for auth guards.

Both ``api_server.py`` and ``src/auth/routes.py`` need to decide whether a
request originates from a trusted local client (loopback or the Docker host
gateway when explicitly trusted). Centralizing it avoids logic drift.
"""

from __future__ import annotations

import ipaddress
import os
from pathlib import Path

_DOCKER_LOOPBACK_ENV = "VIBE_TRADING_TRUST_DOCKER_LOOPBACK"


def env_flag_enabled(name: str) -> bool:
    """Return whether a boolean environment flag is enabled."""
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _default_gateway_ips() -> set:
    """Return IPv4 default gateway addresses from Linux procfs."""
    gateways: set = set()
    try:
        lines = Path("/proc/net/route").read_text(encoding="utf-8").splitlines()
    except OSError:
        return gateways

    for line in lines[1:]:
        fields = line.split()
        if len(fields) < 3 or fields[1] != "00000000":
            continue
        try:
            raw = int(fields[2], 16).to_bytes(4, byteorder="little")
            gateways.add(ipaddress.IPv4Address(raw))
        except ValueError:
            continue
    return gateways


def trusted_docker_loopback_ip(ip) -> bool:
    """Return whether an IP is the trusted Docker host gateway.

    Docker Desktop presents host requests to a container as the bridge gateway
    instead of 127.0.0.1. This escape hatch is safe only when the published
    port is bound to host loopback, so the official compose file enables it
    together with a 127.0.0.1 port binding.
    """
    if not isinstance(ip, ipaddress.IPv4Address):
        return False
    if not env_flag_enabled(_DOCKER_LOOPBACK_ENV):
        return False
    return ip in _default_gateway_ips()


def is_local_client(host: str) -> bool:
    """Return True when *host* (request.client.host) is a trusted local client."""
    if host in {"localhost", "testclient"}:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    if ip.is_loopback:
        return True
    # The Docker host-gateway trust is a dev-mode escape hatch. When multi-user
    # auth is active, do NOT honor it so that host-machine (browser/curl)
    # requests must authenticate just like any remote client.
    from src.auth.service import is_auth_enabled

    if is_auth_enabled():
        return False
    return trusted_docker_loopback_ip(ip)


def request_is_local(request) -> bool:
    """Return True when *request* originates from a trusted local client."""
    host = request.client.host if request.client else ""
    return is_local_client(host)
