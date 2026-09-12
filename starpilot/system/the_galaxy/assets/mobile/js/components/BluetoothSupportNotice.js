import { GxNotice } from "./GxNotice.js"

// Why this browser cannot use Bluetooth, for a bluetoothPlatform() of "ios",
// "firefox" or "unsupported". Shared by the Phone tab and Telematics.
export const BluetoothSupportNotice = {
  name: "BluetoothSupportNotice",
  components: { GxNotice },
  props: { platform: String, secureUrl: String },
  template: `
    <GxNotice v-if="platform === 'ios'" tone="info" icon="bi-apple" title="Bluetooth in the browser is not supported on iPhone at this time :(">
      Safari and every other iPhone and iPad browser lack Web Bluetooth, so a phone cannot pair with the comma from this page.
    </GxNotice>

    <div v-else-if="platform === 'firefox'" class="telematics-gate">
      <GxNotice tone="info" icon="bi-browser-firefox" title="Bluetooth Pairing in the Browser is not supported in Firefox">
        Firefox does not provide Web Bluetooth. Open this page in Chrome on Android to pair:
      </GxNotice>
      <p class="telematics-gate__url"><code>{{ secureUrl }}</code></p>
    </div>

    <GxNotice v-else tone="info" icon="bi-phone" title="Chrome on Android required">
      This browser does not provide Web Bluetooth. Open this page in Chrome on Android.
    </GxNotice>
  `,
}
