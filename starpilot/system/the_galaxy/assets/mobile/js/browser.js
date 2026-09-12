// iOS browsers all use WebKit, so user-agent/platform detection is the only
// reliable way to keep the Bluetooth-only Telematics view out of Safari.
export function isIOSDevice() {
  if (typeof navigator === "undefined") return false
  return /iPad|iPhone|iPod/.test(navigator.userAgent || "")
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
}

// Web Bluetooth exists only in Chromium browsers on Android, and only on a secure
// page. Firefox never exposes it, so it is turned away before the HTTPS detour.
// Returns "ios", "firefox", "unsupported", "insecure" or "ready".
export function bluetoothPlatform() {
  if (isIOSDevice()) return "ios"
  const agent = navigator.userAgent || ""
  if (/Firefox\//.test(agent)) return "firefox"
  if (!/Android/i.test(agent)) return "unsupported"
  if (!(window.isSecureContext && window.location.protocol === "https:")) return "insecure"
  return navigator.bluetooth ? "ready" : "unsupported"
}
