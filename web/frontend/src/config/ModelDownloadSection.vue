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
            @keyup.enter="search(false)"
          />
          <button type="button" class="mss-cfg-btn" @click="search(false)">
            Search
          </button>
        </div>
        <div
          v-if="provider === 'civitai'"
          class="flex flex-wrap items-center justify-center gap-2"
        >
          <select v-model="civitaiType" class="mss-cfg-input max-w-[11rem]">
            <option value="">All types</option>
            <option value="Checkpoint">Checkpoint</option>
            <option value="LORA">LORA</option>
            <option value="LoCon">LoCon</option>
            <option value="VAE">VAE</option>
            <option value="TextualInversion">Textual Inversion</option>
            <option value="Controlnet">ControlNet</option>
            <option value="Upscaler">Upscaler</option>
          </select>
          <select v-model="civitaiSort" class="mss-cfg-input max-w-[12rem]">
            <option value="Most Downloaded">Most Downloaded</option>
            <option value="Highest Rated">Highest Rated</option>
            <option value="Newest">Newest</option>
          </select>
          <label class="inline-flex items-center gap-1 text-xs text-zinc-300">
            <input v-model="primaryFileOnly" type="checkbox" />
            Primary file only
          </label>
        </div>
        <div
          v-else
          class="flex flex-wrap items-center justify-center gap-2 text-xs text-zinc-300"
        >
          <label class="inline-flex items-center gap-1">
            <input v-model="hfSafetensorsOnly" type="checkbox" />
            SafeTensors filter
          </label>
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
        <div v-if="canLoadMore" class="flex justify-center">
          <button type="button" class="mss-cfg-btn-ghost" @click="search(true)">
            Load more
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
              <div
                v-if="ver.files?.length"
                class="mt-2 space-y-1 border-t border-zinc-800 pt-2"
              >
                <p class="text-xs text-zinc-400">Files</p>
                <button
                  v-for="(file, idx) in sortedFiles(ver.files)"
                  :key="(file.name || 'f') + '-' + idx"
                  type="button"
                  class="block w-full rounded border border-zinc-800 px-2 py-1.5 text-left text-xs hover:border-[#9660fa]"
                  @click="queueCivitaiFile(ver, file, detail)"
                >
                  <span class="font-medium">{{
                    file.name || "unnamed file"
                  }}</span>
                  <span class="ml-2 text-zinc-400">
                    {{ file.format || "?" }}
                    <template v-if="file.size"> · {{ file.size }}</template>
                    <template v-if="file.fp"> · {{ file.fp }}</template>
                    <template v-if="file.sizeKB">
                      · {{ formatKb(file.sizeKB) }}</template
                    >
                    <template v-if="file.primary"> · primary</template>
                  </span>
                </button>
              </div>
              <button
                v-else
                type="button"
                class="mss-cfg-btn mt-2"
                @click="queueCivitaiFile(ver, null, detail)"
              >
                Download this version
              </button>
            </div>
          </div>
        </div>
        <div
          v-if="hfFiles.length"
          class="rounded-lg border border-zinc-700 bg-zinc-950 p-3 text-left text-sm"
        >
          <h4 class="mb-2 font-semibold">{{ dlRepo }} — files</h4>
          <button
            v-for="file in hfFiles"
            :key="file.path"
            type="button"
            class="mb-1 block w-full rounded border border-zinc-800 px-2 py-1.5 text-left text-xs hover:border-[#9660fa]"
            @click="queueHfFile(file)"
          >
            <span class="font-medium">{{ file.path }}</span>
            <span v-if="file.size" class="ml-2 text-zinc-400">{{
              formatBytes(file.size)
            }}</span>
          </button>
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
        <div class="mx-auto max-w-sm">
          <label class="mss-cfg-label">Save as (optional)</label>
          <input
            v-model="dlSaveAs"
            class="mss-cfg-input text-center"
            placeholder="custom-name.safetensors"
          />
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
        <div class="flex justify-center gap-2">
          <button
            v-if="dlSource === 'huggingface' && dlRepo"
            type="button"
            class="mss-cfg-btn-ghost"
            @click="loadHfFiles(dlRepo)"
          >
            List files
          </button>
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
      civitaiType: "",
      civitaiSort: "Most Downloaded",
      primaryFileOnly: true,
      hfSafetensorsOnly: true,
      browseStatus: "",
      results: [],
      detail: null,
      hfFiles: [],
      nextCursor: null,
      browsePage: 1,
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
      dlSaveAs: "",
      dlStatus: "",
      jobs: [],
      history: [],
      pollTimer: null,
    };
  },
  computed: {
    canLoadMore() {
      if (this.provider !== "civitai") return false;
      if ((this.query || "").trim()) return Boolean(this.nextCursor);
      return this.results.length > 0 && this.results.length % 20 === 0;
    },
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
    formatKb(kb) {
      const v = Number(kb) || 0;
      return this.formatBytes(v * 1024);
    },
    sortedFiles(files) {
      const list = Array.isArray(files) ? [...files] : [];
      list.sort((a, b) => {
        const af = String(a?.format || "").toLowerCase();
        const bf = String(b?.format || "").toLowerCase();
        const aSafe = af.includes("safetensor") ? 0 : 1;
        const bSafe = bf.includes("safetensor") ? 0 : 1;
        if (aSafe !== bSafe) return aSafe - bSafe;
        if (Boolean(b?.primary) !== Boolean(a?.primary)) {
          return Number(Boolean(b?.primary)) - Number(Boolean(a?.primary));
        }
        return String(a?.name || "").localeCompare(String(b?.name || ""));
      });
      return list;
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
    async search(loadMore = false) {
      this.browseStatus = loadMore ? "Loading more…" : "Searching…";
      this.detail = null;
      this.hfFiles = [];
      if (!loadMore) {
        this.results = [];
        this.nextCursor = null;
        this.browsePage = 1;
      }
      if (this.provider === "civitai") {
        const params = new URLSearchParams();
        const q = (this.query || "").trim();
        if (q) params.set("query", q);
        if (this.civitaiType) params.set("types", this.civitaiType);
        if (this.civitaiSort) params.set("sort", this.civitaiSort);
        if (this.primaryFileOnly) params.set("primaryFileOnly", "true");
        params.set("civitai_host", this.civitaiHost);
        params.set("limit", "20");
        if (q) {
          if (loadMore && this.nextCursor) {
            params.set("cursor", this.nextCursor);
          }
        } else {
          params.set("page", String(loadMore ? this.browsePage + 1 : 1));
        }
        const { ok, data } = await apiJson(
          `/mss-login/api/model-download/civitai/search?${params.toString()}`,
        );
        if (!ok) {
          this.browseStatus = data?.error || "Search failed";
          return;
        }
        const items = data?.result?.items || [];
        const mapped = items.map((m) => ({
          key: `c-${m.id}`,
          id: m.id,
          title: m.name,
          meta: m.type || "",
          raw: m,
        }));
        this.results = loadMore ? [...this.results, ...mapped] : mapped;
        this.nextCursor = data?.next_cursor || null;
        if (!q && loadMore) this.browsePage += 1;
        else if (!q) this.browsePage = 1;
        this.browseStatus = `${this.results.length} result(s)`;
      } else {
        const params = new URLSearchParams();
        params.set("query", this.query || "");
        if (this.hfSafetensorsOnly) params.set("safetensors", "true");
        const { ok, data } = await apiJson(
          `/mss-login/api/model-download/huggingface/search?${params.toString()}`,
        );
        if (!ok) {
          this.browseStatus = data?.error || "Search failed";
          return;
        }
        this.results = (data?.items || []).map((m) => ({
          key: `hf-${m.repo_id || m.id}`,
          id: m.repo_id || m.id,
          title: m.repo_id || m.id,
          meta:
            [m.pipeline_tag, m.has_safetensors ? "safetensors" : ""]
              .filter(Boolean)
              .join(" · ") || "",
          raw: m,
        }));
        this.browseStatus = `${this.results.length} result(s)`;
      }
    },
    async openResult(item) {
      if (this.provider !== "civitai") {
        this.dlSource = "huggingface";
        this.dlRepo = item.id;
        this.dlFilename = "";
        await this.loadHfFiles(item.id);
        return;
      }
      this.browseStatus = "Loading model details…";
      this.hfFiles = [];
      const params = new URLSearchParams();
      params.set("civitai_host", this.civitaiHost);
      const { ok, data } = await apiJson(
        `/mss-login/api/model-download/civitai/models/${encodeURIComponent(item.id)}?${params.toString()}`,
      );
      if (!ok) {
        this.browseStatus = data?.error || "Failed to load model";
        return;
      }
      this.detail = data?.ui || data?.model || null;
      this.browseStatus = "";
    },
    async loadHfFiles(repoId) {
      this.browseStatus = "Loading repo files…";
      this.hfFiles = [];
      const { ok, data } = await apiJson(
        `/mss-login/api/model-download/huggingface/files?repo_id=${encodeURIComponent(repoId)}`,
      );
      if (!ok) {
        this.browseStatus = data?.error || "Failed to list files";
        return;
      }
      this.hfFiles = data?.files || [];
      this.browseStatus = this.hfFiles.length
        ? `${this.hfFiles.length} file(s) — pick one to download`
        : "No model files found in repo";
    },
    async queueCivitaiFile(ver, file, detail) {
      this.dlStatus = "Queuing…";
      const body = {
        source: "civitai",
        model_version_id: String(ver.id),
        model_id: detail?.id ? String(detail.id) : "",
        destination_type: this.dlDest,
        folder_type: this.dlFolder,
        description: detail?.description || "",
        trigger_words: detail?.trigger_words || [],
        civitai_host: this.civitaiHost,
      };
      if (file) {
        if (file.type) body.type = file.type;
        if (file.format) body.format = file.format;
        if (file.size) body.size = file.size;
        if (file.fp) body.fp = file.fp;
        const saveAs = (this.dlSaveAs || file.name || "").trim();
        if (saveAs) body.filename = saveAs.split(/[/\\]/).pop();
      } else if ((this.dlSaveAs || "").trim()) {
        body.filename = this.dlSaveAs.trim().split(/[/\\]/).pop();
      }
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
    async queueHfFile(file) {
      this.dlSource = "huggingface";
      this.dlRepo = this.dlRepo;
      this.dlFilename = file.filename;
      this.dlStatus = "Queuing…";
      const body = {
        source: "huggingface",
        repo_id: this.dlRepo,
        filename: file.filename,
        destination_type: this.dlDest,
        folder_type: this.dlFolder,
      };
      if (file.subfolder) body.subfolder = file.subfolder;
      const saveAs = (this.dlSaveAs || "").trim();
      if (saveAs) body.save_as = saveAs.split(/[/\\]/).pop();
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
    async queueManual() {
      this.dlStatus = "Queuing…";
      const saveAs = (this.dlSaveAs || "").trim().split(/[/\\]/).pop();
      const body =
        this.dlSource === "civitai"
          ? {
              source: "civitai",
              model_version_id: this.dlVersion,
              destination_type: this.dlDest,
              folder_type: this.dlFolder,
              civitai_host: this.civitaiHost,
              ...(saveAs ? { filename: saveAs } : {}),
            }
          : {
              source: "huggingface",
              repo_id: this.dlRepo,
              filename: this.dlFilename,
              destination_type: this.dlDest,
              folder_type: this.dlFolder,
              ...(saveAs ? { save_as: saveAs } : {}),
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
