import { WikiHelp } from './wiki-help.js'
import { navigate } from "./router.js"
import { MenuTile } from "./menu-tile.js"

export const Tools = {
  name: "Tools",
  components: { WikiHelp, MenuTile },
  props: { tools: { type: Array, required: true }, mode: { type: String, required: true } },
  methods: {
    open(tool) { navigate(tool.path) },
    availability(tool) {
      if (this.mode === "local" || tool.availability === "always") return ""
      return tool.availability === "partial-preview" ? "Sample available" : "Connect to your device to use this tool"
    },
  },
  template: `
    <div class="gx-view">
      <header class="gx-settings__header gx-page-header"><div><h2>Tools</h2></div><WikiHelp /></header>
      <div class="gx-grid">
        <MenuTile v-for="tool in tools" :key="tool.path" :icon="tool.icon" :title="tool.name"
          :description="tool.description" :availability="availability(tool)" @select="open(tool)" />
      </div>
    </div>
  `,
}

export const ConnectionsApps = {
  components: { MenuTile },
  props: {
    tools: { type: Array, required: true },
    mode: { type: String, required: true },
    go: { type: Function, required: true },
  },
  computed: {
    visibleTools() { return this.tools.filter(tool => tool.visibility !== "authenticated" || this.mode === "local") },
  },
  template: `
    <div class="gx-view">
      <header class="gx-settings__header gx-page-header"><h2>Connections &amp; Apps</h2></header>
      <section class="gx-card gx-panel gx-stack" aria-label="Support">
        <div class="gx-section__header"><i class="bi bi-life-preserver" aria-hidden="true"></i><h3 class="gx-section__title">Support</h3></div>
        <p class="gx-note">Get driving help, collect connection logs, or report an issue.</p>
        <div class="gx-actions"><button type="button" class="gx-btn" @click="go('/logs/help')">Get help</button></div>
      </section>
      <div class="gx-grid">
        <MenuTile v-for="tool in visibleTools" :key="tool.path" :icon="tool.icon" :title="tool.name"
          :description="tool.description" :disabled="mode !== 'local'" :availability="mode !== 'local' ? 'Connect to your comma' : ''"
          @select="go(tool.path)" />
      </div>
    </div>`,
}
