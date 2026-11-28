"""The factory step, development version: make a box's identity, and its card.

    uv run --project device python tools/factory/factory.py new \
        --out <identity.json> --record <record.json>

Writes two files:
- the identity file: SECRET. It belongs on the box only, readable by root
  (/etc/anvebox/identity.json on a real box). Never commit or share it.
- the registration record: PUBLIC. Device ID, public key and a hash of the
  claim secret. Give it to the cloud:
      uv run --project cloud python -m anvebox_cloud register-box <record.json>

    uv run --project device python tools/factory/factory.py card \
        --identity <identity.json> --cloud <https://cloud> --out cards/<name>.svg

Draws the card that goes in the box, credit-card size: the setup Wi-Fi's name
and password, and a QR code with the box's first claim link (decision D14: the
setup page's link comes first, the card is the backup). The card is SECRET too:
print it, then delete the file. `cards/` is ignored by git.

The simulator makes and registers its own pretend identity against a
development cloud (device/.sim/identity.json), so it can have a card as well.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
from xml.sax.saxutils import escape

from anvebox_agent.identity import Identity, load_or_create, new_identity, save

ROOT = Path(__file__).resolve().parents[2]
FONTS = ROOT / "device" / "portal" / "assets" / "fonts"

# The ANVE mark, as on the setup page.
MARK = "M16 7 L10 25 h3.4 l1.2-3.8 h6.8 L22.6 25 H26 L20 7 Z M15.6 18.4 L18 10.8 20.4 18.4 Z"

# ANVE colours, from :root in device/portal/assets/anve.css. Print is light.
INK, INK2, RULE = "#1D1D1F", "#505055", "#DADADE"


def claim_link(cloud_url: str, identity: Identity) -> str:
    """The card's link: the cloud's claim page, with the first claim secret
    after the #, so it is never sent to a server or kept in its logs."""
    return f"{cloud_url.rstrip('/')}/claim#{identity.device_id}.{identity.claim_secret.reveal()}"


def qr_path(link: str) -> tuple[str, int]:
    """The QR code as one SVG path in module units, and its size in modules."""
    import segno  # a factory tool only, so not needed on the box

    qr = segno.make(link, error="m", micro=False)
    rows = list(qr.matrix_iter(scale=1, border=0))
    parts = []
    for y, row in enumerate(rows):
        x = 0
        while x < len(row):
            if row[x]:
                start = x
                while x < len(row) and row[x]:
                    x += 1
                parts.append(f"M{start} {y}h{x - start}v1h-{x - start}z")
            else:
                x += 1
    return "".join(parts), len(rows)


def _font(name: str, family: str, weight: str) -> str:
    data = base64.b64encode((FONTS / name).read_bytes()).decode("ascii")
    return (
        f'@font-face{{font-family:"{family}";font-weight:{weight};'
        f'src:url(data:font/woff2;base64,{data}) format("woff2")}}'
    )


def card_svg(identity: Identity, cloud_url: str) -> str:
    """85.6 x 54 mm. One unit is 0.1 mm; 1 pt is about 3.53 units."""
    path, modules = qr_path(claim_link(cloud_url, identity))
    qr_size = 236  # 23.6 mm
    unit = qr_size / modules
    qr_x, qr_y = 856 - 48 - qr_size, 120
    password = escape(identity.setup_password.reveal())
    ssid = escape(identity.setup_ssid)
    style = (
        _font("plex-sans-latin.woff2", "ANVE Sans", "100 700")
        + _font("plex-mono-latin-400.woff2", "ANVE Mono", "400")
        + "text{font-family:'ANVE Sans','IBM Plex Sans',sans-serif;fill:" + INK + "}"
        + ".mono{font-family:'ANVE Mono','IBM Plex Mono',monospace}"
        + ".t{font-size:40px;font-weight:600}"
        + ".b{font-size:26px}"
        + ".s{font-size:22px;fill:" + INK2 + "}"
        + ".n{font-size:26px;font-weight:600;fill:" + INK2 + "}"
        + ".k{font-size:24px;fill:" + INK2 + "}"
        + ".v{font-size:31px}"
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="85.6mm" height="54mm" viewBox="0 0 856 540">
<title>ANVE Box {identity.device_id} setup card</title>
<style>{style}</style>
<rect width="856" height="540" rx="32" fill="#FFFFFF"/>
<rect x="2" y="2" width="852" height="536" rx="30" fill="none" stroke="{RULE}" stroke-width="2"/>
<path fill="{INK}" fill-rule="evenodd" transform="translate(35.2 38) scale(1.6) translate(-2 0)" d="{MARK}"/>
<text class="b" x="86" y="78" font-weight="600">ANVE Box</text>
<text class="t" x="48" y="152">Set up your box</text>
<text class="n" x="48" y="206">1</text>
<text class="b" x="84" y="206">Plug it in. When the light pulses</text>
<text class="b" x="84" y="240">slowly, join this Wi-Fi on your phone:</text>
<text class="k" x="84" y="290">Wi-Fi</text>
<text class="v mono" x="216" y="290">{ssid}</text>
<text class="k" x="84" y="336">Password</text>
<text class="v mono" x="216" y="336">{password}</text>
<text class="n" x="48" y="394">2</text>
<text class="b" x="84" y="394">A page opens. Choose your Wi-Fi, then</text>
<text class="b" x="84" y="428">open the link it gives you to finish.</text>
<line x1="48" y1="462" x2="808" y2="462" stroke="{RULE}" stroke-width="2"/>
<text class="s mono" x="48" y="500">Box {escape(identity.device_id)}</text>
<text class="s" x="808" y="500" text-anchor="end">Keep this card. It sets up your box.</text>
<path fill="{INK}" shape-rendering="crispEdges" transform="translate({qr_x} {qr_y}) scale({unit:.4f})" d="{path}"/>
<text class="s" x="{qr_x + qr_size / 2:.0f}" y="{qr_y + qr_size + 40}" text-anchor="middle">No link? Scan this.</text>
<text class="s" x="{qr_x + qr_size / 2:.0f}" y="{qr_y + qr_size + 68}" text-anchor="middle">It works once.</text>
</svg>
"""


def _write_secret(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description="ANVE Box factory step (development)")
    commands = parser.add_subparsers(dest="command", required=True)
    new = commands.add_parser("new", help="make a new box identity")
    new.add_argument("--out", type=Path, required=True, help="where to write the secret identity file")
    new.add_argument("--record", type=Path, required=True, help="where to write the public registration record")
    card = commands.add_parser("card", help="draw the card that goes in the box (SVG)")
    card.add_argument("--identity", type=Path, required=True, help="the box's identity file")
    card.add_argument("--cloud", required=True, help="the cloud's address, such as https://cloud.example")
    card.add_argument("--out", type=Path, required=True, help="where to write the card, such as cards/box.svg")
    args = parser.parse_args()

    if args.command == "card":
        if not args.identity.exists():
            parser.error(f"{args.identity} doesn't exist")
        if not args.cloud.startswith(("https://", "http://")):
            parser.error("--cloud must start with https:// (or http:// for a development cloud)")
        identity = load_or_create(args.identity)
        _write_secret(args.out, card_svg(identity, args.cloud))
        print(f"Card for box {identity.device_id}: {args.out}")
        print("  secret: it has the setup password and the first claim link. Print it, then delete the file.")
        return

    if args.out.exists():
        parser.error(f"{args.out} already exists; a box keeps its identity for life")
    identity = new_identity()
    save(identity, args.out)
    args.record.write_text(json.dumps(identity.registration_record(), indent=2), encoding="utf-8")
    print(f"Box {identity.device_id}")
    print(f"  setup Wi-Fi:  {identity.setup_ssid}")
    print(f"  identity:     {args.out} (secret: keep it on the box)")
    print(f"  record:       {args.record} (public: give it to the cloud)")


if __name__ == "__main__":
    main()
