import { api, showSnackbar } from "../api.js"

const VIEWS = [
  { value: "split", label: "Map + Driving", desc: "The driving view and the navigation map side by side." },
  { value: "driving", label: "Driving View", desc: "The StarPilot driving view fills the car screen." },
  { value: "map", label: "Map Only", desc: "The map fills the screen, with the status border, your speed and alerts on top." },
]

const MAP_ORIENTATIONS = [
  { value: "north_up", label: "North Up", desc: "Keeps street names upright and rotates only the vehicle marker." },
  { value: "heading_up", label: "Heading Up", desc: "Keeps the direction of travel toward the top; raster street names rotate with the map." },
]

export const AndroidAutoCarScreenPanel = {
  name: "AndroidAutoCarScreenPanel",
  props: {
    isMetric: { type: Boolean, default: false },
  },
  data() {
    return { settings: null, statusMetrics: [], tab: "layout", loading: false, error: "", saving: false, views: VIEWS, mapOrientations: MAP_ORIENTATIONS }
  },
  created() { this.load() },
  computed: {
    showsDriving() { return this.settings && this.settings.onroad_view !== "map" },
    showsMap() { return this.settings && this.settings.onroad_view !== "driving" },
    isSplit() { return this.settings && this.settings.onroad_view === "split" },
    blindSpotEnabled() { return this.settings?.blind_spot_monitors !== false },
    speedFactor() { return this.isMetric ? 3.6 : 2.2369362921 },
    speedUnit() { return this.isMetric ? "km/h" : "mph" },
    blindSpotMinSpeed() { return Math.round((this.settings?.blind_spot_min_speed_ms || 0) * this.speedFactor) },
  },
  methods: {
    async load() {
      if (this.loading) return
      this.loading = true
      this.error = ""
      try {
        // The panel mounts as soon as the master toggle changes locally. Its first
        // request can beat the toggle's PUT to the device and receive a transient 403.
        for (let attempt = 0; attempt < 3; attempt++) {
          try {
            const response = await api.getCarScreen()
            this.settings = response.settings
            this.statusMetrics = response.status_metrics
            return
          } catch (e) {
            if (attempt === 2) throw e
            await new Promise(resolve => setTimeout(resolve, attempt === 0 ? 250 : 750))
          }
        }
      } catch (e) {
        this.error = e?.message || "Could not read the car screen settings."
        showSnackbar(this.error, "error")
      } finally {
        this.loading = false
      }
    },
    async update(change) {
      if (!this.settings || this.saving) return
      const previous = this.settings
      this.settings = { ...this.settings, ...change }
      this.saving = true
      try {
        this.settings = (await api.setCarScreen(change)).settings
      } catch (e) {
        this.settings = previous
        showSnackbar(e?.message || "Could not save the car screen settings.", "error")
      } finally {
        this.saving = false
      }
    },
    updateBlindSpotSpeed(event) {
      const raw = String(event.target.value ?? "").trim()
      const value = Number(raw)
      const maximum = this.isMetric ? 200 : 125
      if (raw && Number.isFinite(value)) {
        this.update({ blind_spot_min_speed_ms: Math.min(maximum, Math.max(0, value)) / this.speedFactor })
      }
    },
    updateStatusSlot(index, event) {
      const status_slots = [...this.settings.status_slots]
      status_slots[index] = event.target.value
      this.update({ status_slots })
    },
  },
  template: `
    <div class="gx-car-display">
      <p class="gx-row__desc">Car display only. These are the same settings as Car Display on your car screen.</p>
      <div class="gx-car-display__tabs" role="tablist" aria-label="Car Display">
        <button type="button" role="tab" class="gx-btn" :class="tab === 'layout' ? '' : 'gx-btn--tonal'"
          :aria-selected="tab === 'layout'" @click="tab = 'layout'">Layout</button>
        <button type="button" role="tab" class="gx-btn" :class="tab === 'widgets' ? '' : 'gx-btn--tonal'"
          :aria-selected="tab === 'widgets'" @click="tab = 'widgets'">Status Widgets</button>
      </div>
      <div v-if="loading" class="gx-loading">Loading...</div>
      <div v-else-if="error" class="gx-row">
        <div class="gx-row__info"><span class="gx-row__label">Could not load the layout</span><span class="gx-row__desc">{{ error }}</span></div>
        <button type="button" class="gx-btn gx-btn--tonal" @click="load">Retry</button>
      </div>
      <template v-else-if="settings">
        <template v-if="tab === 'layout'">
          <div class="gx-row__label">While Driving</div>
          <div class="gx-car-display__layouts">
            <label v-for="view in views" :key="view.value" class="gx-car-display__layout" :class="{ 'is-selected': settings.onroad_view === view.value }">
              <input type="radio" name="car-screen-view" :checked="settings.onroad_view === view.value" :disabled="saving" @change="update({ onroad_view: view.value })" />
              <span class="gx-car-display__preview" :class="'gx-car-display__preview--' + view.value" aria-hidden="true"><span></span><span></span></span>
              <span class="gx-row__label">{{ view.label }}</span><span class="gx-row__desc">{{ view.desc }}</span>
            </label>
          </div>
          <div v-if="isSplit" class="gx-row">
            <div class="gx-row__info"><span class="gx-row__label">Map Side</span><span class="gx-row__desc">Which half of the car display shows the map.</span></div>
            <div class="gx-car-display__tabs"><button v-for="side in ['left', 'right']" :key="side" type="button" class="gx-btn"
              :class="settings.map_side === side ? '' : 'gx-btn--tonal'" :aria-pressed="settings.map_side === side" :disabled="saving"
              @click="update({ map_side: side })">{{ side === 'left' ? 'Left' : 'Right' }}</button></div>
          </div>
          <div v-if="showsMap" class="gx-row">
            <div class="gx-row__info"><span class="gx-row__label">Map Orientation</span><span class="gx-row__desc">Keep north or your direction of travel at the top.</span></div>
            <div class="gx-car-display__tabs"><button v-for="orientation in mapOrientations" :key="orientation.value" type="button" class="gx-btn"
              :class="settings.map_orientation === orientation.value ? '' : 'gx-btn--tonal'" :aria-pressed="settings.map_orientation === orientation.value" :disabled="saving"
              @click="update({ map_orientation: orientation.value })">{{ orientation.label }}</button></div>
          </div>
          <label v-if="showsDriving" class="gx-row">
            <div class="gx-row__info"><span class="gx-row__label">Show Road Camera</span><span class="gx-row__desc">Keep speed, driving status and alerts visible when the camera is hidden.</span></div>
            <span class="gx-switch"><input type="checkbox" :checked="settings.camera" :disabled="saving" @change="update({ camera: $event.target.checked })" />
              <span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></span>
          </label>
          <div class="gx-row__label gx-car-display__heading">Blind Spots</div>
          <label class="gx-row">
            <div class="gx-row__info"><span class="gx-row__label">Show Blind Spot Monitors</span><span class="gx-row__desc">Show blind-spot borders, lane warnings and configured side cameras.</span></div>
            <span class="gx-switch"><input type="checkbox" :checked="blindSpotEnabled" :disabled="saving" @change="update({ blind_spot_monitors: $event.target.checked })" />
              <span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></span>
          </label>
          <label v-if="blindSpotEnabled" class="gx-row">
            <div class="gx-row__info"><span class="gx-row__label">Blind Spot Minimum Speed</span><span class="gx-row__desc">Set 0 to show monitors at every speed.</span></div>
            <div class="gx-car-display__tabs"><input class="gx-field" type="number" min="0" :max="isMetric ? 200 : 125" step="1"
              :value="blindSpotMinSpeed" :disabled="saving" @change="updateBlindSpotSpeed" style="width:90px;" /><span>{{ speedUnit }}</span></div>
          </label>
          <div class="gx-row__label gx-car-display__heading">Comma Display</div>
          <label class="gx-row">
            <div class="gx-row__info"><span class="gx-row__label">Turn Off Comma Display</span><span class="gx-row__desc">After the screen timeout while Android Auto is connected. Tap the comma to wake it; connection loss, warnings and critical alerts also wake it. Applies to the independent car view on comma four.</span></div>
            <span class="gx-switch"><input type="checkbox" :checked="settings.sleep_device_screen" :disabled="saving" @change="update({ sleep_device_screen: $event.target.checked })" />
              <span class="gx-switch__track"></span><span class="gx-switch__thumb"></span></span>
          </label>
        </template>
        <template v-else>
          <div class="gx-row__label">Status Column</div>
          <p class="gx-row__desc">Choose the seven stats on the right side of the driving view. A slot can also show the StarPilot logo or stay blank.</p>
          <label v-for="(metric, index) in settings.status_slots" :key="index" class="gx-row">
            <span class="gx-row__label">Slot {{ index + 1 }}</span>
            <GalaxySelect class="gx-field gx-car-display__metric" :value="metric" :disabled="saving"
              :aria-label="'Status slot ' + (index + 1)" @change="updateStatusSlot(index, $event)">
              <option v-for="option in statusMetrics" :key="option.value" :value="option.value">{{ option.label }}</option>
            </GalaxySelect>
          </label>
        </template>
        <p class="gx-row__desc">Changes apply within a second while connected, or the next time Android Auto connects.</p>
      </template>
    </div>
  `,
}
