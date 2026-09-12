import { createApp } from "vue"
import { Telematics } from "./views/Telematics.js"
import { initRouter } from "./store.js"

const mainURL = new URL("../../", window.location.href)
function route() {
  const hash = window.location.hash
  if (hash && hash !== "#/telematics") {
    mainURL.hash = hash
    window.location.replace(mainURL.href)
  }
}
if (!window.location.hash) history.replaceState(null, "", "#/telematics")
route()
window.addEventListener("hashchange", route)
initRouter()
createApp(Telematics).mount("#galaxy-app")

const status = document.getElementById("offline-status")
async function prepareOffline() {
  try {
    if (!window.isSecureContext || !("serviceWorker" in navigator)) throw new Error("HTTPS required")
    const registration = await navigator.serviceWorker.register("telematics-worker.js", { scope: "./" })
    const worker = registration.installing || registration.waiting || registration.active
    if (!worker) throw new Error("Worker unavailable")
    if (worker.state !== "activated") await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error("Saving timed out")), 60000)
      worker.addEventListener("statechange", () => {
        if (worker.state === "activated" || worker.state === "redundant") {
          clearTimeout(timer)
          worker.state === "activated" ? resolve() : reject(new Error("Saving failed"))
        }
      })
    })
    status.textContent = "Ready offline. Bookmark this page and reopen this same address near your paired comma."
  } catch (error) {
    status.textContent = "Offline saving unavailable. Connect to the network and use an HTTPS address with a trusted certificate, then reload."
  }
}
void prepareOffline()
