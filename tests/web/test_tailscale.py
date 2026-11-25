import json
import subprocess

from groundly.web.tailscale import TailscaleInfo, discover_tailscale


def _status(
    *,
    state: str = "Running",
    ips: list[str] | None = None,
    dns_name: str = "MacBook.Tail1234.ts.net.",
):
    return json.dumps(
        {
            "BackendState": state,
            "TailscaleIPs": ips if ips is not None else ["100.101.102.103", "fd7a::1"],
            "Self": {"DNSName": dns_name, "TailscaleIPs": ["100.101.102.103"]},
        }
    )


def test_discover_parses_magicdns_and_cgnat(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/tailscale")
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, stdout=_status(), stderr=""),
    )
    info = discover_tailscale()
    assert info == TailscaleInfo(ipv4="100.101.102.103", dns_name="macbook.tail1234.ts.net")
    assert info.preferred_host == "macbook.tail1234.ts.net"
    assert info.allowed_hosts == frozenset({"100.101.102.103", "macbook.tail1234.ts.net"})


def test_discover_handles_disabled_magicdns_and_invalid_dns(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/tailscale")
    for bad_dns in ("", "not-a-ts-domain", "evil..ts.net.", "bad host.ts.net."):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, d=bad_dns, **kw: subprocess.CompletedProcess(
                a[0], 0, stdout=_status(dns_name=d), stderr=""
            ),
        )
        info = discover_tailscale()
        assert info == TailscaleInfo(ipv4="100.101.102.103", dns_name=None)
        assert info.preferred_host == "100.101.102.103"


def test_discover_returns_none_when_stopped_non_cgnat_or_timed_out(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: "/usr/local/bin/tailscale")
    for payload in (_status(state="Stopped"), _status(ips=["192.168.1.10", "::1"])):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, p=payload, **kw: subprocess.CompletedProcess(a[0], 0, stdout=p, stderr=""),
        )
        assert discover_tailscale() is None

    def fail(*a, **kw):
        raise subprocess.TimeoutExpired(a[0], 2.0)

    monkeypatch.setattr(subprocess, "run", fail)
    assert discover_tailscale() is None


def test_discover_uses_macos_app_fallback(monkeypatch, tmp_path):
    fake_bin = tmp_path / "Tailscale"
    fake_bin.write_text("#!/bin/sh\n")
    fake_bin.chmod(0o755)
    monkeypatch.setattr("shutil.which", lambda name: None)
    monkeypatch.setattr("sys.platform", "darwin")
    monkeypatch.setattr("groundly.web.tailscale._MACOS_TAILSCALE_APP", fake_bin)
    seen_cmd: list[list[str]] = []

    def fake_run(cmd, **kw):
        seen_cmd.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=_status(dns_name=""), stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert discover_tailscale() == TailscaleInfo(ipv4="100.101.102.103", dns_name=None)
    assert seen_cmd == [[str(fake_bin), "status", "--json"]]
