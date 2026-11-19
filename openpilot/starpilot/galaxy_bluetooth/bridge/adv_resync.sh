#!/bin/sh
# Keeps comma advertising while a phone is connected over LE, so more phones can find it. Runs as root.
# Kernel 4.9 won't advertise during any LE link, but the controller can; drive it directly when links change.
hci() { hcitool cmd 0x08 "$@" >/dev/null 2>&1 || true; }

advertise() {
  hci 0x000a 00
  hci 0x0006 a0 00 f0 00 00 00 00 00 00 00 00 00 00 07 00
  hci 0x000a 01
}

watch_links() {
  known=none
  trap 'hci 0x000a 00; exit 0' TERM INT
  while kill -0 "$1" 2>/dev/null; do
    links=$(hcitool con 2>/dev/null | awk '$2 == "LE" {print $5}' | sort | tr '\n' ' ')
    [ "$links" != "$known" ] && advertise
    known=$links
    sleep 2
  done
  hci 0x000a 00
}

watch_links "${1:?usage: $0 <bridge pid>}" >/dev/null 2>&1 &
