import { reactive } from "vue"
import { galaxyAppBase } from "./browser.js"
import { offlineState, prepareOffline } from "./offline.js"

export function isTelematicsAppWindow() {
  return window.matchMedia("(display-mode: standalone)").matches
    && /\/telematics(?:-setup)?\.html$/.test(window.location.pathname)
    && new URLSearchParams(window.location.search).get("app") === "telematics"
}

export const telematicsInstall = reactive({
  setupPage: window.location.pathname.endsWith("/telematics-setup.html"),
  appWindow: isTelematicsAppWindow(),
  prompt: null,
  installed: false,
  message: "",
})

// This document has its own manifest. Never capture Galaxy's install prompt.
if (telematicsInstall.setupPage) {
  window.addEventListener("beforeinstallprompt", event => {
    event.preventDefault()
    telematicsInstall.prompt = event
  })
  window.addEventListener("appinstalled", () => {
    telematicsInstall.prompt = null
    telematicsInstall.installed = true
    telematicsInstall.message = "Telematics installed. Open its home-screen icon near your comma."
  })
}

export async function setupTelematicsOffline() {
  if (offlineState.state === "saving") return
  await prepareOffline(true)
  if (offlineState.state !== "ready") return
  if (!telematicsInstall.setupPage) {
    const url = new URL("assets/mobile/telematics-setup.html", galaxyAppBase())
    url.hash = "/bluetooth/phone"
    window.location.assign(url.href)
  }
}

export async function installTelematics() {
  const prompt = telematicsInstall.prompt
  if (!prompt || offlineState.state !== "ready") return
  telematicsInstall.prompt = null
  try {
    // Called by the Phone page's Install button, inside the user gesture.
    await prompt.prompt()
    const choice = await prompt.userChoice
    telematicsInstall.message = choice.outcome === "accepted"
      ? "Installation requested. Look for the Telematics icon on your home screen."
      : "Installation cancelled. You can install later from Chrome’s menu."
  } catch {
    telematicsInstall.message = "Use Chrome’s menu → Add to Home screen → Install."
  }
}
