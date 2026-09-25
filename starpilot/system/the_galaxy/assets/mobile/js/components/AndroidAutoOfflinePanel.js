import { api, showSnackbar } from "../api.js"
import { usePolling } from "../composables.js"
import { GxNotice } from "./GxNotice.js"
import {
  directionsToRoutes,
  formatBytes,
  formatDistance,
  formatDuration,
  itemBounds,
  itemStatus,
  itemsToGeoJson,
  radiusLabel,
  serviceNotice,
} from "./auto_offline_helpers.js?v=auto-offline-1"
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
      pickOnMap: false,
      areaPoint: null,
      areaPresets: null,
      routeFrom: null,
      routeTo: null,
      routes: [],
      routeIndex: 0,
      routeEstimate: null,
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
  },
  methods: {
    async refresh() {
      try {
        this.summary = await api.getAutoOffline()
        this.error = ""
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
          for (const [id, data] of [["auto-areas", EMPTY], ["auto-routes", EMPTY], ["auto-candidates", EMPTY]]) {
            this.map.addSource(id, { type: "geojson", data })
          }
          this.map.addLayer({ id: "auto-areas-fill", type: "fill", source: "auto-areas", paint: { "fill-color": "#9d72ff", "fill-opacity": 0.14 } })
          this.map.addLayer({ id: "auto-areas-line", type: "line", source: "auto-areas", paint: { "line-color": "#9d72ff", "line-width": 2 } })
          this.map.addLayer({ id: "auto-routes-line", type: "line", source: "auto-routes", paint: { "line-color": "#4096ff", "line-width": 4 } })
          this.map.addLayer({
            id: "auto-candidates-line", type: "line", source: "auto-candidates",
            paint: { "line-color": ["case", ["get", "selected"], "#34c778", "#8a93a6"], "line-width": ["case", ["get", "selected"], 5, 3] },
          })
          this.mapReady = true
          this.drawSaved()
          this.drawCandidates()
        })
        this.map.on("click", (event) => {
          if (!this.pickOnMap) return
          this.pickOnMap = false
          this.chooseAreaPoint({ latitude: event.lngLat.lat, longitude: event.lngLat.lng, name: "" })
        })
      } catch (e) {
        this.error = e?.message || "Could not load the map."
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
    showPoint(point) {
      if (!this.map || !window.mapboxgl) return
      this.marker?.remove()
      this.marker = new window.mapboxgl.Marker({ color: "#9d72ff" }).setLngLat([point.longitude, point.latitude]).addTo(this.map)
      this.map.flyTo({ center: [point.longitude, point.latitude], zoom: 9 })
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
    async chooseAreaPoint(point) {
      this.areaPoint = point
      this.areaPresets = null
      this.showPoint(point)
      try {
        const payload = await api.estimateAutoOffline({ latitude: point.latitude, longitude: point.longitude })
        if (this.areaPoint === point) this.areaPresets = payload?.presets || []
      } catch (e) {
        showSnackbar(e?.message || "Could not size that area.", "error")
      }
      if (!point.name && this.token) {
        try {
          const payload = await api.mapboxReverseCity(point.latitude, point.longitude, this.token)
          const name = payload?.features?.[0]?.properties?.name
          if (this.areaPoint === point && name) this.areaPoint = { ...point, name }
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
      const route = this.selectedRoute
      this.routeEstimate = null
      if (!route) return
      try {
        const estimate = await api.estimateAutoOffline({ points: route.points })
        if (route === this.selectedRoute) this.routeEstimate = estimate
      } catch (e) {
        showSnackbar(e?.message || "Could not size that route.", "error")
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
    radiusLabel,
  },
  template: `
    <div style="display:grid; gap:12px;">
      <GxNotice v-if="error" tone="danger" :text="error" style="margin:0;" />

      <section class="gx-card">
        <div class="gx-section__header">
          <i class="bi bi-cloud-arrow-down"></i>
          <span class="gx-section__title">Offline Maps for Android Auto</span>
        </div>
        <div style="padding: var(--sp-3); display:grid; gap:6px;">
          <p style="margin:0; color:var(--text-muted);">
            Save areas and routes so the car's navigation map keeps working without a signal. The comma downloads them on Wi-Fi,
            keeps them until you delete them, and refreshes them every {{ summary?.refresh_days || 90 }} days.
            The route you're navigating is also saved automatically.
          </p>
          <div class="gx-row" style="border-top:none; min-height:0; padding:4px 0;">
            <span class="gx-row__label">Downloader</span>
            <span class="gx-row__value">{{ downloaderLabel }}</span>
          </div>
          <div class="gx-row" style="border-top:none; min-height:0; padding:4px 0;">
            <span class="gx-row__label">Storage Used</span>
            <span class="gx-row__value">{{ storageLabel }}</span>
          </div>
          <div class="gx-row" style="border-top:none; min-height:0; padding:4px 0;">
            <span class="gx-row__label">Current Route</span>
            <span class="gx-row__value">{{ summary ? activeRouteLabel : 'Checking...' }}</span>
          </div>
          <GxNotice v-if="notice" :tone="notice.tone" :text="notice.text" style="margin:8px 0 0;" />
          <GxNotice v-if="loaded && summary && !token" tone="warn"
            text="Add a Mapbox public key in the App Keys tab to search places and draw the map." style="margin:8px 0 0;" />
        </div>
      </section>

      <section v-if="token" class="gx-card">
        <div ref="map" style="height:340px; border-radius:var(--radius-md); overflow:hidden;"></div>
        <div v-if="pickOnMap" class="gx-note" style="margin:8px var(--sp-3);">Tap the map where the area should be centred.</div>
      </section>

      <section class="gx-card">
        <div class="gx-section__header">
          <i class="bi bi-signpost-split"></i>
          <span class="gx-section__title">Make a Route Available Offline</span>
        </div>
        <div style="padding: var(--sp-3); display:grid; gap:8px;">
          <div class="gx-row" style="border-top:none; flex-wrap:wrap; gap:8px;">
            <span class="gx-row__label" style="min-width:48px;">From</span>
            <PlaceSearch :token="token" :position="position" :selected="routeFrom"
              :placeholder="position ? 'Current location' : 'Search where you start'"
              @select="routeFrom = $event; clearRoutes()" @clear="routeFrom = null; clearRoutes()" />
          </div>
          <div class="gx-row" style="border-top:none; flex-wrap:wrap; gap:8px;">
            <span class="gx-row__label" style="min-width:48px;">To</span>
            <PlaceSearch :token="token" :position="position" :selected="routeTo" placeholder="Search your destination"
              @select="routeTo = $event; clearRoutes()" @clear="routeTo = null; clearRoutes()" />
          </div>
          <div style="display:flex; gap:8px; flex-wrap:wrap;">
            <button type="button" class="gx-btn gx-btn--tonal" :disabled="!routeTo || findingRoute || !token" @click="findRoutes">
              <i class="bi bi-search"></i> {{ findingRoute ? 'Finding routes...' : 'Find Routes' }}
            </button>
          </div>
          <div v-if="routes.length" style="display:grid; gap:4px;">
            <label v-for="(route, index) in routes" :key="route.id" class="gx-row" style="border:none; cursor:pointer; gap:8px;">
              <input type="radio" name="auto-offline-route" :checked="index === routeIndex" @change="selectRoute(index)" style="accent-color:var(--primary);" />
              <div class="gx-row__info">
                <span class="gx-row__label">{{ route.label }}</span>
                <span class="gx-row__desc">{{ formatDuration(route.duration_s) }} · {{ formatDistance(route.distance_m, metric) }}</span>
              </div>
            </label>
            <div class="gx-row" style="border-top:none; flex-wrap:wrap; gap:8px;">
              <span class="gx-row__desc" style="margin:0; flex:1;">
                <template v-if="routeEstimate">About {{ formatBytes(routeEstimate.bytes) }} · {{ routeEstimate.tiles.toLocaleString() }} tiles,
                  with street detail at every turn and the destination.</template>
                <template v-else>Sizing the route...</template>
              </span>
              <button type="button" class="gx-btn" :disabled="!routeEstimate || !routeEstimate.fits || busy === 'route'" @click="saveRoute">
                <i class="bi bi-download"></i> {{ busy === 'route' ? 'Saving...' : 'Make Available Offline' }}
              </button>
            </div>
            <GxNotice v-if="routeEstimate && !routeEstimate.fits" tone="warn" text="Not enough offline storage left for this route. Delete an area or route first." style="margin:0;" />
          </div>
        </div>
      </section>

      <section class="gx-card">
        <div class="gx-section__header">
          <i class="bi bi-bounding-box-circles"></i>
          <span class="gx-section__title">Save an Area</span>
        </div>
        <div style="padding: var(--sp-3); display:grid; gap:8px;">
          <div style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
            <button type="button" class="gx-btn gx-btn--tonal" :disabled="!position" @click="useCurrentLocation"><i class="bi bi-crosshair"></i> Current Location</button>
            <button type="button" class="gx-btn gx-btn--tonal" :disabled="!mapReady" @click="pickOnMap = !pickOnMap">
              <i class="bi bi-pin-map"></i> {{ pickOnMap ? 'Cancel' : 'Pick on Map' }}
            </button>
            <PlaceSearch :token="token" :position="position" placeholder="Or search a city or place" @select="chooseAreaPoint($event)" />
          </div>
          <template v-if="areaPoint">
            <div class="gx-row__label" style="margin-top:4px;">Around {{ areaName() }}</div>
            <div v-if="!areaPresets" class="gx-row__desc">Sizing up the area...</div>
            <div v-for="preset in areaPresets || []" :key="preset.radius_km" class="gx-row" style="border-top:none; flex-wrap:wrap; gap:8px;">
              <div class="gx-row__info">
                <span class="gx-row__label">{{ radiusLabel(preset.radius_km, metric) }} · {{ preset.detail }}</span>
                <span class="gx-row__desc">{{ preset.fits ? 'About ' + formatBytes(preset.bytes) + ' · ' + preset.tiles.toLocaleString() + ' tiles' : 'Too large for the offline storage left' }}</span>
              </div>
              <button type="button" class="gx-btn gx-btn--tonal" :disabled="!preset.fits || busy === 'area'" @click="saveArea(preset)">Save</button>
            </div>
          </template>
        </div>
      </section>

      <section class="gx-card">
        <div class="gx-section__header">
          <i class="bi bi-hdd-stack"></i>
          <span class="gx-section__title">Saved Offline Maps</span>
          <span class="gx-section__count">{{ items.length }}</span>
        </div>
        <div style="padding: var(--sp-3); display:grid; gap:8px;">
          <div v-if="!items.length" class="gx-empty" style="margin:0;">Nothing saved yet. Save an area around home or make a trip's route available offline.</div>
          <div v-for="item in items" :key="item.id" class="gx-card" style="margin:0; padding:var(--sp-2) var(--sp-3);">
            <div style="display:flex; gap:8px; align-items:flex-start;">
              <i class="bi" :class="item.kind === 'route' ? 'bi-signpost-split' : 'bi-bounding-box-circles'" style="color:var(--primary); margin-top:3px;"></i>
              <div style="flex:1; min-width:0;">
                <button type="button" style="border:none; background:transparent; color:inherit; padding:0; cursor:pointer; text-align:left; font:inherit;" @click="focus(item)">
                  <span class="gx-row__label" style="overflow-wrap:anywhere;">{{ item.name }}</span>
                </button>
                <div class="gx-row__desc">{{ describe(item) }}</div>
                <div class="gx-row__desc" :style="status(item).tone === 'danger' ? 'color:var(--error);' : ''">{{ status(item).text }}</div>
                <div v-if="status(item).progress !== null" style="height:6px; border-radius:3px; background:var(--glass-border, rgba(127,127,127,.2)); overflow:hidden; margin-top:6px;">
                  <div :style="'width:' + Math.round(status(item).progress * 100) + '%;height:100%;background:var(--primary);transition:width .3s;'"></div>
                </div>
              </div>
            </div>
            <div style="display:flex; gap:8px; flex-wrap:wrap; justify-content:flex-end; margin-top:8px;">
              <button v-if="status(item).canDownloadNow" type="button" class="gx-btn gx-btn--tonal" :disabled="busy === item.id" @click="act(item, 'download_now')"
                title="Use the comma's current connection even though it's metered">Download Now</button>
              <button v-if="status(item).canUpdate" type="button" class="gx-btn gx-btn--text" :disabled="busy === item.id" @click="act(item, 'update')">Update</button>
              <button v-if="status(item).canDelete" type="button" class="gx-btn gx-btn--text" style="color:var(--error);" :disabled="busy === item.id" @click="remove(item)">Delete</button>
            </div>
          </div>
        </div>
      </section>
    </div>
  `,
}
