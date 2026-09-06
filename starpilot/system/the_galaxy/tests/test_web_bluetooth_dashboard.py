import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[4]
UI_ROOT = REPO_ROOT / "starpilot/system/the_galaxy/assets/mobile"


def _read(relative: str) -> str:
  return (UI_ROOT / relative).read_text(encoding="utf-8")


def test_dashboard_route_navigation_and_styles_are_wired():
  app = _read("js/app.js")
  store = _read("js/store.js")
  shell = _read("js/components/AppShell.js")
  tools = _read("js/views/Tools.js")
  index = _read("index.html")

  assert 'import { Dashboard } from "./views/Dashboard.js"' in app
  assert '"/dashboard": Dashboard' in app
  assert '"/dashboard"' in store
  assert 'name: "Dashboard", link: "/dashboard"' in shell
  assert 'name: "Dashboard", link: "/dashboard"' in tools
  assert "fullScreenDashboard" in shell
  assert 'matchMedia("(orientation: landscape)")' in shell
  assert 'href="/assets/mobile/css/dashboard.css"' in index


def test_dashboard_has_security_gate_controls_and_complete_layouts():
  dashboard = _read("js/views/Dashboard.js")
  assert "window.isSecureContext" in dashboard
  assert "navigator.bluetooth" in dashboard
  assert "Chrome on Android required" in dashboard
  assert 'target.port = "8443"' in dashboard
  assert "reconnectRemembered" in dashboard
  assert "Pair the device first" in dashboard
  assert "dash-landscape" in dashboard
  assert "dash-portrait" in dashboard
  assert "STEER DELAY" in dashboard
  assert "LONGITUDINAL %" in dashboard
  assert "LEAD VEHICLE" in dashboard
  assert "CURVE TARGET" in dashboard
  assert "STOP SIGNAL" in dashboard
  assert "demo" not in dashboard.lower()


def test_ble_client_uses_companion_service_and_coalesces_updates():
  client = _read("js/ble/live_ble.js")
  assert "9b6d1000-6f7a-4a5b-8c3d-2e1f0a9b8c7d" in client
  assert "navigator.bluetooth.requestDevice" in client
  assert "navigator.bluetooth.getDevices" in client
  assert "startNotifications" in client
  assert "characteristicvaluechanged" in client
  assert 'command("get_live_metadata")' in client
  assert "metadataRevision" in client and "alertID" in client
  assert "200 - elapsed" in client
  assert "gattserverdisconnected" in client
  assert "PAIRING_MESSAGE" in client
  assert "demo" not in client.lower()


@pytest.mark.skipif(shutil.which("node") is None, reason="no node.js runtime available")
def test_dashboard_javascript_modules_parse(tmp_path):
  for relative in ("js/ble/live_frames.js", "js/ble/live_ble.js", "js/views/Dashboard.js"):
    source = UI_ROOT / relative
    target = tmp_path / (source.stem + ".mjs")
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    result = subprocess.run([shutil.which("node"), "--check", str(target)], capture_output=True, text=True)
    assert result.returncode == 0, f"{relative} failed to parse:\n{result.stderr}"
