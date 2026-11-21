# Not included in the public copy

On the box this folder holds the ANVE Box team's own implementation of the box link
(identity, signed hello, claim secret, connection) and `tools/anvebox_factory.py`, copied
unchanged from the team's repository. That repository is private, so the files are left
out here. The same goes for the team's page assets in `portal/assets/` (`anve.css`,
`icons.svg`, `led.js`, `fonts/`). Without them `anve_device/boxlink.py`, the setup page
and their tests do not run from this copy; everything else does.
