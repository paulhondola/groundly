"""A stand-in for `extract_worker --serve`: speaks its line protocol and acts on the input
file's name, so the parent's session handling is tested without loading any model."""

import json
import os
import sys
import time
from pathlib import Path

for line in sys.stdin:
    req = json.loads(line)
    name = Path(req["in"]).name
    if name.startswith("crash"):
        print("Segmentation fault in parser", file=sys.stderr, flush=True)
        os._exit(9)
    if name.startswith("hang"):
        time.sleep(60)
    if name.startswith("unavail"):
        print("rapidocr model missing", file=sys.stderr, flush=True)
        status = "model_unavailable"
    elif name.startswith("toolarge"):
        print("image exceeds 100 megapixels", file=sys.stderr, flush=True)
        status = "input_too_large"
    elif name.startswith("notext"):
        status = "no_text"
    else:
        chunk = {"text": str(os.getpid()), "heading_path": None, "page": 1, "token_count": 1}
        Path(req["out"]).write_text(json.dumps({"pages": 1, "chunks": [chunk]}))
        status = "ok"
    sys.stdout.write(json.dumps({"status": status}) + "\n")
    sys.stdout.flush()
    if name.startswith("bye"):
        os._exit(0)
    if name.startswith("die"):
        os._exit(9)
