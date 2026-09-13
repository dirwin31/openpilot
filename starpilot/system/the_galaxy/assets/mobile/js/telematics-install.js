import { reactive } from "vue"
import { galaxyAppBase } from "./browser.js"
import { offlineState, prepareOffline } from "./offline.js"

export function isTelematicsAppWindow() {
  return window.matchMedia("(display-mode: standalone)").matches
    && /\/telematics(?:-setup)?\.html$/.test(window.location.pathname)
    && new URLSearchParams(window.location.search).get("app") === "telematics"
}

const dismissalKey = "galaxy-telematics-offline-banner-dismissed"
let dismissed = false
try { dismissed = localStorage.getItem(dismissalKey) === "1" } catch { /* Storage may be blocked. */ }
export const telematicsOffline = reactive({ dismissed })

export function cachedTelematicsURL() {
  const url = new URL("assets/mobile/telematics.html", galaxyAppBase())
  url.hash = "/telematics"
  return url.href
}

export async function saveTelematicsOffline() {
  if (offlineState.state === "saving") return
  await prepareOffline(true)
}

export function dismissOfflineBanner() {
  telematicsOffline.dismissed = true
  try { localStorage.setItem(dismissalKey, "1") } catch { /* Dismiss for this visit. */ }
}

export function showOfflineBanner() {
  telematicsOffline.dismissed = false
  try { localStorage.removeItem(dismissalKey) } catch { /* Storage may be blocked. */ }
}
