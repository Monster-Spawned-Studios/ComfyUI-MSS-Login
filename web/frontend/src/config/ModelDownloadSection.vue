<template>
  <div class="mss-cfg-section space-y-4">
    <div v-if="!hasPermission" class="mss-cfg-card text-center">
      <p>
        Your role does not have permission to view or manage model downloads.
      </p>
    </div>
    <template v-else>
      <div class="mss-cfg-card space-y-3">
        <h3 class="text-center text-base font-semibold">API keys</h3>
        <p class="mss-cfg-note text-center">
          Keys are encrypted per user. Host preference is not a secret.
        </p>
        <div class="grid gap-3 sm:grid-cols-2">
          <div>
            <label class="mss-cfg-label">CivitAI</label>
            <input
              v-model="civitaiKey"
              type="password"
              class="mss-cfg-input"
              :placeholder="
                sourcesWithKeys.includes('civitai')
                  ? '•••••••• (set)'
                  : 'API key'
              "
            />
          </div>
          <div>
            <label class="mss-cfg-label">HuggingFace</label>
            <input
              v-model="hfKey"
              type="password"
              class="mss-cfg-input"
              :placeholder="
                sourcesWithKeys.includes('huggingface')
                  ? '•••••••• (set)'
                  : 'API key'
              "
            />
          </div>
        </div>
        <div class="flex flex-wrap items-end justify-center gap-2">
          <div>
            <label class="mss-cfg-label">CivitAI host</label>
            <select v-model="civitaiHost" class="mss-cfg-input">
              <option value="civitai.com">civitai.com</option>
              <option value="civitai.red">civitai.red</option>
            </select>
          </div>
          <button type="button" class="mss-cfg-btn" @click="saveKeys">
            Save keys
          </button>
          <button type="button" class="mss-cfg-btn-ghost" @click="saveHost">
            Save host
          </button>
        </div>
        <p class="mss-cfg-note text-center">{{ keysStatus }}</p>
      </div>

      <div class="mss-cfg-card space-y-3">
        <h3 class="text-center text-base font-semibold">Browse / Search</h3>
        <div class="flex flex-wrap items-center justify-center gap-2">
          <select v-model="provider" class="mss-cfg-input max-w-[10rem]">
            <option value="civitai">CivitAI</option>
            <option value="huggingface">HuggingFace</option>
          </select>
          <input
            v-model="query"
            type="text"
            class="mss-cfg-input max-w-md flex-1"
            placeholder="Search models…"
            @keyup.enter="search"
          />
          <button type="button" class="mss-cfg-btn" @click="search">
            Search
          </button>
        </div>
        <p class="mss-cfg-note text-center">{{ browseStatus }}</p>
        <div class="max-h-64 space-y-2 overflow-y-auto text-left">
          <button
            v-for="item in results"
            :key="item.key"
            type="button"
            class="block w-full rounded-lg border border-zinc-800 px-3 py-2 text-left text-sm hover:border-[#9660fa]"
            @click="openResult(item)"
          >
            <span class="font-medium">{{ item.title }}</span>
            <span v-if="item.meta" class="ml-2 text-zinc-400">{{
              item.meta
            }}</span>
          </button>
        </div>
        <div
          v-if="detail"
          class="rounded-lg border border-zinc-700 bg-zinc-950 p-3 text-left text-sm"
        >
          <h4 class="mb-1 font-semibold">{{ detail.name }}</h4>
          <p
            v-if="detail.description"
            class="mb-2 whitespace-pre-wrap text-zinc-300"
          >
            {{ truncate(detail.description, 800) }}
          </p>
          <p v-if="detail.trigger_words?.length" class="mb-2">
            <span class="text-zinc-400">Triggers:</span>
            <span
              v-for="t in detail.trigger_words"
              :key="t"
              class="mr-1 inline-block rounded bg-zinc-800 px-2 py-0.5 text-xs"
              >{{ t }}</span
            >
          </p>
          <div v-if="detail.modelVersions?.length" class="space-y-2">
            <div
              v-for="ver in detail.modelVersions"
              :key="ver.id"
              class="rounded border border-zinc-800 p-2"
            >
              <div class="font-medium">{{ ver.name }} (id {{ ver.id }})</div>
              <p v-if="ver.trainedWords?.length" class="text-xs text-zinc-400">
                Triggers: {{ ver.trainedWords.join(", ") }}
              </p>
              <button
                type="button"
                class="mss-cfg-btn mt-2"
                @click="queueCivitai(ver.id, detail)"
              >
                Download this version
              </button>
            </div>
          </div>
        </div>
      </div>

      <div class="mss-cfg-card space-y-3">
        <h3 class="text-center text-base font-semibold">Manual download</h3>
        <div class="flex flex-wrap justify-center gap-2">
          <select v-model="dlSource" class="mss-cfg-input max-w-[9rem]">
            <option value="civitai">CivitAI</option>
            <option value="huggingface">HuggingFace</option>
          </select>
          <select v-model="dlDest" class="mss-cfg-input max-w-[9rem]">
            <option value="local">Local</option>
            <option value="s3">S3</option>
          </select>
          <select v-model="dlFolder" class="mss-cfg-input max-w-[11rem]">
            <option v-for="f in folders" :key="f" :value="f">{{ f }}</option>
          </select>
        </div>
        <div v-if="dlSource === 'civitai'" class="mx-auto max-w-sm">
          <label class="mss-cfg-label">Model version ID</label>
          <input
            v-model="dlVersion"
            class="mss-cfg-input text-center"
            placeholder="e.g. 138296"
          />
        </div>
        <div v-else class="mx-auto grid max-w-lg gap-2 sm:grid-cols-2">
          <div>
            <label class="mss-cfg-label">Repo ID</label>
            <input
              v-model="dlRepo"
              class="mss-cfg-input"
              placeholder="org/model"
            />
          </div>
          <div>
            <label class="mss-cfg-label">Filename</label>
            <input
              v-model="dlFilename"
              class="mss-cfg-input"
              placeholder="model.safetensors"
            />
          </div>
        </div>
        <div class="flex justify-center">
          <button type="button" class="mss-cfg-btn" @click="queueManual">
            Queue download
          </button>
        </div>
        <p class="mss-cfg-note text-center">{{ dlStatus }}</p>
      </div>

      <div class="mss-cfg-card space-y-3">
        <h3 class="text-center text-base font-semibold">
          Active / incomplete jobs
        </h3>
        <button type="button" class="mss-cfg-btn-ghost" @click="refreshJobs">
          Refresh
        </button>
        <div
          v-for="job in jobs"
          :key="job.job_id"
          class="rounded-lg border border-zinc-800 p-3 text-left text-sm"
        >
          <div class="flex flex-wrap items-center justify-between gap-2">
            <span class="font-medium">{{ jobLabel(job) }}</span>
            <span class="text-zinc-400">{{ job.status }}</span>
          </div>
          <div class="mt-2 h-2 overflow-hidden rounded bg-zinc-800">
            <div
              class="h-full bg-[#9660fa] transition-all"
              :style="{ width: Math.min(100, job.progress_pct || 0) + '%' }"
            />
          </div>
          <p class="mt-1 text-xs text-zinc-400">
            {{ formatBytes(job.bytes_done) }}
            <template v-if="job.total_bytes">
              / {{ formatBytes(job.total_bytes) }}</template
            >
            · {{ Math.round(job.progress_pct || 0) }}%
          </p>
          <p v-if="job.error" class="text-xs text-red-300">{{ job.error }}</p>
          <div class="mt-2 flex flex-wrap gap-2">
            <button
              v-if="job.can_resume"
              type="button"
              class="mss-cfg-btn"
              @click="resumeJob(job.job_id)"
            >
              Resume
            </button>
            <button
              v-if="job.can_cancel"
              type="button"
              class="mss-cfg-btn-danger"
              @click="cancelJob(job.job_id)"
            >
              Cancel
            </button>
          </div>
        </div>
        <p v-if="!jobs.length" class="mss-cfg-note text-center">
          No active jobs.
        </p>
      </div>

      <div class="mss-cfg-card space-y-3">
        <h3 class="text-center text-base font-semibold">
          Previously downloaded
        </h3>
        <p class="mss-cfg-note text-center">
          Your download history with descriptions and triggers. Other users'
          models stay gated by Shared Models / isolation permissions.
        </p>
        <button type="button" class="mss-cfg-btn-ghost" @click="refreshHistory">
          Refresh history
        </button>
        <div
          v-for="item in history"
          :key="item.job_id"
          class="rounded-lg border border-zinc-800 p-3 text-left text-sm"
        >
          <div class="font-medium">
            {{
              item.filename ||
              item.model_version_id ||
              item.repo_id ||
              item.job_id
            }}
          </div>
          <p class="text-xs text-zinc-400">
            {{ item.source }} · {{ item.folder_type }} · {{ item.status }}
          </p>
          <p v-if="item.description" class="mt-1 text-zinc-300">
            {{ truncate(item.description, 280) }}
          </p>
          <p v-if="item.trigger_words?.length" class="mt-1">
            <span
              v-for="t in item.trigger_words"
              :key="t"
              class="mr-1 inline-block rounded bg-zinc-800 px-2 py-0.5 text-xs"
              >{{ t }}</span
            >
          </p>
          <button
            v-if="item.can_resume"
            type="button"
            class="mss-cfg-btn mt-2"
            @click="resumeJob(item.job_id)"
          >
            Resume
          </button>
        </div>
        <p v-if="!history.length" class="mss-cfg-note text-center">
          No download history yet.
        </p>
      </div>
    </template>
  </div>
</template>

<script>
import { apiJson } from "./api.js";

export default {
  name: "ModelDownloadSection",
  props: {
    currentUser: { type: Object, default: null },
  },
  data() {
    return {
      hasPermission: true,
      sourcesWithKeys: [],
      civitaiKey: "",
      hfKey: "",
      civitaiHost: "civitai.com",
      keysStatus: "",
      provider: "civitai",
      query: "",
      browseStatus: "",
      results: [],
      detail: null,
      folders: [
        "checkpoints",
        "loras",
        "vae",
        "embeddings",
        "controlnet",
        "upscale_models",
      ],
      dlSource: "civitai",
      dlDest: "local",
      dlFolder: "checkpoints",
      dlVersion: "",
      dlRepo: "",
      dlFilename: "",
      dlStatus: "",
      jobs: [],
      history: [],
      pollTimer: null,
    };
  },
  methods: {
    truncate(s, n) {
      const t = String(s || "");
      return t.length > n ? t.slice(0, n) + "…" : t;
    },
    formatBytes(n) {
      const v = Number(n) || 0;
      if (v < 1024) return `${v} B`;
      if (v < 1024 * 1024) return `${(v / 1024).toFixed(1)} KB`;
      if (v < 1024 * 1024 * 1024) return `${(v / (1024 * 1024)).toFixed(1)} MB`;
      return `${(v / (1024 * 1024 * 1024)).toFixed(2)} GB`;
    },
    jobLabel(job) {
      return (
        job.filename ||
        job.model_version_id ||
        job.repo_id ||
        job.job_id?.slice(0, 8) ||
        "job"
      );
    },
    async bootstrap() {
      const { ok, status, data } = await apiJson(
        "/mss-login/api/model-download/sources",
      );
      if (status === 403) {
        this.hasPermission = false;
        return;
      }
      if (ok) this.sourcesWithKeys = data?.sources_with_keys || [];
      const pref = await apiJson("/mss-login/api/model-download/preferences");
      if (pref.ok && pref.data?.civitai_host)
        this.civitaiHost = pref.data.civitai_host;
      const folders = await apiJson("/mss-login/api/model-download/folders");
      if (folders.ok && folders.data?.folders?.length) {
        this.folders = folders.data.folders;
        this.dlFolder = folders.data.default_folder || this.folders[0];
      }
      await this.refreshJobs();
      await this.refreshHistory();
    },
    async saveKeys() {
      this.keysStatus = "Saving…";
      if (this.civitaiKey) {
        await apiJson("/mss-login/api/model-download/api-keys", {
          method: "PUT",
          body: { source: "civitai", api_key: this.civitaiKey },
        });
      }
      if (this.hfKey) {
        await apiJson("/mss-login/api/model-download/api-keys", {
          method: "PUT",
          body: { source: "huggingface", api_key: this.hfKey },
        });
      }
      this.civitaiKey = "";
      this.hfKey = "";
      const src = await apiJson("/mss-login/api/model-download/sources");
      this.sourcesWithKeys = src.data?.sources_with_keys || [];
      this.keysStatus = "Keys saved.";
    },
    async saveHost() {
      const { ok, data } = await apiJson(
        "/mss-login/api/model-download/preferences",
        {
          method: "PUT",
          body: { civitai_host: this.civitaiHost },
        },
      );
      this.keysStatus = ok
        ? `Host: ${data?.civitai_host}`
        : data?.error || "Failed";
    },
    async search() {
      this.browseStatus = "Searching…";
      this.detail = null;
      this.results = [];
      if (this.provider === "civitai") {
        const { ok, data } = await apiJson(
          `/mss-login/api/model-download/civitai/search?query=${encodeURIComponent(this.query)}`,
        );
        if (!ok) {
          this.browseStatus = data?.error || "Search failed";
          return;
        }
        const items = data?.result?.items || [];
        this.results = items.map((m) => ({
          key: `c-${m.id}`,
          id: m.id,
          title: m.name,
          meta: m.type || "",
          raw: m,
        }));
        this.browseStatus = `${this.results.length} result(s)`;
      } else {
        const { ok, data } = await apiJson(
          `/mss-login/api/model-download/huggingface/search?query=${encodeURIComponent(this.query)}`,
        );
        if (!ok) {
          this.browseStatus = data?.error || "Search failed";
          return;
        }
        this.results = (data?.items || []).map((m) => ({
          key: `hf-${m.repo_id || m.id}`,
          id: m.repo_id || m.id,
          title: m.repo_id || m.id,
          meta: m.pipeline_tag || "",
          raw: m,
        }));
        this.browseStatus = `${this.results.length} result(s)`;
      }
    },
    async openResult(item) {
      if (this.provider !== "civitai") {
        this.dlSource = "huggingface";
        this.dlRepo = item.id;
        this.browseStatus =
          "Enter a filename below to download from this repo.";
        return;
      }
      this.browseStatus = "Loading model details…";
      const { ok, data } = await apiJson(
        `/mss-login/api/model-download/civitai/models/${encodeURIComponent(item.id)}`,
      );
      if (!ok) {
        this.browseStatus = data?.error || "Failed to load model";
        return;
      }
      this.detail = data?.ui || data?.model || null;
      this.browseStatus = "";
    },
    async queueCivitai(versionId, detail) {
      this.dlStatus = "Queuing…";
      const { ok, data } = await apiJson(
        "/mss-login/api/model-download/download",
        {
          method: "POST",
          body: {
            source: "civitai",
            model_version_id: String(versionId),
            model_id: detail?.id ? String(detail.id) : "",
            destination_type: this.dlDest,
            folder_type: this.dlFolder,
            description: detail?.description || "",
            trigger_words: detail?.trigger_words || [],
            civitai_host: this.civitaiHost,
          },
        },
      );
      this.dlStatus = ok ? `Queued ${data?.job_id}` : data?.error || "Failed";
      await this.refreshJobs();
      await this.refreshHistory();
    },
    async queueManual() {
      this.dlStatus = "Queuing…";
      const body =
        this.dlSource === "civitai"
          ? {
              source: "civitai",
              model_version_id: this.dlVersion,
              destination_type: this.dlDest,
              folder_type: this.dlFolder,
              civitai_host: this.civitaiHost,
            }
          : {
              source: "huggingface",
              repo_id: this.dlRepo,
              filename: this.dlFilename,
              destination_type: this.dlDest,
              folder_type: this.dlFolder,
            };
      const { ok, data } = await apiJson(
        "/mss-login/api/model-download/download",
        {
          method: "POST",
          body,
        },
      );
      this.dlStatus = ok ? `Queued ${data?.job_id}` : data?.error || "Failed";
      await this.refreshJobs();
      await this.refreshHistory();
    },
    async refreshJobs() {
      const { ok, data } = await apiJson("/mss-login/api/model-download/jobs");
      if (ok) this.jobs = data?.jobs || [];
    },
    async refreshHistory() {
      const { ok, data } = await apiJson(
        "/mss-login/api/model-download/history?limit=50",
      );
      if (ok) this.history = data?.items || [];
    },
    async resumeJob(jobId) {
      await apiJson(
        `/mss-login/api/model-download/jobs/${encodeURIComponent(jobId)}/resume`,
        {
          method: "POST",
          body: {},
        },
      );
      await this.refreshJobs();
      await this.refreshHistory();
    },
    async cancelJob(jobId) {
      await apiJson(
        `/mss-login/api/model-download/jobs/${encodeURIComponent(jobId)}/cancel`,
        {
          method: "POST",
          body: {},
        },
      );
      await this.refreshJobs();
    },
  },
  async mounted() {
    await this.bootstrap();
    this.pollTimer = setInterval(() => {
      this.refreshJobs();
    }, 2000);
  },
  beforeUnmount() {
    if (this.pollTimer) clearInterval(this.pollTimer);
  },
};
</script>
