import { api, showSnackbar } from "../api.js"

const VIEWS = [
  { value: "split", label: "Map + Driving", desc: "The driving view and the navigation map side by side." },
  { value: "driving", label: "Driving View", desc: "The StarPilot driving view fills the car screen." },
  { value: "map", label: "Map Only", desc: "The map fills the screen, with the status border, your speed and alerts on top." },
]

export const AndroidAutoCarScreenPanel = {
  name: "AndroidAutoCarScreenPanel",
  props: {
    isMetric: { type: Boolean, default: false },
  },
  data() {
    return { settings: null, loading: false, error: "", saving: false, views: VIEWS }
  },
  created() { this.load() },
  computed: {
    showsDriving() { return this.settings && this.settings.onroad_view !== "map" },
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
            this.settings = (await api.getCarScreen()).settings
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
  },
  template: `
    <div style="padding: var(--sp-3); display:grid; gap:10px;">
      <div v-if="loading" class="gx-loading">Loading...</div>
      <div v-else-if="error" class="gx-row" style="border:none; gap:10px; flex-wrap:wrap;">
        <div class="gx-row__info">
          <span class="gx-row__label">Could not load the layout</span>
          <span class="gx-row__desc">{{ error }}</span>
        </div>
        <button type="button" class="gx-btn gx-btn--tonal" @click="load"><i class="bi bi-arrow-clockwise"></i> Retry</button>
      </div>
      <template v-else-if="settings">
        <div class="gx-row__label">While Driving</div>
        <label v-for="view in views" :key="view.value" class="gx-row" style="border:none; cursor:pointer; gap:10px;">
          <input type="radio" name="car-screen-view" :checked="settings.onroad_view === view.value" :disabled="saving"
            @change="update({ onroad_view: view.value })" style="accent-color:var(--primary); width:18px; height:18px; flex:none;" />
          <div class="gx-row__info">
            <span class="gx-row__label">{{ view.label }}</span>
            <span class="gx-row__desc">{{ view.desc }}</span>
          </div>
        </label>

        <div class="gx-row" style="border-top:1px solid var(--glass-border, rgba(127,127,127,.2)); gap:10px; flex-wrap:wrap;" :style="isSplit ? '' : 'opacity:.5;'">
          <div class="gx-row__info">
            <span class="gx-row__label">Map Side</span>
            <span class="gx-row__desc">Which half of the car screen the map takes.</span>
          </div>
          <div style="display:flex; gap:6px;">
            <button v-for="side in ['left', 'right']" :key="side" type="button" class="gx-btn"
              :class="settings.map_side === side ? '' : 'gx-btn--tonal'" :disabled="!isSplit || saving"
              @click="update({ map_side: side })">{{ side === 'left' ? 'Left' : 'Right' }}</button>
          </div>
        </div>

        <label class="gx-row" style="gap:10px; cursor:pointer;" :style="showsDriving ? '' : 'opacity:.5;'">
          <div class="gx-row__info">
            <span class="gx-row__label">Show Road Camera</span>
            <span class="gx-row__desc">Off keeps the coloured border, speeds, status and alerts over black, and saves the video work.</span>
          </div>
          <input type="checkbox" :checked="settings.camera" :disabled="!showsDriving || saving"
            @change="update({ camera: $event.target.checked })" style="accent-color:var(--primary); width:20px; height:20px; flex:none;" />
        </label>

        <label class="gx-row" style="gap:10px; cursor:pointer;">
          <div class="gx-row__info">
            <span class="gx-row__label">Show Blind Spot Monitors</span>
            <span class="gx-row__desc">Show blind-spot borders, adjacent-lane warnings, and configured side-camera previews on the car screen.</span>
          </div>
          <input type="checkbox" :checked="blindSpotEnabled" :disabled="saving"
            @change="update({ blind_spot_monitors: $event.target.checked })" style="accent-color:var(--primary); width:20px; height:20px; flex:none;" />
        </label>

        <label class="gx-row" style="gap:10px; flex-wrap:wrap;" :style="blindSpotEnabled ? '' : 'opacity:.5;'">
          <div class="gx-row__info">
            <span class="gx-row__label">Blind Spot Minimum Speed</span>
            <span class="gx-row__desc">Show the monitors only at or above this speed. Set 0 to show them at every speed.</span>
          </div>
          <div style="display:flex; align-items:center; gap:6px;">
            <input class="gx-field" type="number" min="0" :max="isMetric ? 200 : 125" step="1"
              :value="blindSpotMinSpeed" :disabled="!blindSpotEnabled || saving" @change="updateBlindSpotSpeed" style="width:90px;" />
            <span class="gx-row__value">{{ speedUnit }}</span>
          </div>
        </label>

        <p class="gx-row__desc" style="margin:0;">
          Changes reach the car screen within a second while it's connected, or apply the next time it connects.
          While driving, the small <strong>•••</strong> button in the bottom-left corner of the car screen opens the home screen,
          starts navigation to a favorite and returns to the drive.
        </p>
      </template>
    </div>
  `,
}
