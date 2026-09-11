import { BluetoothPanel } from "../components/BluetoothPanel.js"
import { WheelControls } from "../components/WheelControls.js"
import { UnidenPanel } from "../components/UnidenPanel.js"
import { GalaxySection } from "../components/GalaxySection.js"
import { GalaxyTabs } from "../components/GalaxyTabs.js"
import { useTabRouting } from "../composables.js"

const TABS = {
  bluetooth: "Bluetooth",
  controllers: "Controllers",
  uniden: "Uniden R4",
}

export const Bluetooth = {
  name: "Bluetooth",
  components: { BluetoothPanel, WheelControls, UnidenPanel, GalaxySection, GalaxyTabs },
  setup() {
    return useTabRouting("/bluetooth", { bluetooth: "bluetooth", controllers: "controllers", uniden: "uniden" })
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

      <template v-else-if="tab === 'controllers'">
        <WheelControls />
      </template>

      <template v-else-if="tab === 'uniden'">
        <UnidenPanel />
      </template>
    </div>
  `,
}
