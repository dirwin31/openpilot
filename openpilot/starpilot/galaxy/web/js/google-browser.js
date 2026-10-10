// Only rendered frames and bounded human input cross Galaxy's existing session.
// Cookies, CDP, and the Google profile never enter this component.
export const GoogleBrowserPanel = {
  props: { email: String, remember: Boolean, parked: Boolean, available: Boolean, existing: Object },
  emits: ['started', 'complete'],
  data: () => ({ browser: null, frame: '', origin: '', error: '', starting: false, sending: false }),
  computed: {
    active() { return ['starting', 'signing_in', 'finishing'].includes(this.browser?.state) },
  },
  mounted() {
    this.alive = true
    this.inputChain = Promise.resolve()
    this.pendingInputs = 0
    this.visibility = () => {
      if (document.hidden) { clearTimeout(this.timer); this.frame = '' }
      else if (this.active) this.poll()
    }
    document.addEventListener('visibilitychange', this.visibility)
    if (this.existing?.id && ['starting', 'signing_in', 'finishing'].includes(this.existing.state)) {
      this.browser = this.existing
      this.$emit('started')
      this.poll()
    }
  },
  beforeUnmount() {
    this.alive = false
    document.removeEventListener('visibilitychange', this.visibility)
    clearTimeout(this.timer)
    this.stop()
    this.frame = ''
  },
  methods: {
    async request(payload, keepalive = false) {
      const response = await fetch('./api/android-auto/google-browser', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload), cache: 'no-store', credentials: 'same-origin', keepalive,
        signal: AbortSignal.timeout(12000),
      })
      const data = await response.json()
      if (!response.ok) throw Object.assign(new Error(data.error || 'Google sign-in is unavailable'), { status: response.status })
      return data
    },
    async start() {
      if (this.starting || this.active || !this.parked || !this.available || !this.email?.trim()) return
      this.starting = true
      this.error = ''
      try {
        const browser = await this.request({ operation: 'start', email: this.email.trim(), remember: this.remember })
        this.browser = browser
        if (!this.alive) { await this.stop(); return }
        this.$emit('started')
        this.poll()
      } catch (error) { if (this.alive) this.error = error.message }
      finally { this.starting = false }
    },
    async poll() {
      if (!this.alive || !this.active || document.hidden || this.polling) return
      this.polling = true
      clearTimeout(this.timer)
      const id = this.browser.id
      try {
        const data = await this.request({ operation: 'frame', id })
        if (!this.alive || !this.active || id !== this.browser?.id) return
        this.browser = data
        this.frame = data.frame ? `data:image/jpeg;base64,${data.frame}` : ''
        this.origin = data.origin || ''
        if (data.state === 'complete') this.$emit('complete')
        if (['failed', 'closed'].includes(data.state)) this.error = data.message || ''
      } catch (error) {
        if (this.alive) {
          this.error = error.message
          this.frame = ''
          if ([401, 403, 409, 503].includes(error.status)) this.browser = { ...this.browser, state: 'closed' }
        }
      } finally {
        this.polling = false
        if (this.alive && this.active && !document.hidden) this.timer = setTimeout(() => this.poll(), 500)
      }
    },
    async stop() {
      clearTimeout(this.timer)
      this.frame = ''
      const id = this.browser?.id
      if (!id || !this.active) return
      this.browser = { ...this.browser, state: 'closed' }
      try { await this.request({ operation: 'stop', id }, true) }
      catch (error) { if (this.alive) this.error = error.message }
    },
    input(input) {
      if (!this.alive || this.browser?.state !== 'signing_in') return
      if (this.pendingInputs >= 32) { this.error = 'Typing is catching up. Wait a moment, then check the selected field.'; return }
      const id = this.browser.id
      this.pendingInputs++
      this.inputChain = this.inputChain.then(async () => {
        if (this.alive && this.browser?.id === id && this.active) await this.request({ operation: 'input', id, input })
      }).catch((error) => { if (this.alive) this.error = error.message }).finally(() => { this.pendingInputs-- })
    },
    point(event) {
      const box = event.currentTarget.getBoundingClientRect()
      return { x: Math.max(0, Math.min(479, (event.clientX - box.left) * 480 / box.width)),
        y: Math.max(0, Math.min(719, (event.clientY - box.top) * 720 / box.height)) }
    },
    down(event) { this.pointer = this.point(event); event.currentTarget.setPointerCapture?.(event.pointerId) },
    up(event) {
      if (!this.pointer) return
      const point = this.point(event)
      const delta = this.pointer.y - point.y
      this.input(Math.abs(delta) > 12 ? { kind: 'scroll', ...this.pointer, delta: Math.max(-720, Math.min(720, delta)) } : { kind: 'tap', ...point })
      this.pointer = null
      if (Math.abs(delta) <= 12) this.$refs.keyboard?.focus({ preventScroll: true })
    },
    wheel(event) { this.input({ kind: 'scroll', ...this.point(event), delta: Math.max(-720, Math.min(720, event.deltaY)) }) },
    type(event) {
      if (event.isComposing) return
      const text = event.target.value
      event.target.value = ''
      if (text) this.input({ kind: 'text', text: text.slice(0, 1024) })
    },
    key(event) {
      if (event.isComposing) return
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
        event.preventDefault()
        this.input({ kind: 'key', key: 'SelectAll' })
        return
      }
      if (['Enter', 'Tab', 'Backspace', 'Delete', 'Escape', 'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) {
        event.preventDefault()
        this.input({ kind: 'key', key: event.key })
      }
    },
  },
  template: `
    <section class="gx-google-browser" aria-label="Google sign-in on your comma">
      <template v-if="!active">
        <button type="button" class="gx-btn" :disabled="starting || !parked || !available || !email?.trim()" @click="start">{{ starting ? 'Starting browser…' : 'Connect Google' }}</button>
        <p v-if="!parked" class="gx-note">Put the car in Park to open Google sign-in.</p>
        <p v-else-if="!available" class="gx-note">{{ existing?.message || 'The on-device browser is not included in this build. Use the token option below or Manual File Upload.' }}</p>
      </template>
      <template v-else>
        <p role="status">{{ browser.state === 'starting' ? 'Starting the browser on your comma…' : browser.state === 'finishing' ? 'Sign-in received. Closing the browser…' : 'Sign in below using the same Google account email.' }}</p>
        <div v-if="frame" class="gx-google-browser__view">
          <p class="gx-google-browser__origin">{{ origin }}</p>
          <img :src="frame" alt="Interactive Google sign-in browser" draggable="false"
            @pointerdown.prevent="down" @pointerup.prevent="up" @pointercancel="pointer = null" @wheel.prevent="wheel" />
          <label class="gx-google-browser__keyboard">Keyboard
            <input ref="keyboard" type="password" class="gx-field" placeholder="Tap a field above, then type here"
              autocomplete="off" autocapitalize="off" spellcheck="false" aria-label="Type into the selected Google field"
              @input="type" @compositionend="type" @keydown="key" />
          </label>
          <div class="gx-actions">
            <button type="button" class="gx-btn gx-btn--tonal" @click="input({kind: 'key', key: 'SelectAll'})">Select all</button>
            <button type="button" class="gx-btn gx-btn--tonal" @click="input({kind: 'key', key: 'Backspace'})">Delete</button>
            <button type="button" class="gx-btn gx-btn--tonal" @click="input({kind: 'key', key: 'Tab'})">Next field</button>
            <button type="button" class="gx-btn gx-btn--tonal" @click="input({kind: 'key', key: 'Enter'})">Enter</button>
          </div>
        </div>
        <p class="gx-note">You can switch apps to approve Google sign-in; return within 90 seconds. If your phone’s passkey is unavailable here, choose another verification method in Google.</p>
        <button type="button" class="gx-btn gx-btn--tonal" @click="stop">Cancel sign-in</button>
      </template>
      <p v-if="error" class="gx-aa-bad" role="alert">{{ error }}</p>
    </section>`,
}
