// Only the standalone Telematics page and its static dependencies are saved.
// Bump this version when changing any dependency to refresh the offline snapshot.
const CACHE = "galaxy-telematics-v1:" + self.registration.scope
const ASSETS = new Set([
  "../mobile/css/material.css",
  "../mobile/css/telematics.css",
  "../mobile/js/api.js",
  "../mobile/js/ble/live_ble.js",
  "../mobile/js/ble/live_frames.js",
  "../mobile/js/browser.js",
  "../mobile/js/components/BluetoothSupportNotice.js",
  "../mobile/js/components/GalaxyModal.js",
  "../mobile/js/components/GxNotice.js",
  "../mobile/js/composables.js",
  "../mobile/js/lan/connection.js",
  "../mobile/js/lan/live_lan.js",
  "../mobile/js/store.js",
  "../mobile/js/telematics-app.js",
  "../mobile/js/views/Telematics.js",
  "../mobile/telematics.html",
  "../vendor/bootstrap-icons/bootstrap-icons.min.css",
  "../vendor/bootstrap-icons/fonts/bootstrap-icons.woff",
  "../vendor/bootstrap-icons/fonts/bootstrap-icons.woff2",
  "../vendor/vue/vue.esm-browser.js"
].map(path => new URL(path, self.location.href).href))
self.addEventListener("install", event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE)
    await cache.addAll([...ASSETS].map(url => new Request(url, { cache: "reload" })))
    await self.skipWaiting()
  })())
})
self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    for (const key of await caches.keys()) {
      if (key.startsWith("galaxy-telematics-") && key.endsWith(":" + self.registration.scope) && key !== CACHE) await caches.delete(key)
    }
    await self.clients.claim()
  })())
})
self.addEventListener("fetch", event => {
  // APIs, other pages, and non-GET operations always go directly to the network.
  if (event.request.method !== "GET" || !ASSETS.has(event.request.url.split("?")[0])) return
  event.respondWith((async () => {
    const cache = await caches.open(CACHE)
    // A complete, versioned snapshot avoids mixing old and new modules offline.
    const saved = await cache.match(event.request, { ignoreSearch: true })
    return saved || fetch(event.request)
  })())
})
