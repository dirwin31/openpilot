const appBasePath = self.location.pathname.replace(/\/service-worker\.js$/, "").replace(/\/$/, "")
const liveCacheName = "starpilot-live-shell-v2"
const liveShellPaths = [
  "/live",
  "/assets/live-manifest.json",
  "/assets/components/tools/live_link_standalone.css",
  "/assets/components/tools/live_link_standalone.js",
  "/assets/images/apple-touch-icon.png",
  "/assets/images/android-chrome-192x192.png",
  "/assets/images/android-chrome-512x512.png",
  "/assets/images/favicon.ico",
]

function appUrl(path) {
  const suffix = path.startsWith("/") ? path : `/${path}`
  return new URL(`${appBasePath}${suffix}`, self.location.origin).href
}

const liveShellUrls = liveShellPaths.map(appUrl)
const liveShellPathnames = new Set(liveShellUrls.map((url) => new URL(url).pathname))

self.addEventListener("install", (event) => {
  event.waitUntil(Promise.all([
    self.skipWaiting(),
    caches.open(liveCacheName).then((cache) => cache.addAll(liveShellUrls)),
  ]))
})

self.addEventListener("activate", (event) => {
  event.waitUntil(Promise.all([
    self.clients.claim(),
    caches.keys().then((names) => Promise.all(
      names.filter((name) => name.startsWith("starpilot-live-shell-") && name !== liveCacheName)
        .map((name) => caches.delete(name)),
    )),
  ]))
})

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return
  const requestUrl = new URL(event.request.url)
  if (requestUrl.origin !== self.location.origin) return

  const livePath = new URL(appUrl("/live")).pathname
  const isTrailingLiveNavigation = event.request.mode === "navigate" && requestUrl.pathname === `${livePath}/`
  if (isTrailingLiveNavigation) {
    event.respondWith(Promise.resolve(Response.redirect(appUrl("/live"), 308)))
    return
  }
  const isLiveNavigation = event.request.mode === "navigate" && requestUrl.pathname === livePath
  if (!isLiveNavigation && !liveShellPathnames.has(requestUrl.pathname)) return

  event.respondWith(
    caches.open(liveCacheName).then(async (cache) => {
      const cached = await cache.match(isLiveNavigation ? appUrl("/live") : event.request, { ignoreSearch: true })
      if (cached) return cached
      return fetch(event.request)
    }),
  )
})

function scopedUrl(path) {
  const url = new URL(path || "/sentry", self.location.origin)
  if (appBasePath && url.pathname !== appBasePath && !url.pathname.startsWith(`${appBasePath}/`)) {
    url.pathname = `${appBasePath}${url.pathname}`
  }
  return url.href
}

self.addEventListener("push", (event) => {
  let data = {}
  try {
    data = event.data ? event.data.json() : {}
  } catch {
    data = { body: event.data?.text() || "Sentry event detected." }
  }

  const title = data.title || "StarPilot Sentry Mode"
  const options = {
    body: data.body || "Movement detected while parked.",
    tag: `starpilot-sentry-${data.eventId || "event"}`,
    data: { url: scopedUrl(data.url || "/sentry") },
    icon: scopedUrl("/assets/images/favicon.ico"),
    badge: scopedUrl("/assets/images/favicon-32x32.png"),
    requireInteraction: true,
  }
  if (data.image) options.image = scopedUrl(data.image)

  event.waitUntil(self.registration.showNotification(title, options))
})

self.addEventListener("notificationclick", (event) => {
  event.notification.close()
  const targetUrl = scopedUrl(event.notification.data?.url || "/sentry")

  event.waitUntil(
    clients.matchAll({ type: "window", includeUncontrolled: true }).then((windowClients) => {
      for (const client of windowClients) {
        if ("focus" in client) {
          client.navigate(targetUrl)
          return client.focus()
        }
      }
      return clients.openWindow(targetUrl)
    })
  )
})
