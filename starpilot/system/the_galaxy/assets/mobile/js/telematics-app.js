import { galaxyAppBase } from "./browser.js"
import { createApp } from "vue"
import { Telematics } from "./views/Telematics.js"
import { initRouter } from "./store.js"

const mainURL = new URL("mobile/", galaxyAppBase())
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
