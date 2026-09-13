// Only the standalone Telematics page and its static dependencies are saved.
// Bump this version when changing any dependency to refresh the offline snapshot.
const CACHE = "galaxy-telematics-v4:" + self.registration.scope
const workerURL = self.location.href.endsWith("/service-worker.js")
  ? new URL("assets/mobile/telematics-worker.js", self.location.href).href : self.location.href
const appURL = new URL("../../", workerURL)
const dashboardURL = new URL("assets/mobile/telematics.html", appURL).href
const ASSETS = new Set([
  "../mobile/css/material.css",
  "../mobile/css/telematics.css",
  "../mobile/js/api.js",
  "../mobile/js/offline.js",
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
  "../images/main_logo.png",
  "../vendor/bootstrap-icons/bootstrap-icons.min.css",
  "../vendor/bootstrap-icons/fonts/bootstrap-icons.woff",
  "../vendor/bootstrap-icons/fonts/bootstrap-icons.woff2",
  "../vendor/vue/vue.esm-browser.js"
].map(path => new URL(path, workerURL).href))
self.addEventListener("install", event => {
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE)
    await cache.addAll([...ASSETS].map(url => new Request(url, { cache: "reload", redirect: "error" })))
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
self.addEventListener("message", event => {
  if (event.data?.type !== "TELEMATICS_OFFLINE_STATUS") return
  event.waitUntil((async () => {
    const cache = await caches.open(CACHE)
    let ready = (await Promise.all([...ASSETS].map(url => cache.match(url)))).every(Boolean)
    if (!ready && event.data.repair) {
      try {
        await cache.addAll([...ASSETS].map(url => new Request(url, { cache: "reload", redirect: "error" })))
        ready = true
      } catch { /* Preserve the saved entries and let the page offer a retry. */ }
    }
    event.ports[0]?.postMessage({ ready })
  })())
})
self.addEventListener("fetch", event => {
  const url = new URL(event.request.url)
  const launches = [appURL.pathname.replace(/\/$/, ""), appURL.pathname, new URL("mobile/", appURL).pathname, new URL("mobile", appURL).pathname,
    new URL("assets/mobile/index.html", appURL).pathname]
  if (event.request.method === "GET" && event.request.mode === "navigate" && url.origin === appURL.origin && launches.includes(url.pathname)) {
    event.respondWith((async () => {
      const controller = new AbortController()
      const timer = setTimeout(() => controller.abort(), 4000)
      try {
        const response = await fetch(event.request, { signal: controller.signal })
        if (response.ok || (response.status < 500 && !response.redirected)) return response
        // Tunnel outage pages must not prevent a saved PWA from opening.
        const cache = await caches.open(CACHE)
        if (await cache.match(dashboardURL)) return Response.redirect(dashboardURL + "#/telematics")
        return response
      } catch (error) {
        const cache = await caches.open(CACHE)
        if (await cache.match(dashboardURL)) return Response.redirect(dashboardURL + "#/telematics")
        throw error
      } finally { clearTimeout(timer) }
    })())
    return
  }
  // APIs, other pages, and non-GET operations always go directly to the network.
  if (event.request.method !== "GET" || !ASSETS.has(event.request.url.split("?")[0])) return
  event.respondWith((async () => {
    const cache = await caches.open(CACHE)
    // A complete, versioned snapshot avoids mixing old and new modules offline.
    const saved = await cache.match(event.request, { ignoreSearch: true })
    return saved || fetch(event.request)
  })())
})
