import { html } from "/assets/vendor/arrow-core.js"
import { galaxyPath } from "/assets/js/utils.js"


let navigating = false

// Compatibility route for old /live_link bookmarks. The standalone document
// owns Live Link so it can be installed and served entirely by its offline
// cache instead of depending on the Galaxy SPA at runtime.
export function LiveLink() {
  const target = galaxyPath("/live")
  if (!navigating) {
    navigating = true
    setTimeout(() => window.location.replace(target), 0)
  }

  return html`
    <div class="liveLinkPage">
      <div class="liveConnectCard">
        <i class="bi bi-broadcast" aria-hidden="true"></i>
        <h3>Opening StarPilot Live Link…</h3>
        <p>Live Link runs as a Bluetooth-only offline app.</p>
        <a class="liveButton" href="${target}">Open Live Link</a>
      </div>
    </div>
  `
}
