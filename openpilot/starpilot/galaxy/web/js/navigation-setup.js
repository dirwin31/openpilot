// Pieces of the navigation setup page: a card-style radio group and the Mapbox/Google comparison.
export const ChoiceGroup = {
  name: "ChoiceGroup",
  props: { label: { type: String, required: true }, hint: { type: String, default: "" }, modelValue: { type: String, default: "" },
    options: { type: Array, required: true }, disabled: { type: Boolean, default: false } },
  emits: ["update:modelValue"],
  methods: {
    pick(event, option) {
      event.target.checked = this.modelValue === option.value
      if (!option.disabled && !this.disabled) this.$emit("update:modelValue", option.value)
    },
  },
  template: `
    <fieldset class="gx-choice" :disabled="disabled">
      <legend class="gx-choice__legend">{{ label }}</legend>
      <p v-if="hint" class="gx-choice__hint">{{ hint }}</p>
      <div class="gx-choice__grid">
        <label v-for="option in options" :key="option.value" class="gx-choice__card"
          :class="{ 'gx-choice__card--on': modelValue === option.value, 'gx-choice__card--off': option.disabled }">
          <input type="radio" class="gx-sr-only" :name="label" :value="option.value" :checked="modelValue === option.value"
            :disabled="option.disabled || disabled" @change="pick($event, option)">
          <span class="gx-choice__title"><strong>{{ option.label }}</strong>
            <span v-if="option.badge" class="gx-starport-pill gx-starport-pill--soon">{{ option.badge }}</span>
            <i v-else-if="modelValue === option.value" class="bi bi-check-circle-fill" aria-hidden="true"></i></span>
          <span class="gx-choice__line">{{ option.detail }}</span>
          <span v-if="option.limit" class="gx-choice__limit">{{ option.limit }}</span>
        </label>
      </div>
    </fieldset>`,
}

// What each service does and where it stops. Mapbox always does search and offline roads.
export const COMPARE_ROWS = [
  ["Place search and addresses", "Yes", "No, Mapbox does this"],
  ["Offline road maps", "Yes", "No, Google tiles can't be saved"],
  ["Lane arrows on the turn card", "Yes", "No"],
  ["Traffic in route times", "Yes", "Yes, with a correction every 7½ minutes"],
  ["Map picture", "Road lines", "Street map with traffic colors"],
  ["Needs", "Mapbox token", "Google key with billing on"],
  ["Free monthly allowance", "Set by Mapbox; counted for this comma", "100,000 map tiles, 10,000 routes, 5,000 traffic routes"],
  ["When the allowance runs out", "Typing suggestions, address lookups and offline downloads pause until the 1st", "The comma stops at 99% and uses Mapbox until it resets"],
]

export const SourceCompare = {
  name: "SourceCompare",
  data() { return { rows: COMPARE_ROWS } },
  template: `
    <details class="gx-starport-wiki"><summary>Compare Mapbox and Google</summary>
      <div class="gx-compare" role="table" aria-label="Mapbox compared with Google">
        <div class="gx-compare__row gx-compare__row--head" role="row"><span role="columnheader">What you get</span><span role="columnheader">Mapbox</span><span role="columnheader">Google</span></div>
        <div v-for="row in rows" :key="row[0]" class="gx-compare__row" role="row">
          <span role="rowheader">{{ row[0] }}</span><span role="cell">{{ row[1] }}</span><span role="cell">{{ row[2] }}</span></div>
      </div>
      <p class="gx-note">Counts cover this comma only. Mapbox and Google bill everything that uses your key, including other apps.</p>
    </details>`,
}
