<template>
  <div class="mss-cfg-section">
    <div class="mss-cfg-card space-y-4">
      <p class="mss-cfg-note">
        Customize the <code>/login</code> page background with an image or
        video. Requires the experimental master switch and
        <strong>login_background</strong> flag. Local files must live under the
        MSS-Login data directory (recommended: <code>login-background/</code>).
      </p>
      <p v-if="!experimentalActive" class="mss-cfg-warn">
        Experimental login background is currently off. Settings below will be
        saved but will not appear on /login until you enable it under
        Experimental features.
      </p>

      <label class="mss-cfg-check-row">
        <input v-model="enabled" type="checkbox" class="mss-cfg-checkbox" />
        <span>Enable custom login background</span>
      </label>

      <div>
        <label class="mss-cfg-label" for="mss-login-bg-source">Source</label>
        <select id="mss-login-bg-source" v-model="source" class="mss-cfg-input">
          <option value="url">External URL</option>
          <option value="local">Local path (data directory)</option>
        </select>
      </div>

      <div v-if="source === 'url'">
        <label class="mss-cfg-label" for="mss-login-bg-url"
          >Image or video URL</label
        >
        <input
          id="mss-login-bg-url"
          v-model="url"
          type="url"
          class="mss-cfg-input"
          placeholder="https://example.com/background.jpg"
        />
      </div>

      <div v-else>
        <label class="mss-cfg-label" for="mss-login-bg-local">Local path</label>
        <input
          id="mss-login-bg-local"
          v-model="localPath"
          type="text"
          class="mss-cfg-input"
          placeholder="login-background/my-bg.mp4"
        />
        <p class="mss-cfg-note mt-1">
          Relative to the data directory, or an absolute path under it.
        </p>
      </div>

      <div>
        <label class="mss-cfg-label" for="mss-login-bg-kind">Media kind</label>
        <select
          id="mss-login-bg-kind"
          v-model="mediaKind"
          class="mss-cfg-input"
        >
          <option value="auto">Auto-detect</option>
          <option value="image">Image</option>
          <option value="video">Video</option>
        </select>
      </div>

      <div class="flex flex-wrap items-center gap-3">
        <button
          type="button"
          class="mss-cfg-btn"
          :disabled="saving"
          @click="save"
        >
          {{ saving ? "Saving…" : "Save login appearance" }}
        </button>
        <span v-if="status" class="mss-cfg-note">{{ status }}</span>
      </div>
    </div>
  </div>
</template>

<script>
import { apiJson } from "./api.js";

export default {
  name: "LoginAppearanceSection",
  data() {
    return {
      enabled: false,
      source: "url",
      url: "",
      localPath: "",
      mediaKind: "auto",
      experimentalActive: false,
      saving: false,
      status: "",
    };
  },
  async mounted() {
    await this.load();
  },
  methods: {
    async load() {
      this.status = "";
      const { ok, data } = await apiJson(
        "/mss-login/api/settings/login-background",
      );
      if (!ok || !data) {
        this.status = data?.error || "Failed to load settings.";
        return;
      }
      this.enabled = !!data.enabled;
      this.source = data.source === "local" ? "local" : "url";
      this.url = data.url || "";
      this.localPath = data.local_path || "";
      this.mediaKind = ["auto", "image", "video"].includes(data.media_kind)
        ? data.media_kind
        : "auto";
      this.experimentalActive = !!data.experimental_active;
    },
    async save() {
      this.saving = true;
      this.status = "";
      const { ok, data } = await apiJson(
        "/mss-login/api/settings/login-background",
        {
          method: "PUT",
          body: {
            enabled: this.enabled,
            source: this.source,
            url: this.url,
            local_path: this.localPath,
            media_kind: this.mediaKind,
          },
        },
      );
      this.saving = false;
      if (!ok) {
        this.status = data?.error || "Save failed.";
        return;
      }
      this.experimentalActive = !!data.experimental_active;
      this.status = data.experimental_active
        ? "Saved. Reload /login to preview."
        : "Saved. Enable experimental login_background to apply on /login.";
    },
  },
};
</script>
