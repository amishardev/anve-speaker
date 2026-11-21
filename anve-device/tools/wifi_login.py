#!/usr/bin/env python3
"""Keep the Pi signed in to a Wi-Fi that has a FortiGate sign-in page (campus network).

    tools/wifi_login.py            sign in if the sign-in page is in the way (quiet if online)
    tools/wifi_login.py --status   only say what the network looks like

How it knows: a plain HTTP request that should return "204 No Content" comes back as a
redirect to https://<gateway>:1000/fgtauth?<token> when the firewall wants a login.
It then fetches that page, reads the hidden `magic` token, and posts the ID and password
to the same gateway over HTTPS (certificate checked), exactly as the browser form does.

The ID and password are NOT in this file or in the project. They are read from a
root-only file, /etc/anve-device/wifi-login (line 1: ID, line 2: password), which the
owner fills in through the setup page. Under systemd they arrive as a credential.
Nothing secret is printed or logged.

Run every 45 s by anve-wifi-login.timer, so an expired login is renewed within a minute.
"""
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROBE = "http://connectivitycheck.gstatic.com/generate_204"
CRED = "/etc/anve-device/wifi-login"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def probe() -> str | None:
    """None when the internet is open; otherwise the sign-in page's address ('' if the
    network is in the way but no sign-in address could be found)."""
    try:
        r = urllib.request.build_opener(NoRedirect).open(PROBE, timeout=8)
        body = r.read(4000).decode("utf-8", "replace")
        if r.status == 204:
            return None
    except urllib.error.HTTPError as e:
        body = (e.headers.get("Location") or "") + e.read(4000).decode("utf-8", "replace")
    except OSError:
        return ""
    m = re.search(r"https://[\w.\-]+(?::\d+)?/fgtauth\?[0-9a-fA-F]+", body)
    return m.group(0) if m else ""


def credentials() -> tuple[str, str] | None:
    d = os.environ.get("CREDENTIALS_DIRECTORY")
    for p in ([Path(d) / "wifi"] if d else []) + [Path(CRED)]:
        try:
            lines = p.read_text().splitlines()
        except OSError:
            continue
        if len(lines) >= 2 and lines[0].strip() and lines[1]:
            return lines[0].strip(), lines[1]
    return None


def login(url: str, user: str, password: str) -> str:
    """'' on success, else a short reason (never containing the ID or password)."""
    ctx = ssl.create_default_context()
    try:
        page = urllib.request.urlopen(url, timeout=10, context=ctx).read(20000).decode("utf-8", "replace")
    except OSError as e:
        return f"the sign-in page could not be opened ({type(e).__name__})"
    magic = re.search(r'name="magic"\s+value="([^"]+)"', page)
    redir = re.search(r'name="4Tredir"\s+value="([^"]*)"', page)
    if not magic:
        return "the sign-in page has no token (it may have changed)"
    base = urllib.parse.urlsplit(url)
    data = urllib.parse.urlencode({"4Tredir": redir.group(1) if redir else PROBE,
                                   "magic": magic.group(1), "username": user,
                                   "password": password}).encode()
    try:
        out = urllib.request.urlopen(f"{base.scheme}://{base.netloc}/", data=data, timeout=12,
                                     context=ctx).read(20000).decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        out = e.read(4000).decode("utf-8", "replace")
    except OSError as e:
        return f"the sign-in was not answered ({type(e).__name__})"
    if re.search(r"authentication failed|invalid|incorrect|denied", out, re.I) and "keepalive" not in out:
        return "the network refused the ID or password"
    if re.search(r"over\s*limit|concurrent|too many", out, re.I) and "keepalive" not in out:
        return "this ID is signed in on too many devices"
    return ""


def say(text: str, level: int = 5):
    """A line for the journal. systemd reads "<5>" as "notice" and "<4>" as "warning"; the
    unit only keeps those (LogLevelMax=notice), so the routine "online" line stays out."""
    print(text if sys.stdout.isatty() else f"<{level}>{text}")


def main() -> int:
    status_only = "--status" in sys.argv
    url = probe()
    if url is None:
        if status_only:
            print("online: no sign-in page in the way")  # routine: not kept
        return 0
    if status_only:
        say("the Wi-Fi sign-in page is in the way" if url else "no internet, and no sign-in page found")
        return 1
    if not url:
        say("no internet, and no sign-in page found: nothing to sign in to", 4)
        return 1
    cred = credentials()
    if not cred:
        say("the Wi-Fi wants a sign-in, but no ID and password are stored (use the setup page)", 4)
        return 2
    why = login(url, *cred)
    if why:
        say(f"Wi-Fi sign-in failed: {why}", 4)
        return 3
    if probe() is None:
        say("signed in to the Wi-Fi")
        return 0
    say("the sign-in was sent, but the internet is still closed", 4)
    return 4


if __name__ == "__main__":
    sys.exit(main())
