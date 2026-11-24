#!/usr/bin/env python3
"""Write app-index.json for a built wheel (run after `python -m build`).
Usage: python tools/make_app_index.py dist/cytoflowgui-0.2.0-py3-none-any.whl \
           --base-url https://github.com/ORG/cytoflowgui/releases/download/v0.2.0/ [--notes "..."]
Attach BOTH the wheel and app-index.json to the GitHub release."""
import argparse
import hashlib
import json
from pathlib import Path

ap = argparse.ArgumentParser()
ap.add_argument("wheel")
ap.add_argument("--base-url", required=True)
ap.add_argument("--notes", default="")
ap.add_argument("--min-python", default="3.11")
a = ap.parse_args()

w = Path(a.wheel)
index = {"schema": 1, "version": w.name.split("-")[1],
         "url": a.base_url.rstrip("/") + "/" + w.name,
         "sha256": hashlib.sha256(w.read_bytes()).hexdigest(),
         "notes": a.notes, "min_python": a.min_python}
out = w.with_name("app-index.json")
out.write_text(json.dumps(index, indent=2), encoding="utf-8")
print("wrote", out)
