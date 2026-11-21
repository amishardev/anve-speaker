#!/usr/bin/env python3
"""The setup page's encryption against the box's own: do the two understand each other?

It runs the very files a browser loads (portal/assets/seal/, through tools/portal_seal.mjs
in Node) against anve_device/pairing.py, in both directions. It needs Node, so it runs on
the laptop; the Pi has none and needs none. The rest of the page's behaviour (what the
box serves, refuses and stores) is in tools/provision_test.py, which runs on the Pi.

    python tools/portal_test.py
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from anve_device import pairing  # noqa: E402

results = []


def check(name, ok, note=""):
    results.append(bool(ok))
    print(f"  {'ok  ' if ok else 'FAIL'} {name}{' - ' + str(note) if note and not ok else ''}")


def node(*args, stdin):
    return subprocess.run(["node", str(ROOT / "tools" / "portal_seal.mjs"), *args], input=json.dumps(stdin),
                          capture_output=True, text=True, encoding="utf-8")


def main() -> int:
    if not shutil.which("node"):
        print("Node is not installed here: run this on the laptop.")
        return 0
    seal_dir = ROOT / "portal" / "assets" / "seal"
    check("the page's encryption files are built", (seal_dir / "seal.mjs").is_file() and (seal_dir / "curves" / "ed25519.mjs").is_file())
    box = pairing.Box()
    details = {"wifi": {"ssid": "Café हिन्दी \U0001f4f6", "password": "correct horse \\ \" battery", "hidden": False},
               "name": "Desk", "room": "", "portal": {"username": "student1", "password": "pa ss"}}
    r = node("seal", pairing.b64(box.public), stdin=details)
    check("the page seals", r.returncode == 0, r.stderr[:300])
    out = json.loads(r.stdout) if r.returncode == 0 else {"message": {}, "key": ""}
    try:
        got, key = box.open(out["message"])
    except pairing.BadMessage as e:
        got, key = {"error": str(e)}, b""
    check("the box opens what the page sealed, every character intact", got == details, got)
    check("both sides arrived at the same key", pairing.b64(key) == out["key"])
    check("what crosses the air shows none of it", not any(x in json.dumps(out["message"]) for x in ("correct horse", "student1", "pa ss", "Desk")))
    r2 = node("seal", pairing.b64(box.public), stdin=details)
    check("sealing the same thing twice never looks the same", r2.returncode == 0 and json.loads(r2.stdout)["message"] != out["message"])
    reply = pairing.Box.reply(key, {"ok": True, "claimLink": "https://cloud.example/claim#ABCDEFGHJK.SECRETSECRETSECRET12", "ticket": "t" * 32, "hold": 120})
    r = node("open", out["key"], stdin=reply)
    check("the page opens the box's sealed answer", r.returncode == 0 and json.loads(r.stdout)["claimLink"].endswith("SECRETSECRETSECRET12"), r.stderr[:300])
    bad = dict(reply, c=reply["c"][:-4] + ("AAAA" if not reply["c"].endswith("AAAA") else "BBBB"))
    check("the page refuses an answer that was tampered with", node("open", out["key"], stdin=bad).returncode != 0)
    other = pairing.Box.reply(b"\x07" * 32, {"ok": True})
    check("and one sealed by somebody else", node("open", out["key"], stdin=other).returncode != 0)
    msg, k2 = pairing.seal(box.public, {"from": "the app's reference code"})
    check("the app's sealing and the page's are the same thing (the box cannot tell them apart)", box.open(msg)[0] == {"from": "the app's reference code"})
    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
