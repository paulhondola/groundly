"""Append-only benchmark result rows (one JSON object per line, per bench).

Rows carry numbers and provenance only, never page text.
"""

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

RESULTS_DIR = Path(__file__).parents[1] / "results"

_PACKAGES = ("docling", "rapidocr", "pypdfium2", "mlx", "onnxruntime", "torch")
_PROBE = (
    "import importlib.metadata as m, json\n"
    "out = {}\n"
    "for p in PKGS:\n"
    "    try:\n"
    "        out[p] = m.version(p)\n"
    "    except m.PackageNotFoundError:\n"
    "        pass\n"
    "print(json.dumps(out))\n"
)


def new_session() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def _sh(*cmd: str) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()


def machine() -> str:
    ram_gb = int(_sh("sysctl", "-n", "hw.memsize")) // 2**30
    mac = ".".join(_sh("sw_vers", "-productVersion").split(".")[:2])
    return f"{_sh('sysctl', '-n', 'hw.model')} {ram_gb}GB macOS-{mac}"


def versions(python: Path) -> dict[str, str]:
    out = _sh(str(python), "-c", _PROBE.replace("PKGS", repr(_PACKAGES)))
    return json.loads(out)


def append(bench: str, row: dict, results_dir: Path = RESULTS_DIR) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    with (results_dir / f"{bench}.jsonl").open("a") as f:
        f.write(json.dumps(row) + "\n")
