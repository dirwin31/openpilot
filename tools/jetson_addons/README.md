# Jetson add-ons

Puts V-ASM and the sign-vision detectors on a Jetson running a jetlink-server with `--addons`:

```bash
tools/jetson_addons/install.sh user@jetson
```

It copies the models and `addons.json` to `/opt/addons`, names the file in `/etc/jetlink/addons.env`, and restarts the server. Turn each add-on on in Galaxy's JetLink page; it applies within seconds. The models must be the files the comma ships, as the wire names a model by its sha256.
