import ast

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
SETTINGS_PATH = REPO_ROOT / "starpilot/system/the_galaxy/assets/components/settings.js"
ROUTER_PATH = REPO_ROOT / "starpilot/system/the_galaxy/assets/components/router.js"
INDEX_PATH = REPO_ROOT / "starpilot/system/the_galaxy/templates/index.html"
BLUETOOTH_PATH = REPO_ROOT / "starpilot/system/the_galaxy/assets/components/tools/bluetooth.js"
CONTROLLERS_PATH = REPO_ROOT / "starpilot/system/the_galaxy/assets/components/tools/wheel_controls.js"
SIDEBAR_PATH = REPO_ROOT / "starpilot/system/the_galaxy/assets/components/sidebar.js"
LIVE_STANDALONE_HTML_PATH = REPO_ROOT / "starpilot/system/the_galaxy/templates/live_link.html"
LIVE_STANDALONE_JS_PATH = REPO_ROOT / "starpilot/system/the_galaxy/assets/components/tools/live_link_standalone.js"
LIVE_STANDALONE_CSS_PATH = REPO_ROOT / "starpilot/system/the_galaxy/assets/components/tools/live_link_standalone.css"
LIVE_COMPATIBILITY_PATH = REPO_ROOT / "starpilot/system/the_galaxy/assets/components/tools/live_link.js"
SERVICE_WORKER_PATH = REPO_ROOT / "starpilot/system/the_galaxy/assets/service-worker.js"
GALAXY_SERVER_PATH = REPO_ROOT / "starpilot/system/the_galaxy/the_galaxy.py"


def test_settings_does_not_create_a_second_router_module():
  source = SETTINGS_PATH.read_text(encoding="utf-8")

  assert "/assets/components/router.js" not in source
  assert "window.__theGalaxyNavigate" in source


def test_router_and_settings_cache_bust_is_consistent():
  router = ROUTER_PATH.read_text(encoding="utf-8")
  index = INDEX_PATH.read_text(encoding="utf-8")

  assert "/assets/components/settings.js?v=router-cycle-fix-4" in router
  assert "/assets/components/router.js?v=router-public-path-6" in index
  assert "/assets/components/tools/bluetooth.js?v=bluetooth-phone-connection-5" in router
  assert "/assets/components/tools/bluetooth.css?v=bluetooth-phone-connection-3" in index


def test_public_galaxy_bootstrap_keeps_the_device_path_for_module_imports():
  index = INDEX_PATH.read_text(encoding="utf-8")

  assert 'window.location.hostname === "galaxy.firestar.link"' in index
  assert '/^[A-Za-z0-9]{16}$/.test(firstPathSegment)' in index
  assert 'window.__theGalaxyBasePath = galaxyBasePath' in index
  assert 'galaxyImportMap.type = "importmap"' in index
  assert 'JSON.stringify({ imports: { "/assets/": `${galaxyBasePath}/assets/` } })' in index
  assert '`${window.__theGalaxyBasePath}/assets/components/router.js?v=router-public-path-6`' in index


def test_bluetooth_actions_use_reactive_disabled_bindings():
  source = BLUETOOTH_PATH.read_text(encoding="utf-8")

  assert 'disabled="${pairingDisabled}"' not in source
  assert 'disabled="${disabled}"' not in source
  assert 'disabled="${() => !state.offroad || !!state.busy}"' in source
  assert "bluetoothAddress" not in source
  assert 'address: audioSelected() ? "" : device.address' in source
  assert 'audioSelected() ? "Stop Using for Audio" : "Use for Audio"' in source
  assert 'request("test_audio", { address: device.address })' in source
  assert "startAudioTestCountdown" in source
  assert "The test sound is sent at NOW" in source
  assert 'deviceSection("My Devices"' in source
  assert 'deviceSection("Available Devices"' in source
  assert "bluetoothForgetButton" in source
  assert "bi-trash3" in source
  assert "state.pairingAddress" in source
  assert "state.enabled && !state.companionDevices.length" in source
  assert "state.operationError || state.statusError" in source
  assert "<h3>Phone connection</h3>" in source
  assert "Follow its beacio install and website-permission checks" in source
  assert "Tap Connect over Bluetooth and choose StarPilot" in source
  assert "no confirmation slider is shown" in source
  assert "isCompanionDevice(device) && !device.connected" in source
  assert "Reconnect from phone" in source


def test_bluetooth_device_list_reads_state_inside_reactive_blocks():
  source = BLUETOOTH_PATH.read_text(encoding="utf-8")

  assert "deviceSection(title, icon, selectDevices, emptyText)" in source
  assert "${() => selectDevices().length}" in source
  assert "const devices = selectDevices()" in source
  assert 'deviceSection("My Devices", "bi-check2-circle", knownDevices, () => "No saved devices yet.")' in source
  assert 'deviceSection("Available Devices", "bi-radar", availableDevices,' in source
  assert ", knownDevices()," not in source
  assert ", availableDevices()," not in source


def test_controller_test_mode_has_explicit_start_and_stop():
  source = CONTROLLERS_PATH.read_text(encoding="utf-8")

  assert 'state.testing ? "test-stop" : "test"' in source
  assert 'state.lastTested.mapped ? "Successful" : "Not mapped"' in source
  assert "Controller inputs are temporarily consumed" in source


def test_controller_joystick_mode_requires_explicit_device_selection():
  source = CONTROLLERS_PATH.read_text(encoding="utf-8")

  assert "Favorite buttons are the default" in source
  assert "Enable for Joystick Mode" in source
  assert 'request("joystick", { device_id: device.device_id, enabled: !selected() })' in source


def test_controller_page_has_ten_controller_only_action_slots():
  source = CONTROLLERS_PATH.read_text(encoding="utf-8")

  assert "Controller-only Actions" in source
  assert "These never appear as on-screen Favorites" in source
  assert 'request("action", { slot: index, key, value })' in source
  assert "const targetIndex = 3 + index" in source
  assert "state.controllerSlots.map(controllerSlotCard)" in source
  assert "Set speed (${() => state.speedUnit})" in source
  assert "Galaxy → Sentry Mode" in source


def test_bluetooth_and_controllers_sidebar_order():
  source = SIDEBAR_PATH.read_text(encoding="utf-8")

  toggles = source.index('{ name: "Toggles"')
  bluetooth = source.index('{ name: "Bluetooth"')
  sentry = source.index('{ name: "Sentry Mode"')
  controllers = source.index('{ name: "Controllers"')
  assert toggles < bluetooth < sentry < controllers


def test_live_link_page_is_wired_consistently():
  sidebar = SIDEBAR_PATH.read_text(encoding="utf-8")
  router = ROUTER_PATH.read_text(encoding="utf-8")
  index = INDEX_PATH.read_text(encoding="utf-8")
  compatibility_page = LIVE_COMPATIBILITY_PATH.read_text(encoding="utf-8")

  # Sidebar entry sits directly after Bluetooth.
  bluetooth = sidebar.index('{ name: "Bluetooth"')
  live_link = sidebar.index('{ name: "Live Link", link: "/live"')
  download = sidebar.index('{ name: "Download Speed Limits"')
  assert bluetooth < live_link < download
  assert 'link: "/live", icon: "bi-broadcast", documentNavigation: true' in sidebar
  assert 'window.location.assign(galaxyPath(href))' in sidebar

  # Router imports and registers the page with a consistent cache-bust string.
  assert '/assets/components/tools/live_link.js?v=live-link-1' in router
  assert 'createRoute("live_link", "/live_link", LiveLink)' in router
  assert 'const target = galaxyPath("/live")' in compatibility_page
  assert "window.location.replace(target)" in compatibility_page
  assert "navigator.bluetooth" not in compatibility_page

  # Stylesheet is linked with the matching cache-bust string.
  assert '/assets/components/tools/live_link.css?v=live-link-1' in index


def test_live_link_has_a_self_contained_bluetooth_page():
  html = LIVE_STANDALONE_HTML_PATH.read_text(encoding="utf-8")
  script = LIVE_STANDALONE_JS_PATH.read_text(encoding="utf-8")
  styles = LIVE_STANDALONE_CSS_PATH.read_text(encoding="utf-8")
  worker = SERVICE_WORKER_PATH.read_text(encoding="utf-8")
  server = GALAXY_SERVER_PATH.read_text(encoding="utf-8")

  assert "live-manifest.json" not in html
  assert "apple-mobile-web-app" not in html
  assert 'src="assets/components/tools/live_link_standalone.js?v=live-link-safari-7"' in html
  assert 'href="assets/components/tools/live_link_standalone.css?v=live-link-safari-7"' in html
  assert 'id="connectButton"' in html
  assert 'id="secureAppLink"' in html
  assert 'id="galaxyMenuLink"' in html
  assert '<a id="secureAppLink" class="primaryButton" href="#" hidden>' in html
  assert 'id="checkSetupButton"' in html
  assert 'id="reloadSetupButton"' in html
  assert 'href="https://apps.apple.com/app/id6761301368"' in html
  assert "Allow on Every Website" in html
  assert "Galaxy → Bluetooth" in html
  assert html.index('id="beacioStep"') < html.index('id="permissionStep"') < html.index('id="pairStep"') < html.index('id="connectStep"')
  assert html.index('id="beacioInstallLink"') < html.index('id="permissionStep"')
  assert html.index('id="checkSetupButton"') < html.index('id="pairStep"')
  assert 'id="featureGrid"' in html
  assert 'id="engagementValue"' not in html
  assert ">AOL<" not in html
  assert ">SLC<" not in html
  assert "Offline app ready" not in html
  assert 'id="slcDetail"' in html

  # The browser page gets driving state only from the companion GATT service.
  assert "navigator.bluetooth.requestDevice" in script
  assert "navigator.bluetooth.getAvailability" in script
  assert "Live Link still cannot see beacio" in script
  assert 'elements.secureAppLink.href = secureBaseUrl' in script
  assert 'elements.secureAppLink.href = `${secureBaseUrl}/live`' not in script
  assert "The comma did not authorize this phone" in script
  assert 'window.location?.hostname === "galaxy.firestar.link"' in script
  assert "This local page does not connect over Bluetooth" in script
  assert "elements.slcChip.hidden = !slcActive" in script
  assert "elements.curveChip.hidden = !curveEnabled" in script
  assert "liveCharacteristic.startNotifications()" in script
  assert 'op: "get_live_metadata"' in script
  assert "fetch(" not in script
  assert "/api/" not in script

  assert '"/assets/components/tools/live_link_standalone.js"' in worker
  assert '"/assets/components/tools/live_link_standalone.css"' in worker
  assert '"/assets/live-manifest.json"' not in worker
  assert 'const liveCacheName = "starpilot-live-shell-v7"' in worker
  assert 'event.request.mode === "navigate"' in worker
  assert '@app.route("/live", methods=["GET"])' in server
  assert 'render_template("live_link.html", secure_live_url=_galaxy_public_base_url())' in server
  declared_functions = {node.name for node in ast.walk(ast.parse(server)) if isinstance(node, ast.FunctionDef)}
  assert "_galaxy_public_base_url" in declared_functions

  # The comma border wraps the whole live surface, and optional features stay compact.
  assert "border: 3px solid var(--drive-border)" in styles
  assert "background: var(--accent)" in styles
  assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in styles
  assert ".offlineReady" not in styles
