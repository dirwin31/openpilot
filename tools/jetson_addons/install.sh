#!/usr/bin/env bash
# Puts StarPilot's Jetson add-ons on a Jetson that runs jetlink-server:
# the models and addons.json in /opt/addons, named in /etc/jetlink/addons.env.
#   tools/jetson_addons/install.sh user@jetson
set -euo pipefail

host=${1:?usage: install.sh user@jetson}
here=$(cd "$(dirname "$0")" && pwd)
root=$(cd "$here/../.." && pwd)
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

cp "$here/addons.json" \
  "$root/starpilot/assets/vision_models/v_asm_model.onnx" \
  "$root/openpilot/starpilot/speed_limits/vision/assets/traffic_signs_us_detector.onnx" \
  "$root/openpilot/starpilot/speed_limits/vision/assets/traffic_signs_us_stop_detector.onnx" \
  "$stage/"
scp -q "$stage"/* "$host:/tmp/"
# shellcheck disable=SC2029
ssh -t "$host" "/opt/jetlink/current/bin/jetlink-server serve --help | grep -q -- --addons || { echo 'This Jetlink has no add-ons yet: update it first.'; exit 1; } \
  && sudo mkdir -p /opt/addons && cd /tmp && sudo mv addons.json $(cd "$stage" && echo *.onnx) /opt/addons/ \
  && echo 'JETLINK_ADDONS=\"--addons /opt/addons/addons.json\"' | sudo tee /etc/jetlink/addons.env >/dev/null \
  && sudo systemctl restart jetlink-server && echo 'Add-ons installed; jetlink logs shows them build.'"
