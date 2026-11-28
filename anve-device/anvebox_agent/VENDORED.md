# Not included in the public copy

On the box this folder holds the ANVE Box team's own implementation of the box link
(identity, signed hello, claim secret, connection), of box voice (`cloud/voice.py`) and of
meeting recording (`cloud/meeting.py`), together with `tools/anvebox_factory.py`,
`tools/talk.py` and `tools/meeting_record.py`, all copied unchanged from the team's
repository. That repository is private, so the files are left
out here. The same goes for the team's page assets in `portal/assets/` (`anve.css`,
`icons.svg`, `led.js`, `fonts/`). Without them `anve_device/boxlink.py`, `boxvoice.py`, `meetings.py`,
the setup page and their tests against the real cloud do not run from this copy;
`tools/boxvoice_test.py` and `tools/meeting_test.py` use stand-ins and need only the
import to resolve. Everything else runs.
