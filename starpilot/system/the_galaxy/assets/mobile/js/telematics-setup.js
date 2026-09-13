import { isTelematicsAppWindow } from "./telematics-install.js"
import { createApp } from "vue"
import { PhonePanel } from "./components/PhonePanel.js"
import { galaxyAppBase } from "./browser.js"
import { initRouter } from "./store.js"

function route() {
  const hash = window.location.hash
  if (!hash || hash === "#/bluetooth/phone") return
  const target = hash === "#/telematics"
    ? new URL("assets/mobile/telematics.html", galaxyAppBase())
    : new URL("mobile/", galaxyAppBase())
  if (hash === "#/telematics" && isTelematicsAppWindow()) target.searchParams.set("app", "telematics")
  target.hash = hash
  window.location.assign(target.href)
}
if (!window.location.hash) history.replaceState(null, "", "#/bluetooth/phone")
route()
window.addEventListener("hashchange", route)
initRouter()
createApp(PhonePanel).mount("#galaxy-app")
