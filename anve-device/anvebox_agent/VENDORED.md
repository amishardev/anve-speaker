# Copied from the ANVE Box repo, unchanged

These files are exact copies from `github.com/ANVEAI/anve-box` at commit `0b56043` (27 Nov 2026; before that `f2c3c4e`, first copied at `d0de5c0`)
(`device/anvebox_agent/`): `__init__.py`, `identity.py`, `redact.py`, `model.py`,
`lights.py`, `cloud/__init__.py`, `cloud/base.py`, `cloud/claim.py`, `cloud/link.py`, `cloud/voice.py` (box voice, part C), `cloud/meeting.py` (meetings, part D).
`tools/anvebox_factory.py` is `tools/factory/factory.py` and `tools/talk.py` is
`tools/box-voice/talk.py`, and `tools/meeting_record.py` is `tools/box-meeting/record.py`,
from the same repo.

They are the team's own implementation of the box link (docs/box-cloud-agreement.md,
part A): the identity, the signed hello, the claim secret and the connection. Do not
edit them here. To take a newer version, copy the files again and update the commit
above. The speaker's own side of the link is `anve_device/boxlink.py`.
