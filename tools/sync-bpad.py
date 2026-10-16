#!/usr/bin/env python3
"""Embed the Base Plate & Anchor Bolt Designer into the canopy app.

Usage:
    python3 tools/sync-bpad.py PATH/TO/all-structural [--file BasePlateAnchorDesigner.html]

Reads the designer from a checkout of the mlococo88/All-Structural repo, appends
tools/bpad-embed-adapter.js (the API the canopy page drives), gzips it, and writes
it into index.html between the BPAD-EMBED markers as a base64 block, stamped with
the source commit. The designer itself is not modified otherwise, so every
improvement made to it in All-Structural is picked up by re-running this script.
"""
import argparse, base64, gzip, hashlib, io, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"
ADAPTER = ROOT / "tools" / "bpad-embed-adapter.js"
START, END = "<!--BPAD-EMBED-START-->", "<!--BPAD-EMBED-END-->"

# the designer functions/globals the adapter relies on; a sync stops if any is gone
REQUIRED = ["function autosave(", "function loadAutosave(", "let S = defaultState()", "let CC=",
            "function mergeState(", "function defaultState(", "function computeAll(", "function allChecks(",
            "function dcrMatrix(", "function validateInputs(", "let VALIDATION=", "function refreshAll(",
            "function openProfisExport(", "function openPrintSettings(", "</body>"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", help="path to a checkout of mlococo88/All-Structural")
    ap.add_argument("--file", default="BasePlateAnchorDesigner.html")
    a = ap.parse_args()
    repo = pathlib.Path(a.repo)
    src_path = repo / a.file
    src = src_path.read_text(encoding="utf-8")
    missing = [r for r in REQUIRED if r not in src]
    if missing:
        sys.exit("The designer no longer has: " + ", ".join(missing) + " — update tools/bpad-embed-adapter.js first.")
    try:
        commit = subprocess.check_output(["git", "-C", str(repo), "log", "-1", "--format=%h", "--", a.file], text=True).strip()
        dirty = subprocess.call(["git", "-C", str(repo), "diff", "--quiet", "--", a.file]) != 0
        if dirty:
            commit += "+local"
    except Exception:
        commit = "unknown"
    adapter = ADAPTER.read_text(encoding="utf-8").replace("__BPAD_COMMIT__", commit)
    i = src.rfind("</body>")
    html = src[:i] + "<script>\n" + adapter + "\n</script>\n" + src[i:]
    raw = html.encode("utf-8")
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=9, mtime=0) as gz:   # mtime 0: same input, same bytes
        gz.write(raw)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    sha = hashlib.sha256(src.encode("utf-8")).hexdigest()[:12]
    block = (f'{START}\n<script type="application/octet-stream" id="bpadGz" data-file="{a.file}" '
             f'data-commit="{commit}" data-sha="{sha}" data-bytes="{len(raw)}">\n'
             + "\n".join(b64[k:k + 4000] for k in range(0, len(b64), 4000)) + f"\n</script>\n{END}")
    index = INDEX.read_text(encoding="utf-8")
    if START in index:
        index = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda m: block, index, flags=re.S)
    else:
        j = index.rfind("</body>")
        index = index[:j] + block + "\n" + index[j:]
    INDEX.write_text(index, encoding="utf-8")
    print(f"Embedded {a.file} @ {commit} (sha {sha}): {len(raw):,} bytes -> {len(b64):,} base64 chars")


if __name__ == "__main__":
    main()
