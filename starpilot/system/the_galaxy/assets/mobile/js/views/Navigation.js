import { NavigationDestinationPanel } from "../components/NavigationDestinationPanel.js?v=nav-destination-6"
import { MapsPanel } from "../components/MapsPanel.js?v=offline-merge-1"
import { NavigationKeysPanel } from "../components/NavigationKeysPanel.js"
import { SpeedLimitsPanel } from "../components/SpeedLimitsPanel.js"
import { AndroidAutoOfflinePanel } from "../components/AndroidAutoOfflinePanel.js?v=offline-merge-1"
import { AndroidAutoIdentityPanel } from "../components/AndroidAutoIdentityPanel.js?v=aa-identity-3"
import { AndroidAutoCarScreenPanel } from "../components/AndroidAutoCarScreenPanel.js?v=car-screen-1"
import { GalaxySection } from "../components/GalaxySection.js"
import { GalaxyTabs } from "../components/GalaxyTabs.js"
import { useTabRouting } from "../composables.js"
import { navigate } from "../store.js"

const TABS = {
  nav: "Destination",
  maps: "Offline Maps",
  keys: "App Keys",
  speeds: "Speed Limits",
  auto: "Android Auto",
}

export const Navigation = {
  name: "Navigation",
  components: {
    NavigationDestinationPanel, MapsPanel, NavigationKeysPanel, SpeedLimitsPanel, GalaxyTabs,
    AndroidAutoOfflinePanel, AndroidAutoIdentityPanel, AndroidAutoCarScreenPanel, GalaxySection,
  },
  data() { return { TABS } },
  methods: {
    openOfflineMaps() { navigate("/navigation/maps") },
  },
  setup() {
    return useTabRouting("/navigation", {
      nav: "", maps: "maps", keys: "keys", speeds: "speeds", auto: "auto",
    })
  },
  template: `
    <template v-if="tab === 'nav'">
      <div class="gx-navigation-view">
        <NavigationDestinationPanel />
        <div class="gx-navigation-tabs"><GalaxyTabs :items="TABS" :active="tab" @select="selectTab" /></div>
      </div>
    </template>
    <div v-else class="gx-view">
      <h2 style="margin-top:0;">Navigation & Maps</h2>
      <GalaxyTabs :items="TABS" :active="tab" @select="selectTab" />
      <template v-if="tab === 'maps'">
        <div style="display:grid; gap:12px;">
          <p style="margin:0; color:var(--text-muted);">
            Two kinds of offline data, downloaded separately: <strong>Map Display</strong> is the map you see on the comma and
            car screen; <strong>Speed Limit &amp; Curve Data</strong> is road data openpilot uses for speed limits and curves.
          </p>
          <AndroidAutoOfflinePanel />
          <MapsPanel />
        </div>
      </template>
      <template v-if="tab === 'keys'"><NavigationKeysPanel /></template>
      <template v-if="tab === 'speeds'"><SpeedLimitsPanel /></template>
      <template v-if="tab === 'auto'">
        <div style="display:grid; gap:12px;">
          <GalaxySection title="Car Screen" icon="bi-display">
            <AndroidAutoCarScreenPanel />
          </GalaxySection>
          <p style="margin:0; color:var(--text-muted);">
            Offline maps for the car screen are under <a href="/navigation/maps" @click.prevent="openOfflineMaps">Navigation › Offline Maps</a>.
          </p>
          <GalaxySection title="Android Auto Identity" icon="bi-key">
            <AndroidAutoIdentityPanel />
          </GalaxySection>
        </div>
      </template>
    </div>
  `,
}
