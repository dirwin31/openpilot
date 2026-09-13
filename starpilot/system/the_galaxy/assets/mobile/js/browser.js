// iOS browsers all use WebKit, so user-agent/platform detection is the only
// reliable way to keep the Bluetooth-only Telematics view out of Safari.
export function isIOSDevice() {
  if (typeof navigator === "undefined") return false
  return /iPad|iPhone|iPod/.test(navigator.userAgent || "")
    || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1)
}

// Web Bluetooth exists only in Chromium browsers on Android, and only on a secure
// page. Firefox never exposes it, so it receives the browser support notice.
// Returns "ios", "firefox", "unsupported", "insecure" or "ready".
export function bluetoothPlatform() {
  if (isIOSDevice()) return "ios"
  const agent = navigator.userAgent || ""
  if (/Firefox\//.test(agent)) return "firefox"
  if (!/Android/i.test(agent)) return "unsupported"
  if (!(window.isSecureContext && window.location.protocol === "https:")) return "insecure"
  return navigator.bluetooth ? "ready" : "unsupported"
}

// Only the public Galaxy app owns browser Bluetooth permissions and offline data.
export function isGalaxyLink() {
  return window.location.protocol === "https:" && ["galaxy.link", "galaxy.firestar.link"].includes(window.location.hostname)
}

export function galaxyRoute(url, route = "/telematics") {
  try {
    const target = new URL(url)
    if (target.protocol !== "https:" || !["galaxy.link", "galaxy.firestar.link"].includes(target.hostname)
      || !/^\/[A-Za-z0-9]{16}\/?$/.test(target.pathname) || target.username || target.password || target.port) return ""
    target.pathname = target.pathname.replace(/\/$/, "") + "/mobile/"
    target.search = ""
    target.hash = route
    return target.href
  } catch { return "" }
}

export function galaxyAppBase() {
  const slug = isGalaxyLink() && window.location.pathname.match(/^\/([A-Za-z0-9]{16})(?:\/|$)/)
  return new URL(slug ? `/${slug[1]}/` : "/", window.location.origin)
}
