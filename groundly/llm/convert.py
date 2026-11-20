"""One-time conversion of bge-m3's torch pickles to f16 safetensors for MLX.

The conversion runs in a child process: the caller never imports torch.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from groundly.core.manifest import HF_REVISION
from groundly.core.paths import groundly_home

_FILES = (
    ("pytorch_model.bin", "model.safetensors"),
    ("sparse_linear.pt", "sparse_linear.safetensors"),
)


def converted_dir(revision: str = HF_REVISION) -> Path:
    return groundly_home() / "models" / f"bge-m3-{revision}"


def ensure_converted(snapshot: Path, revision: str = HF_REVISION) -> Path:
    """The directory of converted weights, converting on first use. It exists only
    when complete: the child writes a sibling temp dir that is renamed into place."""
    from groundly.llm.embeddings import ModelDownloadError

    final = converted_dir(revision)
    if final.is_dir():
        return final
    final.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=f"{final.name}.tmp-", dir=final.parent))
    print("converting bge-m3 weights to safetensors (one-time) …", file=sys.stderr)
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "groundly.llm.convert", str(snapshot), str(tmp)],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode != 0:
            detail = proc.stderr.strip().splitlines()[-1:] or [f"exit code {proc.returncode}"]
            raise ModelDownloadError(f"converting bge-m3 weights failed: {detail[0]}")
        try:
            os.replace(tmp, final)
        except OSError:
            if not final.is_dir():  # a concurrent run that won the rename is fine
                raise
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return final


def main(snapshot: Path, out: Path) -> None:
    import torch
    from safetensors.torch import save_file

    for src, dst in _FILES:
        state = torch.load(snapshot / src, map_location="cpu", weights_only=True)
        save_file({k: v.to(torch.float16).contiguous() for k, v in state.items()}, out / dst)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
