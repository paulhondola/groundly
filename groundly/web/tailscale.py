"""Best-effort Tailscale discovery via the local `tailscale status --json` CLI."""

from __future__ import annotations

import ipaddress
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_MACOS_TAILSCALE_APP = Path("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
_CGNAT = ipaddress.ip_network("100.64.0.0/10")
_TS_DNS_RE = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+ts\.net$")


@dataclass(frozen=True, slots=True)
class TailscaleInfo:
    """Validated Tailscale network identity for the local machine."""

    ipv4: str
    dns_name: str | None = None

    @property
    def preferred_host(self) -> str:
        return self.dns_name or self.ipv4

    @property
    def allowed_hosts(self) -> frozenset[str]:
        return frozenset({self.ipv4, self.dns_name}) if self.dns_name else frozenset({self.ipv4})


def _find_tailscale_binary() -> str | None:
    binary = shutil.which("tailscale")
    if binary:
        return binary
    if (
        sys.platform == "darwin"
        and _MACOS_TAILSCALE_APP.is_file()
        and os.access(_MACOS_TAILSCALE_APP, os.X_OK)
    ):
        return str(_MACOS_TAILSCALE_APP)
    return None


def _valid_ts_dns(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    candidate = raw.strip().rstrip(".").lower()
    if not candidate or len(candidate) > 253:
        return None
    return candidate if _TS_DNS_RE.fullmatch(candidate) else None


def _first_cgnat_ipv4(raw_ips: object) -> str | None:
    if not isinstance(raw_ips, list):
        return None
    for entry in raw_ips:
        if not isinstance(entry, str):
            continue
        try:
            addr = ipaddress.ip_address(entry)
        except ValueError:
            continue
        if isinstance(addr, ipaddress.IPv4Address) and addr in _CGNAT:
            return str(addr)
    return None


def discover_tailscale() -> TailscaleInfo | None:
    """Return validated Tailscale IPv4 + MagicDNS name, or None when unavailable."""
    binary = _find_tailscale_binary()
    if not binary:
        return None
    try:
        proc = subprocess.run(
            [binary, "status", "--json", "--peers=false"],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=2.0,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict) or data.get("BackendState") != "Running":
        return None

    self_info = data.get("Self")
    self_dict = self_info if isinstance(self_info, dict) else {}
    ipv4 = _first_cgnat_ipv4(data.get("TailscaleIPs")) or _first_cgnat_ipv4(
        self_dict.get("TailscaleIPs")
    )
    if not ipv4:
        return None

    dns_name = _valid_ts_dns(self_dict.get("DNSName"))
    return TailscaleInfo(ipv4=ipv4, dns_name=dns_name)
