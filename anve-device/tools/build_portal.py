#!/usr/bin/env python3
"""Rebuild portal/assets/seal/ (the setup page's encryption) from portal/src/seal.mjs.

Run on the laptop, not on the Pi. It downloads nothing: it copies the "noble" libraries
the phone app already has (anve-app/node_modules/@noble, MIT licence) file by file, and
only changes the import lines so a browser can load them as ordinary modules
('@noble/hashes/utils.js' becomes a relative path). The libraries' code is not touched,
except that each file is made smaller with the app's own copy of terser when it is there.
The result is checked in, so the Pi never builds anything, and tools/portal_test.py
proves it against the box's own code.

    python tools/build_portal.py [path/to/anve-app] [--plain]     (--plain: do not shrink)
"""
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
args = [a for a in sys.argv[1:] if not a.startswith("--")]
APP = Path(args[0]).resolve() if args else HERE.parent / "anve-app"
NOBLE = APP / "node_modules" / "@noble"
SRC = HERE / "portal" / "src" / "seal.mjs"
OUT = HERE / "portal" / "assets" / "seal"
SPEC = re.compile(r"""(\bfrom\s*|\bimport\s*)(["'])([^"']+)\2""")


def target(spec: str, source: Path) -> Path:
    """The file an import line means."""
    if spec.startswith("."):
        return (source.parent / spec).resolve()
    if spec.startswith("@noble/"):
        return (NOBLE / spec[len("@noble/"):]).resolve()
    raise SystemExit(f"{source}: an import this script does not know: {spec}")


def out_path(source: Path) -> Path:
    if source == SRC.resolve():
        return OUT / "seal.mjs"
    rel = source.relative_to(NOBLE.resolve())
    return (OUT / rel).with_suffix(".mjs")


def main() -> int:
    if not (NOBLE / "ciphers").is_dir():
        raise SystemExit(f"no noble libraries in {NOBLE} (run npm install in the app folder)")
    if OUT.exists():
        shutil.rmtree(OUT)
    todo, done = [SRC.resolve()], {}
    while todo:
        source = todo.pop()
        if source in done:
            continue
        if not source.is_file():
            raise SystemExit(f"missing: {source}")
        dest = out_path(source)
        lines = []
        for line in source.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith(("*", "/*", "//")):          # a comment: leave it be
                lines.append(line)
                continue

            def swap(m, source=source, dest=dest):
                t = target(m.group(3), source)
                todo.append(t)
                rel = os.path.relpath(out_path(t), dest.parent).replace(os.sep, "/")
                return f"{m.group(1)}{m.group(2)}{rel if rel.startswith('.') else './' + rel}{m.group(2)}"
            lines.append(SPEC.sub(swap, line))
        done[source] = (dest, "\n".join(lines) + "\n")
    for dest, text in done.values():
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8", newline="\n")
    plain = sum(len(t) for _, t in done.values())

    terser = APP / "node_modules" / "terser" / "bin" / "terser"
    small = None
    if "--plain" not in sys.argv and terser.is_file() and shutil.which("node"):
        for dest, _ in done.values():
            r = subprocess.run(["node", str(terser), str(dest), "--module", "--compress", "--mangle",
                                "--comments", "false", "--output", str(dest)], capture_output=True, text=True)
            if r.returncode:
                raise SystemExit(f"terser failed on {dest.name}: {r.stderr[:300]}")
        small = sum(d.stat().st_size for d, _ in done.values())

    versions = {p: json.loads((NOBLE / p / "package.json").read_text(encoding="utf-8"))["version"]
                for p in ("ciphers", "curves", "hashes")}
    licence = (NOBLE / "curves" / "LICENSE").read_text(encoding="utf-8")
    (OUT / "NOTICE.txt").write_text(
        "The files in this folder, other than seal.mjs, are the noble cryptography libraries\n"
        "by Paul Miller (paulmillr.com), copied by tools/build_portal.py from the phone app's\n"
        "node_modules: " + ", ".join(f"@noble/{p} {v}" for p, v in versions.items()) + ".\n"
        "Only their import lines were changed (and the files made smaller). Do not edit them\n"
        "here: run the build again.\n\n" + licence, encoding="utf-8", newline="\n")
    print(f"portal/assets/seal: {len(done)} files, {plain} bytes"
          + (f", {small} after shrinking" if small else " (not shrunk)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
