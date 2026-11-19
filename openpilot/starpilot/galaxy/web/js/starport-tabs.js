export const StarPortTabs = {
  props: {
    active: { type: String, default: 'car' },
    go: { type: Function, default: path => { location.hash = path } },
  },
  data: () => ({ tabs: [
    { id: 'car', label: 'Car Screen', icon: 'bi-car-front', path: '/starport' },
    { id: 'phone', label: 'Phone Screen', icon: 'bi-phone-landscape', path: '/starport/phone' },
  ] }),
  template: `<nav class="gx-settings-tabs" aria-label="StarPort sections">
    <button v-for="tab in tabs" :key="tab.id" type="button" class="gx-chip"
      :aria-pressed="active === tab.id" @click="go(tab.path)"><i :class="'bi ' + tab.icon" aria-hidden="true"></i> {{ tab.label }}</button>
  </nav>`,
}
