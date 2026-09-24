import { api } from "../api.js"
import { usePolling } from "../composables.js"
import { GxNotice } from "./GxNotice.js"
import { acceptsFile, describeIdentity, describeJob, uploadLabel } from "./android_auto_identity_helpers.js?v=aa-identity-2"

function uploadWithProgress(file, onProgress) {
  return new Promise((resolve, reject) => {
    const form = new FormData()
    form.append("apk", file, file.name)
    const xhr = new XMLHttpRequest()
    xhr.open("POST", "/api/android_auto/identity/upload")
    xhr.upload.onprogress = (event) => { if (event.lengthComputable) onProgress(event.loaded / event.total) }
    xhr.onload = () => {
      let data = {}
      try { data = JSON.parse(xhr.responseText || "{}") } catch { data = {} }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data)
      else reject(new Error(data.error || `Upload failed (${xhr.status})`))
    }
    xhr.onerror = () => reject(new Error("Upload failed; check the connection to the comma"))
    xhr.send(form)
  })
}

export const AndroidAutoIdentityPanel = {
  name: "AndroidAutoIdentityPanel",
  components: { GxNotice },
  data() {
    return { status: null, error: "", busy: "", uploadProgress: 0, url: "", fileName: "" }
  },
  created() { this.poll = usePolling(() => this.refresh(), { interval: 2000 }); this.poll.start() },
  beforeUnmount() { this.poll?.destroy() },
  computed: {
    identity() { return describeIdentity(this.status) },
    job() { return describeJob(this.status?.job, Date.now() / 1000) },
    running() { return !!this.job?.running || !!this.busy },
    fileLabel() { return uploadLabel(this.status) },
  },
  methods: {
    async refresh() {
      try {
        this.status = await api.getAndroidAutoIdentity()
      } catch (e) {
        this.error = e?.message || "Could not read the Android Auto identity"
      }
    },
    chooseFile() { this.$refs.file?.click() },
    async onFile(event) {
      const file = event.target.files?.[0]
      event.target.value = ""
      if (!file) return
      if (!acceptsFile(file.name)) {
        this.error = "Choose the Android Auto .apk, .xapk or .apkm file."
        return
      }
      this.error = ""
      this.fileName = file.name
      this.busy = "upload"
      this.uploadProgress = 0
      try {
        await uploadWithProgress(file, (fraction) => { this.uploadProgress = fraction })
        await this.refresh()  // the upload's own reply is older than a poll that may have landed meanwhile
      } catch (e) {
        this.error = e?.message || "Upload failed"
      } finally {
        this.busy = ""
      }
    },
    async download() {
      const url = this.url.trim()
      if (!/^https?:\/\//i.test(url)) {
        this.error = "Enter an http(s) link to the APK or XAPK."
        return
      }
      this.error = ""
      this.busy = "download"
      try {
        await api.downloadAndroidAutoApk(url)
        await this.refresh()
      } catch (e) {
        this.error = e?.message || "Download failed"
      } finally {
        this.busy = ""
      }
    },
    async remove() {
      if (!window.confirm("Remove the Android Auto identity from this comma? Android Auto will stop working until you install it again.")) return
      this.busy = "remove"
      try {
        await api.removeAndroidAutoIdentity()
        await this.refresh()
      } catch (e) {
        this.error = e?.message || "Could not remove the identity"
      } finally {
        this.busy = ""
      }
    },
  },
  template: `
    <div style="padding: var(--sp-3);">
      <GxNotice :tone="identity.tone === 'ok' ? 'info' : identity.tone"
                :icon="identity.tone === 'ok' ? 'bi-check-circle-fill' : 'bi-key-fill'"
                :title="identity.title" :text="identity.text" style="margin:0 0 var(--sp-2);" />
      <div v-if="status && status.installed && status.subject" class="gx-row__desc" style="margin:0 0 var(--sp-2); overflow-wrap:anywhere;">
        {{ status.subject }}<span v-if="status.imported"> · installed {{ String(status.imported).slice(0, 10) }}</span>
      </div>

      <GxNotice v-if="job" :tone="job.tone === 'ok' ? 'info' : job.tone"
                :icon="job.running ? 'bi-hourglass-split' : job.tone === 'ok' ? 'bi-check-circle-fill' : 'bi-x-octagon-fill'"
                :text="job.text" style="margin:0 0 var(--sp-2);" />
      <GxNotice v-if="error" tone="danger" :text="error" style="margin:0 0 var(--sp-2);" />

      <h4 style="margin:12px 0 8px;">{{ status && (status.installed || status.expired) ? 'Renew' : 'Install' }}</h4>
      <ol style="margin:0 0 12px; padding-left:20px; color:var(--text-muted); line-height:1.5;">
        <li>On this phone or computer, download the <strong>Android Auto</strong> app
          (<code>{{ status?.knownGoodVersion || '17.6.663454-release' }}</code> is known to work) as an XAPK or APK from an APK mirror.
          Make sure it is the app itself, not a mirror's store installer.</li>
        <li>Choose the file below. The comma finds the identity in it, checks it against Google's root certificate, installs it and deletes the file.</li>
      </ol>

      <input ref="file" type="file" accept=".apk,.xapk,.apkm,application/vnd.android.package-archive" style="display:none;" @change="onFile" />
      <div style="display:flex; gap:8px; flex-wrap:wrap; margin:0 0 12px;">
        <button type="button" class="gx-btn" :disabled="running" @click="chooseFile">
          <i class="bi bi-upload"></i> {{ busy === 'upload' ? 'Uploading ' + Math.round(uploadProgress * 100) + '%' : fileLabel }}
        </button>
        <button type="button" class="gx-btn gx-btn--tonal" :disabled="!!busy" @click="refresh"><i class="bi bi-arrow-clockwise"></i> Refresh</button>
      </div>

      <details style="margin:0 0 12px;">
        <summary style="cursor:pointer; color:var(--text-muted);">Or have the comma download it from a link</summary>
        <div style="display:flex; gap:8px; margin-top:8px; flex-wrap:wrap;">
          <input v-model="url" class="gx-field" style="flex:1; min-width:200px;" type="url" inputmode="url" placeholder="https://…/android-auto.xapk" />
          <button type="button" class="gx-btn gx-btn--tonal" :disabled="running || !url.trim()" @click="download">Download</button>
        </div>
        <p class="gx-row__desc" style="margin-top:6px;">A direct link to the file, e.g. from your own cloud storage. Mirror pages that need a browser will not work here.</p>
      </details>

      <div v-if="status && (status.installed || status.expired || status.error)" style="display:flex; justify-content:flex-end;">
        <button type="button" class="gx-btn gx-btn--danger" :disabled="running" @click="remove"><i class="bi bi-trash"></i> Remove Identity</button>
      </div>
    </div>
  `,
}
