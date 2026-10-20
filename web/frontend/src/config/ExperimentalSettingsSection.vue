<template>
  <div class="mss-cfg-section space-y-4">
    <!-- Master Switch & Features -->
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

    <!-- Push Notifications (ntfy) -->
    <div class="mss-cfg-card space-y-3">
      <h3 class="text-center text-base font-semibold">
        Push Notifications (ntfy)
      </h3>
      <p class="mss-cfg-note text-center">
        Receive instant alerts on your phone or desktop for security events, logins, and NSFW blocks via ntfy.sh or a self-hosted ntfy server.
      </p>

      <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div>
          <label class="mss-cfg-label" for="mss-ntfy-topic">Topic Name</label>
          <input
            id="mss-ntfy-topic"
            v-model="ntfyTopic"
            type="text"
            class="mss-cfg-input"
            placeholder="e.g. my-private-comfy-topic"
          />
        </div>
        <div>
          <label class="mss-cfg-label" for="mss-ntfy-url">Server URL</label>
          <input
            id="mss-ntfy-url"
            v-model="ntfyBaseUrl"
            type="url"
            class="mss-cfg-input"
            placeholder="https://ntfy.sh"
          />
        </div>
      </div>

      <div>
        <label class="mss-cfg-label" for="mss-ntfy-token">API Token (Optional)</label>
        <input
          id="mss-ntfy-token"
          v-model="ntfyApiToken"
          type="password"
          class="mss-cfg-input"
          :placeholder="ntfyHasToken ? 'Token configured (enter new to replace)' : 'Leave blank if public topic'"
          autocomplete="off"
        />
        <label v-if="ntfyHasToken" class="mss-cfg-check-row mt-2">
          <input v-model="ntfyClearToken" type="checkbox" class="mss-cfg-checkbox" />
          <span class="text-xs text-zinc-400">Clear stored API token on save</span>
        </label>
      </div>

      <div class="space-y-2 border-t border-zinc-800 pt-3">
        <label class="mss-cfg-label">Notification Events</label>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs">
          <label v-for="(lbl, key) in ntfyEventLabels" :key="key" class="mss-cfg-check-row">
            <input
              v-model="ntfyEvents[key]"
              type="checkbox"
              class="mss-cfg-checkbox"
            />
            <span>{{ lbl }}</span>
          </label>
        </div>
      </div>

      <div class="flex flex-wrap items-center gap-3 pt-2">
        <button
          type="button"
          class="mss-cfg-btn"
          :disabled="ntfySaving"
          @click="saveNtfy"
        >
          {{ ntfySaving ? "Saving…" : "Save ntfy settings" }}
        </button>
        <span v-if="ntfyStatus" class="mss-cfg-note">{{ ntfyStatus }}</span>
      </div>
    </div>

    <!-- System & Debug Options -->
    <div class="mss-cfg-card space-y-3">
      <h3 class="text-center text-base font-semibold">
        System &amp; Console Controls
      </h3>
      <p class="mss-cfg-note text-center">
        Configure guest token permissions and console verbosity.
      </p>

      <div class="space-y-2.5">
        <label class="mss-cfg-check-row">
          <input
            v-model="allowGuestJwt"
            type="checkbox"
            class="mss-cfg-checkbox"
          />
          <span>Allow guest JWT tokens (guest login issues session JWT)</span>
        </label>

        <label class="mss-cfg-check-row">
          <input
            v-model="filterDebugMessages"
            type="checkbox"
            class="mss-cfg-checkbox"
          />
          <span>Filter debug messages (suppress verbose DEBUG output from console)</span>
        </label>
      </div>

      <div class="flex flex-wrap items-center gap-3 pt-2">
        <button
          type="button"
          class="mss-cfg-btn"
          :disabled="systemSaving"
          @click="saveSystemControls"
        >
          {{ systemSaving ? "Saving…" : "Save system options" }}
        </button>
        <span v-if="systemStatus" class="mss-cfg-note">{{ systemStatus }}</span>
      </div>
    </div>

    <!-- Custom Nodes Dependency Installer -->
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

    <!-- Experimental Failsafe -->
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

const NTFY_EVENT_LABELS = {
  nsfw_block: "Notify when user blocked for NSFW",
  user_created: "Notify when new user created",
  user_login: "Notify when user logs in (include IP)",
  user_logout: "Notify when user logs out",
  api_token_created: "Notify when API/JWT token created",
  login_failure: "Notify on login failure",
  shared_items_added: "Notify when shared model/item added",
  shared_items_removed: "Notify when shared model/item removed",
  experimental_recovery: "Notify when failsafe/recovery runs",
};

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
      // ntfy
      ntfyTopic: "",
      ntfyBaseUrl: "https://ntfy.sh",
      ntfyApiToken: "",
      ntfyHasToken: false,
      ntfyClearToken: false,
      ntfyEvents: {},
      ntfyEventLabels: NTFY_EVENT_LABELS,
      ntfySaving: false,
      ntfyStatus: "",
      // system
      allowGuestJwt: false,
      filterDebugMessages: false,
      systemSaving: false,
      systemStatus: "",
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
    async loadNtfy() {
      try {
        const { ok, data } = await apiJson("/mss-login/api/settings/ntfy");
        if (ok && data) {
          this.ntfyTopic = data.topic || "";
          this.ntfyBaseUrl = data.base_url || "https://ntfy.sh";
          this.ntfyHasToken = !!data.has_api_token;
          const enabled = data.enabled_events || [];
          const evMap = {};
          Object.keys(NTFY_EVENT_LABELS).forEach((k) => {
            evMap[k] = enabled.includes(k);
          });
          this.ntfyEvents = evMap;
        }
      } catch (_) {}
    },
    async loadSystemControls() {
      try {
        const [gRes, dRes] = await Promise.all([
          apiJson("/mss-login/api/settings/guest-jwt"),
          apiJson("/mss-login/api/settings/debug-filter"),
        ]);
        if (gRes.ok && gRes.data) {
          this.allowGuestJwt = !!gRes.data.allow_guest_jwt;
        }
        if (dRes.ok && dRes.data) {
          this.filterDebugMessages = !!dRes.data.filter_debug_messages;
        }
      } catch (_) {}
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
    async saveNtfy() {
      this.ntfySaving = true;
      this.ntfyStatus = "Saving…";
      const enabledList = Object.keys(this.ntfyEvents).filter((k) => this.ntfyEvents[k]);
      const payload = {
        topic: this.ntfyTopic.trim(),
        base_url: this.ntfyBaseUrl.trim(),
        enabled_events: enabledList,
      };
      if (this.ntfyClearToken) {
        payload.api_token = "";
      } else if (this.ntfyApiToken.trim()) {
        payload.api_token = this.ntfyApiToken.trim();
      }

      const { ok, data } = await apiJson("/mss-login/api/settings/ntfy", {
        method: "PUT",
        body: payload,
      });
      this.ntfySaving = false;
      if (ok) {
        this.ntfyApiToken = "";
        this.ntfyClearToken = false;
        this.ntfyStatus = "ntfy settings saved.";
        await this.loadNtfy();
      } else {
        this.ntfyStatus = data?.error || "Save failed.";
      }
    },
    async saveSystemControls() {
      this.systemSaving = true;
      this.systemStatus = "Saving…";
      try {
        const [gRes, dRes] = await Promise.all([
          apiJson("/mss-login/api/settings/guest-jwt", {
            method: "PUT",
            body: { allow_guest_jwt: this.allowGuestJwt },
          }),
          apiJson("/mss-login/api/settings/debug-filter", {
            method: "PUT",
            body: { filter_debug_messages: this.filterDebugMessages },
          }),
        ]);
        this.systemSaving = false;
        if (gRes.ok && dRes.ok) {
          this.systemStatus = "System controls saved.";
        } else {
          this.systemStatus = "Save failed.";
        }
      } catch (err) {
        this.systemSaving = false;
        this.systemStatus = "Save failed: " + (err.message || String(err));
      }
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
    await Promise.all([
      this.loadExperimental(),
      this.loadFailsafe(),
      this.loadNtfy(),
      this.loadSystemControls(),
    ]);
  },
};
</script>
