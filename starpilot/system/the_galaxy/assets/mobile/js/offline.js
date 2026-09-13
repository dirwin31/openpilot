import { reactive } from "vue"
import { api } from "./api.js"
import { isGalaxyLink, isIOSDevice, galaxyAppBase } from "./browser.js"

export const offlineState = reactive({ state: "idle", message: "Preparing Telematics for use without internet…" })
const base = galaxyAppBase()
const dashboard = new URL("assets/mobile/telematics.html", base)
let preparation

function activated(registration) {
  const worker = registration.installing || registration.waiting || registration.active
  if (!worker) return Promise.reject(new Error("Worker unavailable"))
  return new Promise((resolve, reject) => {
    const finish = () => {
      if (!["activated", "redundant"].includes(worker.state)) return
      clearTimeout(timer)
      worker.removeEventListener("statechange", finish)
      worker.state === "activated" ? resolve() : reject(new Error("Saving failed"))
    }
    const timer = setTimeout(() => {
      worker.removeEventListener("statechange", finish)
      reject(new Error("Saving timed out"))
    }, 60000)
    worker.addEventListener("statechange", finish)
    finish()
  })
}

export function prepareOffline(retry = false) {
  if (!isGalaxyLink() || isIOSDevice()) return Promise.resolve()
  if (preparation && !retry) return preparation
  offlineState.state = "saving"
  offlineState.message = "Preparing Telematics for use without internet…"
  preparation = (async () => {
    try {
      if (!window.isSecureContext || !("serviceWorker" in navigator)) throw new Error("Saving unavailable")
      // Keep a verified device identity with the cached dashboard, even when setup
      // starts on the PWA home page rather than Telematics.
      let identity = localStorage.getItem(`galaxy-telematics-page:${dashboard.pathname}`)
      try {
        const status = await api.getDeviceStatus({ signal: AbortSignal.timeout(3000), cache: "no-store" })
        if (status?.telematicsDeviceId) {
          identity = status.telematicsDeviceId
          localStorage.setItem(`galaxy-telematics-page:${dashboard.pathname}`, identity)
        }
      } catch { /* An already saved identity remains usable without the tunnel. */ }
      const registration = await navigator.serviceWorker.register(new URL("service-worker.js", base).href, { scope: base.pathname.replace(/\/$/, "") || "/" })
        .catch(async error => {
          const saved = await navigator.serviceWorker.getRegistration(base.href)
          if (saved?.active) return saved
          throw error
        })
      await activated(registration).catch(error => {
        if (registration.active?.state !== "activated") throw error
      })
      // Activation alone is insufficient when an older notifications-only worker
      // is active. Ask the controlling version to verify its complete snapshot.
      await new Promise((resolve, reject) => {
        const channel = new MessageChannel()
        const timer = setTimeout(() => { channel.port1.close(); reject(new Error("Snapshot unavailable")) }, 60000)
        channel.port1.onmessage = event => {
          clearTimeout(timer)
          channel.port1.close()
          event.data?.ready ? resolve() : reject(new Error("Snapshot incomplete"))
        }
        registration.active.postMessage({ type: "TELEMATICS_OFFLINE_STATUS", repair: true }, [channel.port2])
      })
      // Retire the old, narrower dashboard worker after the PWA snapshot is safe.
      for (const previous of await navigator.serviceWorker.getRegistrations()) {
        if (previous.scope === new URL("assets/mobile/", base).href) await previous.unregister()
      }
      if (!identity) throw new Error("Device identity unavailable")
      offlineState.state = "ready"
      offlineState.message = "Available without internet. Reopen Galaxy near your paired comma with Bluetooth enabled."
    } catch (error) {
      offlineState.state = "error"
      offlineState.message = "Telematics could not be saved completely. Connect to the internet and retry saving from your Galaxy link."
    }
  })()
  return preparation
}
