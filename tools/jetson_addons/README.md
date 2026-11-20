# Jetson add-ons

V-ASM and sign vision on the Jetson, through jetlink-server's add-ons
(jetlink_repo docs/addons.md upstream). The comma side is
`openpilot/starpilot/models/jetlink_addons.py`, switched on per add-on in
Galaxy's JetLink page; the comma runs each model on its own CPU whenever the
Jetson does not answer.

On the Jetson, jetlink-server needs a release with `--addons`. Then:

```bash
tools/jetson_addons/install.sh user@jetson
```

It copies the three models and `addons.json` to `/opt/addons`, names the file
in `/etc/jetlink/addons.env` (which Jetlink's installer never writes), and
restarts the server. `jetlink logs` shows each model build once, then
`claimed its interface` after the comma reboots with the add-on on.

The class triples in `addons.json` match `jetlink_addons.py`. The models must
be the same files the comma ships: the wire names a model by its sha256.

Ported from Peter Nguyen's Jetson work (trung791997/openpilot).
