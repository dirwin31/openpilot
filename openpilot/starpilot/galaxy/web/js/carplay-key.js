import { requestJson } from "./startup.js"
import { GxNotice } from "./notice.js"
import { GxDialog } from "./dialog.js"
import { formatDate, uploadPackage, uploadTimeout } from "./starport.js"

const STAGES = ["reading", "extracting", "verifying", "installing", "done"]
const CHECKS = [
  { stage: "reading", codes: ["NOT_PACKAGE", "CORRUPT", "NO_CODE"], label: "CarLink app opened" },
  { stage: "extracting", codes: ["WRONG_APP", "UNSUPPORTED_VERSION", "NOT_AVAILABLE"], label: "CarPlay key found" },
  { stage: "verifying", codes: ["INVALID"], label: "Key checked" },
]

export function validCarPlaySetup(value) {
  return typeof value?.key?.installed === "boolean" && value.import && typeof value.import.state === "string" &&
    Number.isSafeInteger(value.maxUploadBytes) && value.maxUploadBytes > 0 &&
    typeof value.releasesUrl === "string" && value.releasesUrl.startsWith("https://github.com/")
}

export function keyInfo(key, locale = undefined) {
  if (key?.installed) {
    const until = formatDate(key.expires, undefined, locale)
    return { level: "ready", label: until ? `Ready · Certificate valid until ${until}` : "Ready" }
  }
  if (key?.error) return { level: "broken", label: "Needs reinstalling" }
  return { level: "none", label: "Not set up" }
}

export function carplayPreflight(file) {
  const name = typeof file?.name === "string" ? file.name : ""
  if (/\.(xapk|apkm|ipa)$/i.test(name)) return "Choose the CarLink .apk from the GitHub release, not a bundle."
  if (/\.sha256$/i.test(name)) return "That’s the checksum file. Choose CarLink-x.y.z.apk from the release."
  if (name && !/\.(apk|zip)$/i.test(name)) return "That’s not an Android package. Choose the CarLink .apk you downloaded."
  return ""
}

export function keyProblem(job) {
  if (job?.state !== "failed") return ""
  switch (job.code) {
    case "NOT_PACKAGE": case "CORRUPT": return "This file looks incomplete or damaged. Download it again and wait for the download to finish."
    case "NO_CODE": case "WRONG_APP": return "This isn’t the CarLink app. Download CarLink-x.y.z.apk from the CarLink GitHub release."
    case "UNSUPPORTED_VERSION": return job.error || "This CarLink release stores its key differently. Try another release."
    case "INVALID": return "The key in this release doesn’t work. Try another CarLink release."
    case "NOT_AVAILABLE": return job.error || "Reading the CarPlay key is not available in this build yet."
    case "CANCELLED": return "Galaxy sign-in changed. Sign in again and retry."
    default: return job.error || "Something went wrong. Try again."
  }
}

export function keyChecks(job) {
  const at = job?.state === "done" ? STAGES.length : STAGES.indexOf(job?.stage)
  const ends = CHECKS.map((_, index) => STAGES.indexOf(index + 1 < CHECKS.length ? CHECKS[index + 1].stage : "done"))
  let failed = -1
  if (job?.state === "failed") {
    failed = CHECKS.findIndex(check => check.codes.includes(job.code))
    if (failed < 0) failed = Math.max(0, ends.findIndex(end => at < end))
  }
  return CHECKS.map((check, index) => ({
    label: check.label,
    status: failed >= 0 ? (index < failed ? "done" : index === failed ? "failed" : "pending") :
      at >= ends[index] ? "done" : at >= STAGES.indexOf(check.stage) ? "active" : "pending",
  }))
}

export const CarPlayKey = {
  components: { GxNotice, GxDialog },
  props: { unauthorized: { type: Function, required: true } },
  data: () => ({ setup: null, busy: false, error: "", timer: null, active: false, installOpen: false, removeOpen: false,
    packageFile: null, uploadProgress: null, installAttempted: false, installBaseline: null }),
  computed: {
    info() { return keyInfo(this.setup?.key) },
    hasKey() { return !!(this.setup?.key.installed || this.setup?.key.error) },
    running() { return this.setup?.import?.state === "running" },
    packageError() {
      if (!this.packageFile) return ""
      const preflight = carplayPreflight(this.packageFile)
      if (preflight) return preflight
      if (!Number.isSafeInteger(this.packageFile.size) || this.packageFile.size <= 0) return "The selected file is empty or its size cannot be read."
      if (this.setup && this.packageFile.size > this.setup.maxUploadBytes) return "The selected file is too large to be CarLink."
      return ""
    },
    uploadReason() {
      if (!this.packageFile) return "Choose the CarLink APK."
      if (this.packageError) return this.packageError
      if (this.running) return "Wait for the current key check to finish."
      return ""
    },
    installState() {
      const job = this.setup?.import
      const current = this.installAttempted && job && job.started !== this.installBaseline
      if (this.uploadProgress || this.installAttempted && this.busy && !current) return "uploading"
      if (!current || job.state === "idle") return "choose"
      if (job.state === "running") return "checking"
      if (job.state === "done" && this.setup.key.installed) return "done"
      return job.state === "failed" ? "failed" : "checking"
    },
    uploadPercent() {
      const { loaded, total } = this.uploadProgress || {}
      return total ? Math.min(100, Math.floor(loaded * 100 / total)) : 0
    },
    checks() { return keyChecks(this.setup?.import) },
    problem() { return keyProblem(this.setup?.import) },
  },
  mounted() { this.active = true; this.refresh() },
  beforeUnmount() { this.active = false; clearTimeout(this.timer) },
  methods: {
    adopt(value) { if (!validCarPlaySetup(value)) throw new Error("Invalid CarPlay setup status"); this.setup = value },
    fail(error) {
      if (error.status === 401) { this.active = false; clearTimeout(this.timer); this.unauthorized(); return }
      this.error = error.message
    },
    async refresh() {
      if (!this.active) return
      clearTimeout(this.timer)
      try {
        if (!this.busy) { this.adopt(await requestJson("./api/carplay/setup")); this.error = "" }
      } catch (error) { this.fail(error) } finally {
        if (this.active) this.timer = setTimeout(() => this.refresh(), this.running ? 1000 : 5000)
      }
    },
    openInstall() { Object.assign(this, { installOpen: true, installAttempted: false, packageFile: null, error: "" }) },
    closeInstall() { this.installOpen = false },
    chooseAgain() { Object.assign(this, { installAttempted: false, packageFile: null }) },
    choosePackage(event) { this.packageFile = event.target.files?.[0] || null },
    async upload() {
      if (this.busy || this.uploadReason) return
      const file = this.packageFile
      const timeout = uploadTimeout(file.size)
      Object.assign(this, { busy: true, error: "", installAttempted: true, installBaseline: this.setup.import?.started ?? null,
        uploadProgress: { loaded: 0, total: file.size } })
      try {
        this.adopt(await requestJson("./api/carplay/upload", { timeout, request: { method: "POST",
          headers: { "Content-Type": "application/octet-stream" }, body: file },
          fetcher: (path, options) => uploadPackage(path, { ...options, timeout }, progress => { if (this.active) this.uploadProgress = progress }) }))
      } catch (error) { this.fail(error) } finally {
        Object.assign(this, { busy: false, uploadProgress: null })
        this.refresh()
      }
    },
    async removeKey() {
      this.removeOpen = false
      if (this.busy || this.running) return
      this.busy = true; this.error = ""
      try {
        this.adopt(await requestJson("./api/carplay/key", { timeout: 15000, request: { method: "DELETE" } }))
      } catch (error) { this.fail(error) } finally { this.busy = false }
    },
  },
  template: `
    <div class="gx-row" :aria-busy="busy || undefined">
      <div class="gx-row__info"><span class="gx-row__label">CarPlay key</span>
        <span class="gx-row__desc">iPhones need a CarPlay key, read on this comma from your own copy of the CarLink app.</span></div>
    </div>
    <p v-if="!setup && !error" class="gx-note">Loading CarPlay key…</p>
    <div v-if="setup" class="gx-starport-status">
      <span class="gx-starport-pill" :class="'gx-starport-pill--' + info.level" role="status">{{ info.label }}</span>
      <button v-if="hasKey" type="button" class="gx-icon-btn gx-recordings__danger" aria-label="Delete CarPlay Key" :disabled="busy || running" title="Delete CarPlay Key" @click="removeOpen = true"><i class="bi bi-trash3"></i></button>
      <div class="gx-actions gx-starport-status__actions">
        <button v-if="!setup.key.installed" class="gx-btn" :disabled="busy" @click="openInstall">{{ setup.key.error ? 'Reinstall' : 'Add CarPlay key' }}</button>
        <button v-else class="gx-btn gx-btn--tonal" :disabled="busy" @click="openInstall">Update key</button>
      </div>
    </div>
    <p v-if="running && !installOpen" class="gx-note" role="status">Checking CarLink…</p>
    <GxNotice v-if="error && !installOpen" tone="danger">{{ error }}</GxNotice>

    <GxDialog v-if="removeOpen && setup" :inert="busy" labelledby="gx-carplay-remove-title" describedby="gx-carplay-remove-body" alert @close="removeOpen = false">
      <div><h3 id="gx-carplay-remove-title">Remove the CarPlay key?</h3>
        <p id="gx-carplay-remove-body">This deletes the CarPlay key from this comma. iPhones won’t connect until you add it again.</p></div>
      <div class="gx-settings__controls gx-actions">
        <button type="button" class="gx-btn gx-btn--tonal" @click="removeOpen = false">Cancel</button>
        <button type="button" class="gx-btn gx-btn--danger" @click="removeKey">Remove</button>
      </div>
    </GxDialog>

    <GxDialog v-if="installOpen && setup" labelledby="gx-carplay-install-title" class="gx-starport-sheet__dialog" @close="closeInstall">
      <div class="gx-starport-sheet__toolbar">
        <button type="button" class="gx-icon-btn" aria-label="Close" @click="closeInstall"><i class="bi bi-x-lg"></i></button>
      </div>
      <div class="gx-starport-sheet__body">
        <div v-if="installState === 'done'" class="gx-message gx-starport-sheet__done">
          <div class="gx-starport-done-badge"><i class="bi bi-check-lg" aria-hidden="true"></i></div>
          <h3 id="gx-carplay-install-title">CarPlay key added</h3>
          <p class="gx-note">{{ info.label }}</p>
          <ul class="gx-starport-checks"><li v-for="check in checks" :key="check.label" :class="'gx-starport-check--' + check.status">{{ check.label }}</li></ul>
          <button class="gx-btn gx-starport-cta" @click="closeInstall">Done</button>
        </div>
        <div v-else class="gx-stack">
          <div class="gx-stack gx-starport-sheet__head">
            <h3 id="gx-carplay-install-title">{{ setup.key.installed ? 'Update the CarPlay Key' : 'Add the CarPlay Key' }}</h3>
            <p class="gx-note">One-time setup so iPhones accept your comma as a CarPlay screen.</p>
          </div>
          <GxNotice tone="warn">
            <p><strong>CarPlay may stop working after an iOS update.</strong> The key comes from CarLink, not from Apple. Apple can block it at any time, and an iOS update can break CarPlay until a newer CarLink release works again. Then update the key here with the newer release.</p>
            <p>This is a test feature with no support from Apple or the CarLink developer.</p>
          </GxNotice>
          <ol class="gx-steps gx-steps--compact">
            <li class="gx-step-card" :class="{ 'gx-starport-sheet__step--muted': installState !== 'choose' }">
              <div class="gx-step-card__head">
                <span class="gx-step__num gx-step-card__num">1</span>
                <div class="gx-step-card__title"><strong>Download CarLink</strong><small>The newest release on GitHub</small></div>
              </div>
              <div class="gx-step-card__body">
                <a class="gx-btn gx-btn--tonal" :href="setup.releasesUrl" target="_blank" rel="noopener noreferrer">
                  <i class="bi bi-box-arrow-up-right" aria-hidden="true"></i> Open CarLink Releases
                </a>
                <ol class="gx-note gx-starport-mirror-steps">
                  <li>Under <strong>Assets</strong>, download <strong>CarLink-x.y.z.apk</strong>.</li>
                  <li>Skip the <strong>.sha256</strong> file. You don’t need to install CarLink anywhere.</li>
                </ol>
              </div>
            </li>
            <li class="gx-step-card" :class="{ 'gx-starport-sheet__step--muted': installState !== 'choose' }">
              <div class="gx-step-card__head">
                <span class="gx-step__num gx-step-card__num">2</span>
                <div class="gx-step-card__title"><strong>Choose the downloaded file</strong><small>The CarLink .apk you just downloaded</small></div>
              </div>
              <div class="gx-step-card__body">
                <label class="gx-btn gx-btn--tonal gx-starport-file" :class="{ 'gx-starport-file--disabled': installState !== 'choose' }">
                  <input type="file" accept=".apk,.zip" aria-label="CarLink APK" :disabled="installState !== 'choose'" @change="choosePackage" />
                  <i class="bi bi-folder2-open"></i> {{ packageFile ? 'Choose a Different File' : 'Choose File' }}
                </label>
                <div v-if="packageFile && !packageError" class="gx-chip gx-starport-file-chip">
                  <i class="bi bi-file-earmark-zip"></i>
                  <span>{{ packageFile.name }}</span>
                  <small>{{ Math.ceil(packageFile.size / 1048576) }} MB</small>
                </div>
                <small v-if="packageError" class="gx-starport-bad" role="alert">{{ packageError }}</small>
              </div>
            </li>
            <li class="gx-step-card">
              <div class="gx-step-card__head">
                <span class="gx-step__num gx-step-card__num">3</span>
                <div class="gx-step-card__title"><strong>Install</strong><small>Send it to your comma and read the key</small></div>
              </div>
              <div class="gx-step-card__body">
                <button v-if="installState === 'choose'" class="gx-btn gx-starport-cta" :disabled="!!uploadReason || busy" @click="upload">Install</button>
                <div v-if="installState === 'uploading'" role="status" aria-live="polite" class="gx-stack">
                  <div class="gx-update-progress__track" role="progressbar" aria-label="Sending to your comma" aria-valuemin="0" aria-valuemax="100" :aria-valuenow="uploadPercent">
                    <div class="gx-update-progress__fill" :style="{ width: uploadPercent + '%' }"></div>
                  </div>
                  <small>Sending to your comma… {{ uploadPercent }}%</small>
                </div>
                <ul v-if="['checking', 'failed'].includes(installState)" class="gx-starport-checks" aria-live="polite">
                  <li v-for="check in checks" :key="check.label" :class="'gx-starport-check--' + check.status">{{ check.label }}</li>
                </ul>
                <GxNotice v-if="installState === 'failed'" tone="danger">
                  <p>{{ problem }}</p>
                  <template #actions><button class="gx-btn gx-btn--tonal" @click="chooseAgain">Choose Another File</button></template>
                </GxNotice>
                <GxNotice v-if="error && installState === 'choose' && installAttempted" tone="danger">{{ error }}</GxNotice>
              </div>
            </li>
          </ol>
          <div class="gx-stack gx-starport-sheet__footer">
            <small class="gx-note gx-starport-trademark">CarPlay and iOS are trademarks of Apple Inc. StarPilot is not affiliated with or endorsed by Apple or CarLink.</small>
            <small class="gx-note">The uploaded file is deleted after import. The key stays on this comma and is never shown or shared. Maximum size {{ Math.floor(setup.maxUploadBytes / 1048576) }} MB.</small>
          </div>
        </div>
      </div>
    </GxDialog>`,
}
