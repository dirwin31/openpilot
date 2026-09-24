import { BluetoothPanel } from "../components/BluetoothPanel.js"
import { AndroidAutoIdentityPanel } from "../components/AndroidAutoIdentityPanel.js?v=aa-identity-3"
import { WheelControls } from "../components/WheelControls.js"
import { GalaxySection } from "../components/GalaxySection.js"
import { GalaxyTabs } from "../components/GalaxyTabs.js"
import { useTabRouting } from "../composables.js"

const TABS = {
  bluetooth: "Bluetooth",
  controllers: "Controllers",
  androidAuto: "Android Auto",
}

export const Bluetooth = {
  name: "Bluetooth",
  components: { BluetoothPanel, AndroidAutoIdentityPanel, WheelControls, GalaxySection, GalaxyTabs },
  setup() {
    return useTabRouting("/bluetooth", { bluetooth: "bluetooth", controllers: "controllers", androidAuto: "android-auto" })
  },
  data() { return { TABS } },
  template: `
    <div class="gx-view">
      <h2 style="margin-top:0;">Bluetooth</h2>
      <GalaxyTabs :items="TABS" :active="tab" @select="selectTab" />

      <template v-if="tab === 'bluetooth'">
        <GalaxySection title="Bluetooth Devices" icon="bi-bluetooth" :collapsible="false">
          <BluetoothPanel />
        </GalaxySection>
      </template>

      <template v-else-if="tab === 'androidAuto'">
        <GalaxySection title="Android Auto Identity" icon="bi-key" :collapsible="false">
          <AndroidAutoIdentityPanel />
        </GalaxySection>
      </template>

      <template v-else>
        <WheelControls />
      </template>
    </div>
  `,
}
