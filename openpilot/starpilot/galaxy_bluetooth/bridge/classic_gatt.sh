#!/bin/sh
# Keeps GATT off classic Bluetooth so iPhones use LE for the app and notifications. Runs as root.
set -eu
here=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SHIM=/run/bt-shim/no_bredr_att.so
DROPIN=/run/systemd/system/bluetooth.service.d/no-bredr-att.conf
CONF="[Service]
Environment=LD_PRELOAD=$SHIM
ExecStart=
ExecStart=/usr/libexec/bluetooth/bluetoothd -C"

shim_loaded() {
  pid=$(systemctl show bluetooth.service --property=MainPID --value)
  [ "${pid:-0}" -gt 0 ] && grep -q no_bredr_att.so "/proc/$pid/maps"
}

apply_shim() {
  mkdir -p "$(dirname "$SHIM")" "$(dirname "$DROPIN")"
  if [ ! -e "$SHIM" ] || [ "$here/no_bredr_att.c" -nt "$SHIM" ]; then
    cc -shared -fPIC -O2 -o "$SHIM.tmp" "$here/no_bredr_att.c" -ldl
    mv "$SHIM.tmp" "$SHIM"
  fi
  if [ "$(cat "$DROPIN" 2>/dev/null)" != "$CONF" ]; then
    printf '%s\n' "$CONF" > "$DROPIN"
    systemctl daemon-reload
  fi
  systemctl is-active -q bluetooth.service || return 0
  shim_loaded && return 0
  systemctl restart bluetooth.service
  for _ in $(seq 1 100); do busctl --system status org.bluez >/dev/null 2>&1 && break; sleep 0.1; done
  for _ in $(seq 1 20); do bluetoothctl power on >/dev/null 2>&1 && break; sleep 0.25; done
}

# bluetoothd lists GATT services in the classic SDP list, which makes iOS use GATT over classic.
strip_records() {
  for _ in $(seq 1 60); do
    grep -q "\"pid\": $1}" /data/galaxy-ble/bridge-ready.json 2>/dev/null && break
    sleep 0.5
  done
  for h in $(sdptool browse local | awk '/Service RecHandle/{h=$3} /"ATT"/{print h}' | sort -u); do
    sdptool del "$h" >/dev/null || true
  done
}

case "${1:-}" in
  apply) apply_shim ;;
  strip) strip_records "${2:-0}" >/dev/null 2>&1 & ;;
  *) echo "usage: $0 apply|strip <bridge pid>" >&2; exit 2 ;;
esac
