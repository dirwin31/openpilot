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

export function galaxySignInURL(value) {
  try {
    const { login } = destinations(value)
    login.hash = ""
    return login.href
  } catch { return "" }
}

export async function galaxyDestination(value) {
  return destinations(value).target.href
}

export async function openGalaxyBluetooth(value) {
  window.location.assign(await galaxyDestination(value))
}
