// iOS browsers all use WebKit, so user-agent/platform detection is the only
// reliable way to keep the Bluetooth-only Telematics view out of Safari.
export function isIOSDevice() {
  if (typeof navigator === "undefined") return false
  return /iPad|iPhone|iPod/.test(navigator.userAgent || "")
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
}
