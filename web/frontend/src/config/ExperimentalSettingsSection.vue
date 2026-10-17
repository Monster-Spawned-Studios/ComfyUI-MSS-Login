<template>
  <div class="mss-cfg-section space-y-4">
    <div class="mss-cfg-card space-y-3">
      <p class="mss-cfg-note">
        Experimental features use a master switch plus per-feature toggles. When
        the master is off, every experimental feature is disabled regardless of
        individual flags. Some features require a ComfyUI restart to fully
        apply.
      </p>

      <label class="mss-cfg-check-row">
        <input v-model="master" type="checkbox" class="mss-cfg-checkbox" />
        <span
          ><strong>Enable experimental features (Master Switch)</strong></span
        >
      </label>

      <div class="space-y-2 border-t border-zinc-800 pt-3">
        <label
          v-for="feat in features"
          :key="feat.key"
          class="mss-cfg-check-row"
        >
          <input
            v-model="flags[feat.key]"
            type="checkbox"
            class="mss-cfg-checkbox"
            :disabled="!master"
          />
          <span>
            {{ feat.label }}
            <span v-if="feat.restartHint" class="ml-1 text-xs text-amber-300/90"
              >(may require restart)</span
            >
          </span>
        </label>
      </div>

      <p class="mss-cfg-note">
        Custom login background media is configured under
        <strong>Login Appearance</strong>; the flag above only enables that
        feature.
      </p>

      <div class="flex flex-wrap items-center gap-3">
        <button
          type="button"
          class="mss-cfg-btn"
          :disabled="saving"
          @click="saveExperimental"
        >
          {{ saving ? "Saving…" : "Save experimental settings" }}
        </button>
        <span v-if="status" class="mss-cfg-note">{{ status }}</span>
      </div>

      <div
        v-if="restartRequired"
        class="mss-cfg-warn rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-sm text-amber-100"
        role="status"
      >
        <p class="font-medium">Restart the ComfyUI server to fully apply:</p>
        <ul class="mt-1 list-disc pl-5">
          <li v-for="reason in restartReasons" :key="reason">{{ reason }}</li>
        </ul>
      </div>
    </div>

    <div class="mss-cfg-card space-y-3">
      <h3 class="text-center text-base font-semibold">
        Other custom nodes dependency installer
      </h3>
      <p class="mss-cfg-note text-center">
        Scans sibling folders in <code>custom_nodes/</code>, resolves version
        conflicts, and installs missing requirements. Enable the experimental
        flag above for the startup auto-scan; use these buttons anytime.
      </p>
      <div class="flex flex-wrap justify-center gap-2">
        <button
          type="button"
          class="mss-cfg-btn"
          :disabled="depsBusy"
          @click="runNodeDeps(false)"
        >
          Scan &amp; Install Now
        </button>
        <button
          type="button"
          class="mss-cfg-btn-ghost"
          :disabled="depsBusy"
          @click="runNodeDeps(true)"
        >
          Dry Run Check Only
        </button>
      </div>
      <pre
        v-if="depsResult"
        class="max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg border border-zinc-800 bg-zinc-950 p-2 text-left text-xs text-zinc-300"
        >{{ depsResult }}</pre
      >
    </div>

    <div class="mss-cfg-card space-y-3">
      <h3 class="text-center text-base font-semibold">Experimental failsafe</h3>
      <p class="mss-cfg-note text-center">
        Non-experimental safety control. When enabled, critical experimental
        failures can auto-disable risky features.
      </p>
      <label class="mss-cfg-check-row">
        <input
          v-model="failsafeEnabled"
          type="checkbox"
          class="mss-cfg-checkbox"
        />
        <span>Enable experimental failsafe</span>
      </label>
      <label class="mss-cfg-check-row">
        <input
          v-model="failsafeEscalate"
          type="checkbox"
          class="mss-cfg-checkbox"
        />
        <span>Escalate to recovery update after repeated failures</span>
      </label>
      <p v-if="failsafeState" class="mss-cfg-note text-center">
        {{ failsafeState }}
      </p>
      <div class="flex flex-wrap items-center justify-center gap-3">
        <button
          type="button"
          class="mss-cfg-btn"
          :disabled="failsafeSaving"
          @click="saveFailsafe"
        >
          {{ failsafeSaving ? "Saving…" : "Save failsafe settings" }}
        </button>
        <span v-if="failsafeStatus" class="mss-cfg-note">{{
          failsafeStatus
        }}</span>
      </div>
    </div>
  </div>
</template>

<script>
import { apiJson } from "./api.js";

const FEATURE_DEFS = [
  { key: "mfa", label: "MFA (two-factor authentication)" },
  {
    key: "s3",
    label: "S3 storage (mount & sync)",
    restartHint: true,
  },
  { key: "loading_screen", label: "Loading screen (post-login)" },
  { key: "news", label: "News / RSS feed" },
  {
    key: "model_isolation",
    label: "Model isolation (per-user model folders)",
  },
  {
    key: "tailscale_local_auth",
    label: "Tailscale & Local Network Authentication",
  },
  {
    key: "install_other_nodes_deps",
    label: "Auto-install dependencies for other custom nodes",
    restartHint: true,
  },
  {
    key: "login_background",
    label: "Custom login background (image/video)",
  },
];

function emptyFlags() {
  const o = {};
  for (const f of FEATURE_DEFS) o[f.key] = false;
  return o;
}

export default {
  name: "ExperimentalSettingsSection",
  data() {
    return {
      features: FEATURE_DEFS,
      master: false,
      flags: emptyFlags(),
      saving: false,
      status: "",
      restartRequired: false,
      restartReasons: [],
      depsBusy: false,
      depsResult: "",
      failsafeEnabled: false,
      failsafeEscalate: false,
      failsafeState: "",
      failsafeSaving: false,
      failsafeStatus: "",
    };
  },
  methods: {
    async loadExperimental() {
      const { ok, data } = await apiJson(
        "/mss-login/api/settings/experimental",
      );
      if (!ok) {
        this.status = data?.error || "Failed to load experimental settings";
        return;
      }
      this.master = !!data.experimental_features;
      const next = emptyFlags();
      const exp = data.experimental || {};
      for (const f of FEATURE_DEFS) {
        next[f.key] = !!exp[f.key];
      }
      this.flags = next;
      this.status = "";
    },
    async loadFailsafe() {
      const { ok, data } = await apiJson(
        "/mss-login/api/settings/experimental-failsafe",
      );
      if (!ok) {
        this.failsafeStatus = data?.error || "Failed to load failsafe";
        return;
      }
      this.failsafeEnabled = !!data.enabled;
      this.failsafeEscalate = !!data.escalate_after_repeated_failure;
      const parts = [];
      if (data.last_failure_reason)
        parts.push(`Last reason: ${data.last_failure_reason}`);
      if (data.failure_count != null)
        parts.push(`Failure count: ${data.failure_count}`);
      this.failsafeState = parts.join(" · ");
      this.failsafeStatus = "";
    },
    async saveExperimental() {
      this.saving = true;
      this.status = "Saving…";
      this.restartRequired = false;
      this.restartReasons = [];
      const experimental = {};
      for (const f of FEATURE_DEFS) {
        experimental[f.key] = !!this.flags[f.key];
      }
      const { ok, data } = await apiJson(
        "/mss-login/api/settings/experimental",
        {
          method: "PUT",
          body: {
            experimental_features: !!this.master,
            experimental,
          },
        },
      );
      this.saving = false;
      if (!ok) {
        this.status = data?.error || "Save failed";
        return;
      }
      // Reload checkbox state from GET (config values, not env-effective).
      await this.loadExperimental();
      this.restartRequired = !!data.restart_required;
      this.restartReasons = Array.isArray(data.restart_reasons)
        ? data.restart_reasons
        : [];
      this.status = this.restartRequired
        ? "Saved. Restart ComfyUI to fully apply some changes."
        : "Experimental settings saved.";
    },
    async saveFailsafe() {
      this.failsafeSaving = true;
      this.failsafeStatus = "Saving…";
      const { ok, data } = await apiJson(
        "/mss-login/api/settings/experimental-failsafe",
        {
          method: "PUT",
          body: {
            enabled: !!this.failsafeEnabled,
            escalate_after_repeated_failure: !!this.failsafeEscalate,
          },
        },
      );
      this.failsafeSaving = false;
      if (!ok) {
        this.failsafeStatus = data?.error || "Save failed";
        return;
      }
      this.failsafeEnabled = !!data.enabled;
      this.failsafeEscalate = !!data.escalate_after_repeated_failure;
      this.failsafeStatus = "Failsafe settings saved.";
    },
    async runNodeDeps(dryRun) {
      this.depsBusy = true;
      this.depsResult = dryRun
        ? "Running dry-run scan on custom_nodes…"
        : "Scanning and installing dependencies (this may take a few moments)…";
      const { ok, data } = await apiJson(
        "/mss-login/api/admin/install-node-deps",
        {
          method: "POST",
          body: { dry_run: !!dryRun },
        },
      );
      this.depsBusy = false;
      if (!ok || data?.success === false) {
        this.depsResult = `Error: ${data?.error || "Request failed"}`;
        return;
      }
      const lines = [
        `Scan complete.`,
        `Nodes scanned: ${data.nodes_scanned ?? "?"}`,
        `Conflicts resolved: ${data.conflicts_resolved_count ?? "?"}`,
      ];
      for (const r of data.results || []) {
        const extra =
          r.conflicts_resolved && r.conflicts_resolved.length
            ? ` (${r.conflicts_resolved.length} conflict(s) fixed)`
            : "";
        lines.push(`• ${r.node}: ${r.status}${extra}`);
      }
      this.depsResult = lines.join("\n");
    },
  },
  async mounted() {
    await Promise.all([this.loadExperimental(), this.loadFailsafe()]);
  },
};
</script>
