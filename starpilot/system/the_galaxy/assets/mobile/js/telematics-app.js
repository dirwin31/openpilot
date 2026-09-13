import { galaxyAppBase } from "./browser.js"
import { createApp } from "vue"
import { Telematics } from "./views/Telematics.js"
import { initRouter } from "./store.js"

const mainURL = new URL("mobile/", galaxyAppBase())
function route() {
  const hash = window.location.hash
  if (hash && hash !== "#/telematics") {
    const target = hash === "#/bluetooth/phone"
      ? new URL("assets/mobile/telematics-setup.html", galaxyAppBase()) : new URL(mainURL)
    if (hash === "#/bluetooth/phone" && new URLSearchParams(window.location.search).get("app") === "telematics") target.searchParams.set("app", "telematics")
    target.hash = hash
    window.location.replace(target.href)
  }
}
if (!window.location.hash) history.replaceState(null, "", "#/telematics")
route()
window.addEventListener("hashchange", route)
initRouter()
createApp(Telematics).mount("#galaxy-app")
