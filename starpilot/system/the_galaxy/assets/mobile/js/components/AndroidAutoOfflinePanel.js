import { api, showSnackbar } from "../api.js"
import { usePolling } from "../composables.js"
import { GxNotice } from "./GxNotice.js"
import {
  coverageToGeoJson,
  directionsToRoutes,
  formatBytes,
  formatDistance,
  formatDuration,
  itemBounds,
  itemStatus,
  itemsToGeoJson,
  presetDetailDescription,
  radiusLabel,
  serviceNotice,
} from "./auto_offline_helpers.js?v=auto-offline-3"
import { getMapboxSearchContext } from "../../../components/navigation/navigation_utilities.js?v=nav-route-selection-1"

const MAPBOX_STYLE = "mapbox://styles/frogsgomoo/cmcfv151j000o01rcdxebhl76"
const EMPTY = { type: "FeatureCollection", features: [] }

let mapboxLoadPromise = null
function loadMapboxGL() {
  if (mapboxLoadPromise) return mapboxLoadPromise
  mapboxLoadPromise = new Promise((resolve, reject) => {
    if (window.mapboxgl) return resolve(window.mapboxgl)
    const link = document.createElement("link")
    link.rel = "stylesheet"
    link.href = "https://api.mapbox.com/mapbox-gl-js/v3.0.1/mapbox-gl.css"
    document.head.appendChild(link)
    const script = document.createElement("script")
    script.src = "https://api.mapbox.com/mapbox-gl-js/v3.0.1/mapbox-gl.js"
    script.onload = () => resolve(window.mapboxgl)
    script.onerror = () => reject(new Error("Failed to load Mapbox GL"))
    document.head.appendChild(script)
  })
  return mapboxLoadPromise
}

function newSessionToken() {
  return window.crypto?.randomUUID ? window.crypto.randomUUID() : String(Date.now()) + Math.random().toString(16).slice(2)
}

// A place search box using the same Mapbox Search Box calls as the Destination tab.
const PlaceSearch = {
  name: "PlaceSearch",
  props: {
    token: { type: String, default: "" },
    position: { type: Object, default: null },
    placeholder: { type: String, default: "Search a place or address" },
    selected: { type: Object, default: null },
  },
  emits: ["select", "clear"],
  data() { return { query: "", suggestions: [], searching: false, request: 0, timer: null, sessionToken: newSessionToken() } },
  watch: {
    selected: { immediate: true, handler(place) { if (place) this.query = place.name || "" } },
  },
  beforeUnmount() { clearTimeout(this.timer) },
  methods: {
    context(query) {
      const context = getMapboxSearchContext(query, this.position, navigator.languages || [navigator.language])
      if (this.position) context.proximity = `${this.position.longitude},${this.position.latitude}`
      return context
    },
    onInput(event) {
      this.query = String(event?.target?.value ?? this.query)
      if (this.selected) this.$emit("clear")
      clearTimeout(this.timer)
      this.request += 1
      const value = this.query.trim()
      if (value.length < 3 || !this.token) {
        this.suggestions = []
        return
      }
      this.timer = setTimeout(() => this.search(value), 350)
    },
    async search(value) {
      const request = ++this.request
      this.searching = true
      try {
        const payload = await api.mapboxSuggest(value, this.token, this.sessionToken, this.context(value))
        if (request === this.request) this.suggestions = Array.isArray(payload?.suggestions) ? payload.suggestions : []
      } catch (e) {
        if (request === this.request) this.suggestions = []
      } finally {
        if (request === this.request) this.searching = false
      }
    },
    async choose(place) {
      this.suggestions = []
      const name = String(place?.name || "").trim() || this.query.trim()
      try {
        let coordinates = place?.geometry?.coordinates
        if (!coordinates && place?.mapbox_id) {
          const payload = await api.mapboxRetrieve(place.mapbox_id, this.token, this.sessionToken)
          coordinates = payload?.features?.[0]?.geometry?.coordinates
        }
        if (!Array.isArray(coordinates)) throw new Error("no coordinates")
        this.sessionToken = newSessionToken()
        this.$emit("select", { latitude: Number(coordinates[1]), longitude: Number(coordinates[0]), name })
      } catch (e) {
        showSnackbar("Could not find that place's location.", "error")
      }
    },
  },
  template: `
    <div style="position:relative; flex:1; min-width:200px;">
      <input class="gx-field" style="width:100%;" type="search" :value="query" :placeholder="placeholder" :disabled="!token" @input="onInput" />
      <div v-if="suggestions.length" class="gx-card" style="position:absolute; left:0; right:0; top:100%; z-index:5; margin-top:4px; max-height:260px; overflow:auto;">
        <button v-for="place in suggestions" :key="place.mapbox_id || place.name" type="button" class="gx-row"
          style="width:100%; border:none; background:transparent; color:inherit; cursor:pointer; text-align:left;" @click="choose(place)">
          <div class="gx-row__info">
            <span class="gx-row__label">{{ place.name }}</span>
            <span class="gx-row__desc">{{ place.place_formatted || place.full_address || '' }}</span>
          </div>
        </button>
      </div>
    </div>
  `,
}

export const AndroidAutoOfflinePanel = {
  name: "AndroidAutoOfflinePanel",
  components: { GxNotice, PlaceSearch },
  data() {
    return {
      summary: null,
      loaded: false,
      error: "",
      busy: "",
      mapReady: false,
      coverageEnabled: true,
      coverageZoom: 16,
      coverage: null,
      coverageError: "",
      coverageRequest: 0,
      coverageLoading: false,
      pickOnMap: false,
      areaPoint: null,
      areaPresets: null,
      areaLoading: false,
      areaError: "",
      areaRequest: 0,
      routeFrom: null,
      routeTo: null,
      routes: [],
      routeIndex: 0,
      routeEstimate: null,
      routeError: "",
      routeRequest: 0,
      findingRoute: false,
    }
  },
  created() {
    this.map = null
    this.marker = null
    this.poll = usePolling(() => this.refresh(), { interval: 3000 })
    this.poll.start()
  },
  beforeUnmount() {
    this.coverageRequest += 1
    clearTimeout(this.coverageTimer)
    this.areaRequest += 1
    this.routeRequest += 1
    this.poll?.destroy()
    this.map?.remove()
    this.map = null
  },
  computed: {
    metric() { return !!this.summary?.isMetric },
    token() { return String(this.summary?.mapboxPublic || "").trim() },
    position() { return this.summary?.position || null },
    items() { return this.summary?.items || [] },
    notice() { return serviceNotice(this.summary) },
    storageLabel() {
      if (!this.summary) return "Checking..."
      return `${formatBytes(this.summary.offline_bytes)} of ${formatBytes(this.summary.max_bytes)}`
    },
    downloaderLabel() {
      if (!this.summary) return "Checking..."
      if (!this.summary.service_running) return "Not running"
      if (this.items.some((item) => item.state === "downloading")) return "Downloading"
      if (this.items.some((item) => item.state === "waiting_wifi")) return "Waiting for Wi-Fi"
      if (this.items.some((item) => item.state === "queued")) return "Queued"
      if (this.items.some((item) => ["incomplete", "storage_full", "no_space"].includes(item.state))) return "Needs attention"
      return "Idle"
    },
    activeRouteLabel() {
      const route = this.summary?.route || {}
      const total = Number(route.total) || 0
      if (!total) return "No route set"
      const remaining = Number(route.remaining) || 0
      return remaining > 0 ? `Saving · ${Math.floor(((total - remaining) / total) * 100)}%` : "Saved for offline"
    },
    selectedRoute() { return this.routes[this.routeIndex] || null },
  },
  watch: {
    items() { this.drawSaved() },
    coverageZoom() { this.scheduleCoverage() },
    coverageEnabled() { this.scheduleCoverage() },
  },
  methods: {
    async refresh() {
      try {
        this.summary = await api.getAutoOffline()
        this.error = ""
        if (!this.coverageLoading) this.refreshCoverage()
      } catch (e) {
        this.error = e?.message || "Could not read the offline maps."
      } finally {
        this.loaded = true
      }
      if (this.token && !this.map) {
        await this.$nextTick()
        this.setupMap()
      }
    },

    // ── map ────────────────────────────────────────────────────────────────
    async setupMap() {
      if (this.map || !this.$refs.map || !this.token) return
      try {
        const mapboxgl = await loadMapboxGL()
        mapboxgl.accessToken = this.token
        const center = this.position || { latitude: 39.5, longitude: -98.35 }
        this.map = new mapboxgl.Map({
          container: this.$refs.map,
          style: MAPBOX_STYLE,
          center: [center.longitude, center.latitude],
          zoom: this.position ? 9 : 3,
          attributionControl: false,
        })
        this.map.on("load", () => {
          for (const [id, data] of [["auto-coverage", EMPTY], ["auto-areas", EMPTY], ["auto-routes", EMPTY], ["auto-candidates", EMPTY]]) {
            this.map.addSource(id, { type: "geojson", data })
          }
          this.map.addLayer({ id: "auto-coverage-fill", type: "fill", source: "auto-coverage",
            paint: { "fill-color": ["case", ["get", "saved"], "#34c778", "#4096ff"], "fill-opacity": 0.3 } })
          this.map.addLayer({ id: "auto-coverage-line", type: "line", source: "auto-coverage",
            paint: { "line-color": ["case", ["get", "saved"], "#34c778", "#4096ff"], "line-width": 1 } })
          this.map.addLayer({ id: "auto-areas-fill", type: "fill", source: "auto-areas", paint: { "fill-color": "#9d72ff", "fill-opacity": 0.14 } })
          this.map.addLayer({ id: "auto-areas-line", type: "line", source: "auto-areas", paint: { "line-color": "#9d72ff", "line-width": 2 } })
          this.map.addLayer({ id: "auto-routes-line", type: "line", source: "auto-routes", paint: { "line-color": "#4096ff", "line-width": 4 } })
          this.map.addLayer({
            id: "auto-candidates-line", type: "line", source: "auto-candidates",
            paint: { "line-color": ["case", ["get", "selected"], "#34c778", "#8a93a6"], "line-width": ["case", ["get", "selected"], 5, 3] },
          })
          this.mapReady = true
          this.refreshCoverage()
          this.drawSaved()
          this.drawCandidates()
        })
        this.map.on("moveend", () => this.scheduleCoverage())
        this.map.on("click", (event) => {
          if (!this.pickOnMap) return
          this.pickOnMap = false
          this.chooseAreaPoint({ latitude: event.lngLat.lat, longitude: event.lngLat.lng, name: "" }, false)
        })
      } catch (e) {
        this.error = e?.message || "Could not load the map."
      }
    },
    scheduleCoverage() {
      this.coverageRequest += 1
      this.coverageLoading = false
      this.coverage = null
      this.coverageError = ""
      this.map?.getSource("auto-coverage")?.setData(EMPTY)
      clearTimeout(this.coverageTimer)
      this.coverageTimer = setTimeout(() => this.refreshCoverage(), 250)
    },
    async refreshCoverage() {
      if (!this.mapReady || !this.map || !this.coverageEnabled) return
      const request = ++this.coverageRequest
      const bounds = this.map.getBounds()
      this.coverageLoading = true
      try {
        const coverage = await api.getAutoOfflineCoverage({ zoom: this.coverageZoom,
          west: bounds.getWest(), south: bounds.getSouth(), east: bounds.getEast(), north: bounds.getNorth() })
        if (request !== this.coverageRequest) return
        this.coverage = coverage
        this.coverageError = ""
        this.map.getSource("auto-coverage")?.setData(coverageToGeoJson(coverage))
      } catch (e) {
        if (request !== this.coverageRequest) return
        this.coverage = null
        this.map?.getSource("auto-coverage")?.setData(EMPTY)
        this.coverageError = e?.message || "Could not load downloaded tile coverage."
      } finally {
        if (request === this.coverageRequest) this.coverageLoading = false
      }
    },
    drawSaved() {
      if (!this.mapReady) return
      const { areas, routes } = itemsToGeoJson(this.items)
      this.map.getSource("auto-areas")?.setData(areas)
      this.map.getSource("auto-routes")?.setData(routes)
    },
    drawCandidates() {
      if (!this.mapReady) return
      const features = this.routes.map((route, index) => ({
        type: "Feature",
        properties: { selected: index === this.routeIndex },
        geometry: { type: "LineString", coordinates: route.points.map(([lat, lon]) => [lon, lat]) },
      }))
      features.sort((a, b) => Number(a.properties.selected) - Number(b.properties.selected))
      this.map.getSource("auto-candidates")?.setData({ type: "FeatureCollection", features })
    },
    showPoint(point, recenter = true) {
      if (!this.map || !window.mapboxgl) return
      this.marker?.remove()
      this.marker = new window.mapboxgl.Marker({ color: "#9d72ff" }).setLngLat([point.longitude, point.latitude]).addTo(this.map)
      if (recenter) this.map.flyTo({ center: [point.longitude, point.latitude], zoom: 9 })
    },
    focus(item) {
      if (!this.map) return
      const [west, south, east, north] = itemBounds(item)
      this.map.fitBounds([[west, south], [east, north]], { padding: 40, maxZoom: 13 })
      this.$refs.map?.scrollIntoView?.({ behavior: "smooth", block: "center" })
    },

    // ── areas ──────────────────────────────────────────────────────────────
    useCurrentLocation() {
      if (!this.position) {
        showSnackbar("The comma doesn't have a location yet.", "error")
        return
      }
      this.chooseAreaPoint({ ...this.position, name: "" })
    },
    async chooseAreaPoint(point, recenter = true) {
      if (this.busy) return
      const request = ++this.areaRequest
      this.pickOnMap = false
      this.areaPoint = point
      this.areaPresets = null
      this.areaError = ""
      this.areaLoading = true
      this.showPoint(point, recenter)
      try {
        const payload = await api.estimateAutoOffline({ latitude: point.latitude, longitude: point.longitude })
        if (request !== this.areaRequest) return
        if (!payload?.presets?.length) throw new Error("No area sizes returned. Try again.")
        this.areaPresets = payload.presets
      } catch (e) {
        if (request === this.areaRequest) this.areaError = e?.message || "Could not size that area. Try again."
      } finally {
        if (request === this.areaRequest) this.areaLoading = false
      }
      if (request !== this.areaRequest) return
      if (!point.name && this.token) {
        try {
          const payload = await api.mapboxReverseCity(point.latitude, point.longitude, this.token)
          const name = payload?.features?.[0]?.properties?.name
          if (request === this.areaRequest && name) this.areaPoint = { ...point, name }
        } catch (e) { /* keep the coordinates as the name */ }
      }
    },
    areaName() {
      const point = this.areaPoint
      return point?.name || (point ? `${point.latitude.toFixed(3)}, ${point.longitude.toFixed(3)}` : "")
    },
    async saveArea(preset) {
      if (!this.areaPoint || this.busy) return
      this.busy = "area"
      try {
        await api.addAutoOfflineArea({
          name: this.areaName(), latitude: this.areaPoint.latitude, longitude: this.areaPoint.longitude,
          radius_km: preset.radius_km, max_zoom: preset.max_zoom,
        })
        showSnackbar(`Saving ${radiusLabel(preset.radius_km, this.metric)} around ${this.areaName()} for offline use.`)
        this.areaRequest += 1
        this.areaPoint = null
        this.areaPresets = null
        this.marker?.remove()
        await this.refresh()
      } catch (e) {
        showSnackbar(e?.message || "Could not save the area.", "error")
      } finally {
        this.busy = ""
      }
    },

    // ── routes ─────────────────────────────────────────────────────────────
    clearRoutes() {
      this.routeRequest += 1
      this.routeError = ""
      this.routes = []
      this.routeIndex = 0
      this.routeEstimate = null
      this.drawCandidates()
    },
    async findRoutes() {
      const from = this.routeFrom || (this.position ? { ...this.position, name: "Current location" } : null)
      if (!from) {
        showSnackbar("Choose where the route starts; the comma has no location yet.", "error")
        return
      }
      if (!this.routeTo) return
      this.findingRoute = true
      this.clearRoutes()
      try {
        this.routes = directionsToRoutes(await api.mapboxDirections(from, this.routeTo, this.token))
        if (!this.routes.length) throw new Error("No route found between those places.")
        this.drawCandidates()
        const lons = this.routes.flatMap((r) => r.points.map((p) => p[1]))
        const lats = this.routes.flatMap((r) => r.points.map((p) => p[0]))
        this.map?.fitBounds([[Math.min(...lons), Math.min(...lats)], [Math.max(...lons), Math.max(...lats)]], { padding: 40 })
        await this.estimateRoute()
      } catch (e) {
        showSnackbar(e?.message || "Route search failed.", "error")
      } finally {
        this.findingRoute = false
      }
    },
    async selectRoute(index) {
      this.routeIndex = index
      this.drawCandidates()
      await this.estimateRoute()
    },
    async estimateRoute() {
      const request = ++this.routeRequest
      const route = this.selectedRoute
      this.routeError = ""
      this.routeEstimate = null
      if (!route) return
      try {
        const estimate = await api.estimateAutoOffline({ points: route.points })
        if (request === this.routeRequest) this.routeEstimate = estimate
      } catch (e) {
        if (request === this.routeRequest) this.routeError = e?.message || "Could not size that route."
      }
    },
    async saveRoute() {
      const route = this.selectedRoute
      if (!route || this.busy) return
      this.busy = "route"
      try {
        await api.addAutoOfflineRoute({
          name: this.routeTo?.name || "Saved route",
          origin_name: this.routeFrom?.name || "Current location",
          points: route.points, distance_m: route.distance_m, duration_s: route.duration_s,
        })
        showSnackbar(`${this.routeTo?.name || "The route"} will be available offline once it downloads.`)
        this.clearRoutes()
        this.routeTo = null
        await this.refresh()
      } catch (e) {
        showSnackbar(e?.message || "Could not save the route.", "error")
      } finally {
        this.busy = ""
      }
    },

    // ── saved items ────────────────────────────────────────────────────────
    status(item) { return itemStatus(item, Date.now() / 1000) },
    describe(item) {
      if (item.kind === "route") {
        const parts = [item.origin_name ? `From ${item.origin_name}` : "", formatDistance(item.distance_m, this.metric), item.duration_s ? formatDuration(item.duration_s) : ""]
        return parts.filter(Boolean).join(" · ")
      }
      const detail = (this.summary?.presets || []).find((p) => p.max_zoom === item.max_zoom)?.detail || ""
      return [`${radiusLabel(item.radius_km, this.metric)} around`, detail].filter(Boolean).join(" · ")
    },
    async act(item, action) {
      this.busy = item.id
      try {
        await api.autoOfflineAction(item.id, action)
        await this.refresh()
      } catch (e) {
        showSnackbar(e?.message || "That didn't work.", "error")
      } finally {
        this.busy = ""
      }
    },
    async remove(item) {
      if (!window.confirm(`Delete the offline map for ${item.name}? Tiles another saved area or route still uses are kept.`)) return
      this.busy = item.id
      try {
        await api.deleteAutoOffline(item.id)
        await this.refresh()
      } catch (e) {
        showSnackbar(e?.message || "Could not delete it.", "error")
      } finally {
        this.busy = ""
      }
    },
    formatBytes,
    formatDistance,
    formatDuration,
    presetDetailDescription,
    radiusLabel,
  },
  template: `
    <div style="display:grid; gap:10px;">
      <GxNotice v-if="error" tone="danger" :text="error" style="margin:0;" />

      <p style="margin:0; color:var(--text-muted); font-size:var(--fs-sm);">
        Saved areas and active routes are kept offline for the comma and car screen, and refresh on Wi-Fi every {{ summary?.refresh_days || 90 }} days.
      </p>

      <div style="display:flex; flex-wrap:wrap; align-items:center; gap:6px 14px; font-size:var(--fs-sm); color:var(--text-muted); padding:2px 0;">
        <span>Downloader: <strong style="color:var(--text);">{{ downloaderLabel }}</strong></span>
        <span style="opacity:0.3;">•</span>
        <span>Storage: <strong style="color:var(--text);">{{ storageLabel }}</strong></span>
        <span style="opacity:0.3;">•</span>
        <span>Current Route: <strong style="color:var(--text);">{{ summary ? activeRouteLabel : 'Checking...' }}</strong></span>
      </div>

      <GxNotice v-if="notice" :tone="notice.tone" :text="notice.text" style="margin:0;" />
      <GxNotice v-if="loaded && summary && !token" tone="warn"
        text="Add a Mapbox public key in the App Keys tab to search places and draw the map." style="margin:0;" />

      <section class="gx-card" style="margin:0;">
        <div class="gx-section__header" style="min-height:42px; padding:8px var(--sp-3);">
          <i class="bi bi-signpost-split" style="font-size:1.15rem;"></i>
          <span class="gx-section__title" style="font-size:var(--fs-base);">Make a Route Available Offline</span>
        </div>
        <div style="padding:var(--sp-2) var(--sp-3) var(--sp-3); display:grid; gap:8px;">
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="min-width:42px; font-size:var(--fs-sm); font-weight:var(--fw-medium); color:var(--text-muted);">From</span>
            <PlaceSearch :token="token" :position="position" :selected="routeFrom"
              :placeholder="position ? 'Current location' : 'Search where you start'"
              @select="routeFrom = $event; clearRoutes()" @clear="routeFrom = null; clearRoutes()" />
          </div>
          <div style="display:flex; align-items:center; gap:8px;">
            <span style="min-width:42px; font-size:var(--fs-sm); font-weight:var(--fw-medium); color:var(--text-muted);">To</span>
            <PlaceSearch :token="token" :position="position" :selected="routeTo" placeholder="Search your destination"
              @select="routeTo = $event; clearRoutes()" @clear="routeTo = null; clearRoutes()" />
          </div>
          <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:2px;">
            <button type="button" class="gx-btn gx-btn--tonal" style="min-height:36px; padding:0 14px;" :disabled="!routeTo || findingRoute || !token" @click="findRoutes">
              <i class="bi bi-search"></i> {{ findingRoute ? 'Finding routes...' : 'Find Routes' }}
            </button>
          </div>
          <div v-if="routes.length" style="display:grid; gap:4px; margin-top:4px;">
            <label v-for="(route, index) in routes" :key="route.id" class="gx-row" style="border:none; min-height:0; padding:6px 8px; cursor:pointer; gap:8px; border-radius:var(--radius-md);">
              <input type="radio" name="auto-offline-route" :checked="index === routeIndex" @change="selectRoute(index)" style="accent-color:var(--primary);" />
              <div class="gx-row__info">
                <span class="gx-row__label">{{ route.label }}</span>
                <span class="gx-row__desc" style="margin-top:2px;">{{ formatDuration(route.duration_s) }} · {{ formatDistance(route.distance_m, metric) }}</span>
              </div>
            </label>
            <div style="display:flex; align-items:center; flex-wrap:wrap; gap:8px; padding:4px 0;">
              <span class="gx-row__desc" style="margin:0; flex:1;">
                <template v-if="routeEstimate">About {{ formatBytes(routeEstimate.bytes) }} · {{ routeEstimate.tiles.toLocaleString() }} tiles,
                  with street detail at every turn and the destination.</template>
                <template v-else-if="!routeError">Sizing the route...</template>
              </span>
              <button type="button" class="gx-btn" style="min-height:36px; padding:0 14px;" :disabled="!routeEstimate || !routeEstimate.fits || busy === 'route'" @click="saveRoute">
                <i class="bi bi-download"></i> {{ busy === 'route' ? 'Saving...' : 'Make Available Offline' }}
              </button>
            </div>
            <GxNotice v-if="routeError" tone="danger" :text="routeError" style="margin:0;" />
            <button v-if="routeError" type="button" class="gx-btn gx-btn--tonal" @click="estimateRoute">Retry sizing</button>
            <GxNotice v-if="routeEstimate && !routeEstimate.fits" tone="warn" text="Not enough offline storage left for this route. Delete an area or route first." style="margin:0;" />
          </div>
        </div>
      </section>

      <section v-if="token" class="gx-card" style="margin:0; overflow:hidden;">
        <div style="padding:10px; display:grid; gap:6px;">
          <label><input type="checkbox" v-model="coverageEnabled" /> Show downloaded tiles</label>
          <label v-if="coverageEnabled">Tile detail:
            <select v-model.number="coverageZoom" class="gx-field" aria-label="Downloaded tile zoom level">
              <option v-for="zoom in 19" :key="zoom - 1" :value="zoom - 1">Zoom {{ zoom - 1 }}{{ ({13: ' · Regional', 14: ' · Road', 15: ' · City', 16: ' · Street'})[zoom - 1] || '' }}</option>
            </select>
          </label>
          <div v-if="coverageEnabled" class="gx-row__desc">
            <span style="color:#34c778;">■ Saved offline</span> · <span style="color:#4096ff;">■ Temporary cache</span> · Purple outlines: requested areas.
            Coverage is for the selected zoom only; the background map is online.
          </div>
          <div v-if="coverageEnabled" class="gx-row__desc" role="status">
            <template v-if="coverageError">{{ coverageError }}</template>
            <template v-else-if="coverage">{{ coverage.tiles.length.toLocaleString() }} downloaded tiles in view at zoom {{ coverage.zoom }}. {{ coverage.truncated ? 'Display limit reached; zoom the map in to see all tiles.' : '' }}</template>
            <template v-else>Checking downloaded tiles...</template>
          </div>
        </div>
        <div ref="map" style="height:280px; border-radius:var(--radius-md); overflow:hidden;"></div>
        <div v-if="pickOnMap" class="gx-note" style="margin:6px var(--sp-3);">Tap the map where the area should be centred.</div>
      </section>

      <section class="gx-card" style="margin:0;">
        <div class="gx-section__header" style="min-height:42px; padding:8px var(--sp-3);">
          <i class="bi bi-bounding-box-circles" style="font-size:1.15rem;"></i>
          <span class="gx-section__title" style="font-size:var(--fs-base);">Save an Area</span>
        </div>
        <div style="padding:var(--sp-2) var(--sp-3) var(--sp-3); display:grid; gap:8px;">
          <p style="margin:0; color:var(--text-muted); font-size:var(--fs-xs);">
            Download a coverage radius around a point. Smaller radii include full street-level turns and residential roads, while larger radii cover broader highway networks within device storage.
          </p>
          <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
            <button type="button" class="gx-btn gx-btn--tonal" style="min-height:36px; padding:0 12px;" :disabled="!position" @click="useCurrentLocation"><i class="bi bi-crosshair"></i> Current Location</button>
            <button type="button" class="gx-btn gx-btn--tonal" style="min-height:36px; padding:0 12px;" :disabled="!mapReady" @click="pickOnMap = !pickOnMap">
              <i class="bi bi-pin-map"></i> {{ pickOnMap ? 'Cancel' : 'Pick on Map' }}
            </button>
            <PlaceSearch :token="token" :position="position" placeholder="Or search a city or place" @select="chooseAreaPoint($event)" />
          </div>
          <template v-if="areaPoint">
            <div class="gx-row__label" style="margin-top:4px;">Around {{ areaName() }}</div>
            <div v-if="areaLoading" class="gx-row__desc" role="status">Sizing up the area...</div>
            <GxNotice v-if="areaError" tone="danger" :text="areaError" style="margin:0;" />
            <button v-if="areaError" type="button" class="gx-btn gx-btn--tonal" @click="chooseAreaPoint(areaPoint, false)">Retry sizing</button>
            <div v-for="preset in areaPresets || []" :key="preset.radius_km" class="gx-row" style="border-top:none; min-height:0; padding:8px 0; flex-wrap:wrap; gap:8px;">
              <div class="gx-row__info">
                <span class="gx-row__label" style="font-weight:var(--fw-bold);">{{ radiusLabel(preset.radius_km, metric) }} radius · {{ preset.detail }}</span>
                <span class="gx-row__desc" style="margin-top:2px;">{{ presetDetailDescription(preset, metric) }}</span>
                <div style="font-size:var(--fs-xs); color:var(--text-muted); margin-top:2px;">
                  {{ preset.fits ? 'Est. ' + formatBytes(preset.bytes) + ' · ' + preset.tiles.toLocaleString() + ' tiles' : 'Too large for available offline storage' }}
                </div>
              </div>
              <button type="button" class="gx-btn gx-btn--tonal" style="min-height:34px; padding:0 12px; align-self:center;" :disabled="!preset.fits || !!busy" @click="saveArea(preset)">{{ busy === 'area' ? 'Adding to downloads...' : 'Download' }}</button>
            </div>
          </template>
        </div>
      </section>

      <section class="gx-card" style="margin:0;">
        <div class="gx-section__header" style="min-height:42px; padding:8px var(--sp-3);">
          <i class="bi bi-hdd-stack" style="font-size:1.15rem;"></i>
          <span class="gx-section__title" style="font-size:var(--fs-base);">Downloads &amp; Saved Maps</span>
          <span class="gx-section__count">{{ items.length }}</span>
        </div>
        <div style="padding:var(--sp-2) var(--sp-3) var(--sp-3); display:grid; gap:6px;">
          <div v-if="!items.length" class="gx-empty" style="margin:0; padding:var(--sp-3);">Nothing saved yet. Save an area around home or make a trip's route available offline.</div>
          <div v-for="item in items" :key="item.id" class="gx-card" style="margin:0; padding:8px 10px; background:rgba(255, 255, 255, 0.02); border:1px solid var(--glass-border, rgba(127,127,127,.15)); border-radius:var(--radius-md);">
            <div style="display:flex; gap:8px; align-items:flex-start;">
              <i class="bi" :class="item.kind === 'route' ? 'bi-signpost-split' : 'bi-bounding-box-circles'" style="color:var(--primary); margin-top:2px; font-size:1.1rem;"></i>
              <div style="flex:1; min-width:0;">
                <div style="display:flex; justify-content:space-between; align-items:baseline; gap:8px; flex-wrap:wrap;">
                  <button type="button" style="border:none; background:transparent; color:inherit; padding:0; cursor:pointer; text-align:left; font:inherit;" @click="focus(item)">
                    <span class="gx-row__label" style="font-weight:var(--fw-bold); font-size:var(--fs-sm); overflow-wrap:anywhere;">{{ item.name }}</span>
                  </button>
                  <div style="display:flex; gap:6px; flex-wrap:wrap; margin-left:auto;">
                    <button v-if="status(item).canDownloadNow" type="button" class="gx-btn gx-btn--tonal" style="min-height:28px; padding:0 8px; font-size:var(--fs-xs);" :disabled="busy === item.id" @click="act(item, 'download_now')"
                      title="Use the comma's current connection even though it's metered">Download Now</button>
                    <button v-if="status(item).canUpdate" type="button" class="gx-btn gx-btn--text" style="min-height:28px; padding:0 6px; font-size:var(--fs-xs);" :disabled="busy === item.id" @click="act(item, 'update')">Update</button>
                    <button v-if="status(item).canDelete" type="button" class="gx-btn gx-btn--text" style="min-height:28px; padding:0 6px; font-size:var(--fs-xs); color:var(--error);" :disabled="busy === item.id" @click="remove(item)">Delete</button>
                  </div>
                </div>
                <div class="gx-row__desc" style="margin-top:2px; font-size:var(--fs-xs);">{{ describe(item) }}</div>
                <div class="gx-row__desc" style="margin-top:2px; font-size:var(--fs-xs);" :style="status(item).tone === 'danger' ? 'color:var(--error);' : ''">{{ status(item).text }}</div>
                <div v-if="status(item).progress !== null" role="progressbar" :aria-label="item.name + ' download'" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="Math.floor(status(item).progress * 100)" style="height:4px; border-radius:2px; background:var(--glass-border, rgba(127,127,127,.2)); overflow:hidden; margin-top:4px;">
                  <div :style="'width:' + Math.round(status(item).progress * 100) + '%;height:100%;background:var(--primary);transition:width .3s;'"></div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </section>
    </div>
  `,
}
