"""Stable identity shared by the BLE and local HTTP telemetry transports."""
import socket


def telemetry_device_id(params) -> str:
  # HardwareSerial is initialized by manager and does not change with accounts.
  serial = params.get("HardwareSerial")
  if isinstance(serial, bytes):
    serial = serial.decode("utf-8", errors="replace")
  if serial:
    return str(serial)
  return socket.gethostname().split(".", 1)[0] or "comma"
