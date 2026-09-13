// A device page cannot assume the phone has a signed-in Galaxy session.
function destinations(value) {
  const target = new URL(value)
  const match = target.pathname.match(/^\/([A-Za-z0-9]{16})\/mobile\/$/)
  if (target.protocol !== "https:" || !["galaxy.link", "galaxy.firestar.link"].includes(target.hostname)
      || target.username || target.password || target.port || !match
      || !["#/telematics", "#/bluetooth/phone"].includes(target.hash)) throw new Error("Your Galaxy link is unavailable. Set up Galaxy remote access first.")
  const login = new URL(`/${match[1]}`, target.origin)
  // Retain the destination for login services that preserve fragments.
  login.hash = target.hash
  target.search = ""
  return { target, login }
}

async function probe(url, mode) {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), 6000)
  try {
    return await fetch(url, { method: "GET", mode, credentials: "include", redirect: "manual", cache: "no-store", signal: controller.signal })
  } finally { clearTimeout(timer) }
}

export async function galaxyDestination(value) {
  const { target, login } = destinations(value)
  const check = new URL(target)
  check.hash = ""
  let response
  try {
    response = await probe(check.href, "cors")
  } catch {
    // CORS and network failures look alike. An opaque request can check basic
    // reachability, but cannot verify a session. Never treat opacity as sign-in.
    try { response = await probe(login.href.split("#")[0], "no-cors") }
    catch { throw new Error("Galaxy is unreachable. Check your internet connection and try again.") }
    if (response.type === "opaque" || response.type === "opaqueredirect") return login.href
    if (response.status >= 500) throw new Error("Galaxy is unavailable right now. Try again shortly.")
    return login.href
  }
  if (response.status >= 500) throw new Error("Galaxy is unavailable right now. Try again shortly.")
  if (response.ok && response.type !== "opaque") {
    const html = await response.text()
    if (html.includes("galaxy-app") && html.includes("assets/mobile/js/app.js")) return target.href
  }
  // Authentication errors, redirects and the gateway's unknown-route response
  // all go through the canonical slug entry point instead of another deep link.
  return login.href
}

export async function openGalaxyBluetooth(value) {
  const destination = await galaxyDestination(value)
  window.location.assign(destination)
}
