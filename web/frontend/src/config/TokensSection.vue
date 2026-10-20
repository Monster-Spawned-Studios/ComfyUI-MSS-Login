<template>
  <div class="mss-cfg-section space-y-5">
    <!-- Guest Warning -->
    <div v-if="isGuest" class="mss-cfg-card text-center py-6">
      <div class="text-3xl mb-2">🔒</div>
      <h3 class="text-base font-semibold text-zinc-100">Guest Account</h3>
      <p class="mss-cfg-note mt-1 max-w-md mx-auto">
        Guest accounts cannot generate API tokens or manage persistent sessions. Please log in with a registered user account.
      </p>
    </div>

    <template v-else>
      <!-- Generated Token Notification Modal / Banner -->
      <div
        v-if="generatedToken"
        class="rounded-xl border border-[#9660fa] bg-zinc-950/90 p-4 shadow-xl backdrop-blur-md space-y-3"
      >
        <div class="flex items-center justify-between gap-2 border-b border-zinc-800 pb-2">
          <div class="flex items-center gap-2">
            <span class="flex h-2 w-2 rounded-full bg-emerald-400 animate-pulse"></span>
            <h4 class="text-sm font-semibold text-zinc-100">
              New Token Generated: <span class="text-[#9660fa]">{{ generatedLabel || "API Token" }}</span>
            </h4>
          </div>
          <button
            type="button"
            class="mss-cfg-btn-ghost h-7 w-7 p-0 text-zinc-400 hover:text-zinc-100"
            aria-label="Dismiss"
            @click="generatedToken = ''"
          >
            ✕
          </button>
        </div>

        <p class="text-xs text-amber-300">
          Make sure to copy your token now. For your security, this raw secret cannot be shown again after closing.
        </p>

        <div class="flex flex-col sm:flex-row gap-2 items-stretch sm:items-center">
          <input
            type="text"
            readonly
            :value="generatedToken"
            class="mss-cfg-input font-mono text-xs select-all text-zinc-200"
          />
          <button
            type="button"
            class="mss-cfg-btn shrink-0 gap-1.5"
            @click="copyToken(generatedToken)"
          >
            <span>{{ copySuccess ? "✓ Copied!" : "📋 Copy Token" }}</span>
          </button>
        </div>

        <!-- Integration Snippets -->
        <div class="mt-2 rounded-lg bg-zinc-900/80 p-3 text-xs space-y-2 border border-zinc-800">
          <p class="font-medium text-zinc-300">Mobile & API Integration Examples:</p>
          <div class="space-y-1 font-mono text-[11px] text-zinc-400">
            <div class="flex items-center justify-between bg-zinc-950 px-2 py-1.5 rounded">
              <span class="truncate">Header: <code>Authorization: Bearer &lt;token&gt;</code></span>
              <button
                type="button"
                class="text-[#9660fa] hover:underline ml-2"
                @click="copySnippet(`Authorization: Bearer ${generatedToken}`)"
              >
                Copy Header
              </button>
            </div>
            <div class="flex items-center justify-between bg-zinc-950 px-2 py-1.5 rounded">
              <span class="truncate">WebSocket: <code>wss://your-host/ws?token=&lt;token&gt;</code></span>
              <button
                type="button"
                class="text-[#9660fa] hover:underline ml-2"
                @click="copySnippet(`wss://${window.location.host}/ws?token=${generatedToken}`)"
              >
                Copy WS URL
              </button>
            </div>
          </div>
        </div>
      </div>

      <!-- Create New API Token Card -->
      <div class="mss-cfg-card space-y-4">
        <div>
          <h3 class="text-base font-semibold text-zinc-100 flex items-center gap-2">
            <span>🔑</span> Create Long-Lived API Token
          </h3>
          <p class="mss-cfg-note mt-0.5">
            Generate tokens for the Mobile App, Comfy Portal, external scripts, or Krita AI Diffusion.
          </p>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          <div>
            <label class="mss-cfg-label" for="mss-token-label">Token Name / Purpose</label>
            <input
              id="mss-token-label"
              v-model="newTokenLabel"
              type="text"
              class="mss-cfg-input"
              placeholder="e.g. iPad Mobile App, Comfy Portal"
            />
          </div>

          <div>
            <label class="mss-cfg-label" for="mss-token-expiry">Expiration</label>
            <select
              id="mss-token-expiry"
              v-model="newTokenExpiryHours"
              class="mss-cfg-input"
            >
              <option value="24">1 Day (24 hours)</option>
              <option value="168">7 Days (1 week)</option>
              <option value="720">30 Days (1 month)</option>
              <option value="2160">90 Days (3 months)</option>
              <option value="8760">1 Year (365 days)</option>
              <option value="custom">Custom hours</option>
              <option value="0">Never Expire (requires permission)</option>
            </select>
          </div>

          <div v-if="newTokenExpiryHours === 'custom'">
            <label class="mss-cfg-label" for="mss-token-custom-hours">Custom Hours</label>
            <input
              id="mss-token-custom-hours"
              v-model="customExpiryHours"
              type="number"
              min="1"
              step="1"
              class="mss-cfg-input"
              placeholder="Hours (e.g. 48)"
            />
          </div>

          <div>
            <label class="mss-cfg-label" for="mss-token-password">Re-enter Password</label>
            <input
              id="mss-token-password"
              v-model="newTokenPassword"
              type="password"
              class="mss-cfg-input"
              placeholder="Required for security confirmation"
              autocomplete="current-password"
            />
          </div>
        </div>

        <!-- MFA inputs if triggered -->
        <div v-if="mfaRequired" class="grid grid-cols-1 sm:grid-cols-2 gap-3 p-3 rounded-lg bg-zinc-950 border border-zinc-800">
          <div>
            <label class="mss-cfg-label" for="mss-token-mfa-code">MFA Authenticator Code</label>
            <input
              id="mss-token-mfa-code"
              v-model="mfaCode"
              type="text"
              class="mss-cfg-input font-mono"
              placeholder="6-digit TOTP code"
              maxlength="8"
            />
          </div>
          <div>
            <label class="mss-cfg-label" for="mss-token-mfa-backup">Or Backup Code</label>
            <input
              id="mss-token-mfa-backup"
              v-model="mfaBackupCode"
              type="text"
              class="mss-cfg-input font-mono"
              placeholder="XXXX-XXXX"
            />
          </div>
        </div>

        <div class="flex flex-wrap items-center gap-3 pt-1">
          <button
            type="button"
            class="mss-cfg-btn"
            :disabled="creatingToken"
            @click="handleCreateToken"
          >
            {{ creatingToken ? "Generating..." : "Generate API Token" }}
          </button>
          <span v-if="createStatus" :class="createStatusIsError ? 'text-red-400' : 'text-emerald-400'" class="text-xs">
            {{ createStatus }}
          </span>
        </div>
      </div>

      <!-- Active API Tokens List -->
      <div class="mss-cfg-card space-y-3">
        <div class="flex items-center justify-between">
          <div>
            <h3 class="text-base font-semibold text-zinc-100 flex items-center gap-2">
              <span>📋</span> Active API Tokens
            </h3>
            <p class="mss-cfg-note mt-0.5">
              Long-lived API tokens issued to your account.
            </p>
          </div>
          <button
            type="button"
            class="mss-cfg-btn-ghost text-xs py-1.5 px-3"
            :disabled="loadingTokens"
            @click="fetchApiTokens"
          >
            {{ loadingTokens ? "Refreshing..." : "↻ Refresh" }}
          </button>
        </div>

        <div v-if="loadingTokens" class="py-4 text-center text-xs text-zinc-400">
          Loading API tokens...
        </div>

        <div v-else-if="apiTokens.length === 0" class="py-6 text-center text-xs text-zinc-500">
          No active API tokens found. Create one above to connect mobile apps or external tools.
        </div>

        <div v-else class="overflow-x-auto rounded-lg border border-zinc-800">
          <table class="w-full text-left text-xs border-collapse">
            <thead class="bg-zinc-950 text-zinc-400 font-medium">
              <tr>
                <th class="px-3 py-2.5">Name / Purpose</th>
                <th class="px-3 py-2.5">Hash Prefix</th>
                <th class="px-3 py-2.5">Created</th>
                <th class="px-3 py-2.5">Last Used</th>
                <th class="px-3 py-2.5">Expires</th>
                <th class="px-3 py-2.5 text-right">Action</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-zinc-800 bg-zinc-900/40">
              <tr v-for="token in apiTokens" :key="token.id || token.token_hash_prefix" class="hover:bg-zinc-800/40 transition">
                <td class="px-3 py-2.5 font-medium text-zinc-200">
                  <span class="inline-flex items-center gap-1.5">
                    <span class="h-1.5 w-1.5 rounded-full bg-[#9660fa]"></span>
                    {{ token.label || "Unlabeled Token" }}
                  </span>
                </td>
                <td class="px-3 py-2.5 font-mono text-zinc-400">
                  {{ token.token_hash_prefix || "—" }}
                </td>
                <td class="px-3 py-2.5 text-zinc-400">
                  {{ formatDate(token.created_at_iso) }}
                </td>
                <td class="px-3 py-2.5 text-zinc-400">
                  {{ token.last_used_at_iso ? formatDate(token.last_used_at_iso) : "Never" }}
                </td>
                <td class="px-3 py-2.5">
                  <span
                    :class="isNeverExpires(token.expires_iso) ? 'text-purple-300 font-medium' : 'text-zinc-400'"
                  >
                    {{ isNeverExpires(token.expires_iso) ? "Never" : formatDate(token.expires_iso) }}
                  </span>
                </td>
                <td class="px-3 py-2.5 text-right">
                  <button
                    type="button"
                    class="mss-cfg-btn-danger text-[11px] py-1 px-2.5"
                    @click="handleRevokeToken(token)"
                  >
                    Revoke
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <!-- Active Session JWT Tokens (with Eye Reveal) -->
      <div class="mss-cfg-card space-y-3">
        <div class="flex items-center justify-between">
          <div>
            <h3 class="text-base font-semibold text-zinc-100 flex items-center gap-2">
              <span>🖥️</span> Active Browser Sessions
            </h3>
            <p class="mss-cfg-note mt-0.5">
              Active browser sessions with session JWTs. Click the eye icon to inspect your session token.
            </p>
          </div>
          <button
            type="button"
            class="mss-cfg-btn-ghost text-xs py-1.5 px-3"
            :disabled="loadingSessions"
            @click="fetchSessions"
          >
            {{ loadingSessions ? "Refreshing..." : "↻ Refresh" }}
          </button>
        </div>

        <div v-if="loadingSessions" class="py-4 text-center text-xs text-zinc-400">
          Loading sessions...
        </div>

        <div v-else-if="sessions.length === 0" class="py-6 text-center text-xs text-zinc-500">
          No session records found.
        </div>

        <div v-else class="overflow-x-auto rounded-lg border border-zinc-800">
          <table class="w-full text-left text-xs border-collapse">
            <thead class="bg-zinc-950 text-zinc-400 font-medium">
              <tr>
                <th class="px-3 py-2.5">Token (Masked)</th>
                <th class="px-3 py-2.5">Created</th>
                <th class="px-3 py-2.5">Last Active</th>
                <th class="px-3 py-2.5">Session Type</th>
                <th class="px-3 py-2.5 text-right">Actions</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-zinc-800 bg-zinc-900/40">
              <tr v-for="s in sessions" :key="s.jti" class="hover:bg-zinc-800/40 transition">
                <td class="px-3 py-2.5 font-mono text-zinc-300 max-w-[280px]">
                  <div class="flex items-center gap-2">
                    <span v-if="revealedTokens[s.jti]" class="break-all text-[11px] select-all bg-zinc-950 px-2 py-1 rounded border border-zinc-800">
                      {{ revealedTokens[s.jti] }}
                    </span>
                    <span v-else class="text-zinc-500">
                      ••••••••••••-{{ (s.jti || "").slice(-4) }}
                    </span>
                    <button
                      v-if="s.is_current"
                      type="button"
                      class="mss-cfg-btn-ghost h-6 w-6 p-0 shrink-0 text-sm"
                      :title="revealedTokens[s.jti] ? 'Hide token' : 'Reveal token'"
                      @click="toggleRevealSession(s)"
                    >
                      {{ revealedTokens[s.jti] ? "🔒" : "👁️" }}
                    </button>
                    <button
                      v-if="revealedTokens[s.jti]"
                      type="button"
                      class="text-[#9660fa] hover:underline text-[11px] shrink-0"
                      @click="copyToken(revealedTokens[s.jti])"
                    >
                      Copy
                    </button>
                  </div>
                </td>
                <td class="px-3 py-2.5 text-zinc-400">
                  {{ formatDate(s.created_at_iso) }}
                </td>
                <td class="px-3 py-2.5 text-zinc-400">
                  {{ s.last_used_at_iso ? formatDate(s.last_used_at_iso) : "—" }}
                </td>
                <td class="px-3 py-2.5">
                  <span
                    v-if="s.is_current"
                    class="inline-flex items-center gap-1 rounded bg-[#9660fa]/20 px-2 py-0.5 text-[11px] font-medium text-[#c4a4ff] border border-[#9660fa]/30"
                  >
                    Current Session
                  </span>
                  <span v-else class="text-zinc-500 text-[11px]">
                    Other Device
                  </span>
                </td>
                <td class="px-3 py-2.5 text-right">
                  <button
                    type="button"
                    class="mss-cfg-btn-danger text-[11px] py-1 px-2.5"
                    @click="handleRevokeSession(s)"
                  >
                    Revoke
                  </button>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </div>
</template>

<script>
import { apiJson } from "./api.js";

export default {
  name: "TokensSection",
  props: {
    currentUser: { type: Object, default: null },
  },
  data() {
    return {
      newTokenLabel: "",
      newTokenExpiryHours: "720",
      customExpiryHours: 48,
      newTokenPassword: "",
      creatingToken: false,
      createStatus: "",
      createStatusIsError: false,
      mfaRequired: false,
      mfaTempToken: null,
      mfaCode: "",
      mfaBackupCode: "",
      generatedToken: "",
      generatedLabel: "",
      copySuccess: false,
      loadingTokens: false,
      apiTokens: [],
      loadingSessions: false,
      sessions: [],
      revealedTokens: {},
    };
  },
  computed: {
    isGuest() {
      const u = (this.currentUser?.username || "").toLowerCase();
      return !u || u === "guest";
    },
  },
  async mounted() {
    if (!this.isGuest) {
      await Promise.all([this.fetchApiTokens(), this.fetchSessions()]);
    }
  },
  methods: {
    formatDate(isoStr) {
      if (!isoStr) return "N/A";
      try {
        const d = new Date(isoStr);
        if (isNaN(d.getTime())) return isoStr;
        return d.toLocaleDateString(undefined, {
          month: "short",
          day: "numeric",
          year: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        });
      } catch (_) {
        return isoStr;
      }
    },
    isNeverExpires(isoStr) {
      return (
        isoStr === "9999-12-31T23:59:59+00:00" ||
        isoStr === "9999-12-31T23:59:59Z" ||
        isoStr?.startsWith("9999")
      );
    },
    async fetchApiTokens() {
      this.loadingTokens = true;
      try {
        const { ok, data } = await apiJson("/mss-login/api/tokens");
        if (ok && Array.isArray(data?.tokens)) {
          this.apiTokens = data.tokens;
        }
      } catch (e) {
        console.error("[TokensSection] fetchApiTokens error:", e);
      } finally {
        this.loadingTokens = false;
      }
    },
    async fetchSessions() {
      this.loadingSessions = true;
      try {
        const { ok, data } = await apiJson("/mss-login/me/sessions");
        if (ok && Array.isArray(data?.sessions)) {
          this.sessions = data.sessions;
        }
      } catch (e) {
        console.error("[TokensSection] fetchSessions error:", e);
      } finally {
        this.loadingSessions = false;
      }
    },
    async handleCreateToken() {
      this.createStatus = "";
      this.createStatusIsError = false;

      let expireHours = this.newTokenExpiryHours;
      if (expireHours === "custom") {
        expireHours = String(this.customExpiryHours || "24");
      }

      if (!this.mfaTempToken && !this.newTokenPassword) {
        this.createStatus = "Please enter your password to confirm.";
        this.createStatusIsError = true;
        return;
      }

      this.creatingToken = true;

      try {
        const body = {
          label: this.newTokenLabel.trim(),
          expire_hours: expireHours,
          require_password_reauth: true,
          username: this.currentUser?.username || "",
        };

        if (this.mfaTempToken) {
          body.mfa_temp_token = this.mfaTempToken;
          if (this.mfaBackupCode.trim()) {
            body.backup_code = this.mfaBackupCode.trim();
          } else if (this.mfaCode.trim()) {
            body.code = this.mfaCode.trim().replace(/\s/g, "");
          }
        } else {
          body.password = this.newTokenPassword;
        }

        const { ok, data } = await apiJson("/mss-login/generate_token", {
          method: "POST",
          body,
        });

        if (!ok || !data) {
          this.createStatus = data?.error || "Failed to generate token.";
          this.createStatusIsError = true;
          this.creatingToken = false;
          return;
        }

        if (data.mfa_required && data.mfa_temp_token) {
          this.mfaRequired = true;
          this.mfaTempToken = data.mfa_temp_token;
          this.createStatus = "MFA code required. Enter code and submit again.";
          this.createStatusIsError = false;
          this.creatingToken = false;
          return;
        }

        if (data.jwt_token) {
          this.generatedToken = data.jwt_token;
          this.generatedLabel = this.newTokenLabel.trim();
          this.newTokenPassword = "";
          this.mfaCode = "";
          this.mfaBackupCode = "";
          this.mfaRequired = false;
          this.mfaTempToken = null;
          this.createStatus = "Token generated successfully!";
          this.createStatusIsError = false;
          await this.fetchApiTokens();
        } else {
          this.createStatus = data.message || "Token generated.";
        }
      } catch (err) {
        this.createStatus = "Error: " + (err?.message || String(err));
        this.createStatusIsError = true;
      } finally {
        this.creatingToken = false;
      }
    },
    async handleRevokeToken(token) {
      const label = token.label || token.token_hash_prefix || "this token";
      if (!window.confirm(`Revoke API token "${label}"? Connected applications will lose access immediately.`)) {
        return;
      }
      try {
        const prefix = String(token.token_hash_prefix || "").replace(/\.+$/, "");
        const { ok, data } = await apiJson("/mss-login/api/tokens", {
          method: "DELETE",
          body: { token_hash_prefix: prefix },
        });
        if (ok) {
          await this.fetchApiTokens();
        } else {
          alert(data?.error || "Failed to revoke token.");
        }
      } catch (e) {
        alert("Error: " + (e?.message || String(e)));
      }
    },
    async toggleRevealSession(session) {
      const jti = session.jti;
      if (this.revealedTokens[jti]) {
        const next = { ...this.revealedTokens };
        delete next[jti];
        this.revealedTokens = next;
        return;
      }

      try {
        const { ok, data } = await apiJson("/mss-login/me/current-token");
        if (!ok || !data) {
          alert(data?.error || "Unable to retrieve token.");
          return;
        }

        if (!data.is_https) {
          const proceed = window.confirm(
            "Connection is not secure (HTTP). Revealing the token over unencrypted HTTP may expose it. Proceed anyway?"
          );
          if (!proceed) return;
        }

        if (data.token) {
          this.revealedTokens = {
            ...this.revealedTokens,
            [jti]: data.token,
          };
        }
      } catch (err) {
        alert("Failed to reveal token: " + (err?.message || String(err)));
      }
    },
    async handleRevokeSession(session) {
      if (!window.confirm("Revoke this session? If this is your current device, you will be logged out.")) {
        return;
      }
      try {
        const { ok, data } = await apiJson("/mss-login/me/sessions/revoke", {
          method: "POST",
          body: { jti: session.jti },
        });
        if (ok && data?.status === "ok") {
          if (session.is_current) {
            window.location.href = "/logout";
            return;
          }
          await this.fetchSessions();
        } else {
          alert(data?.error || "Failed to revoke session.");
        }
      } catch (e) {
        alert("Error: " + (e?.message || String(e)));
      }
    },
    async copyToken(token) {
      if (!token) return;
      try {
        await navigator.clipboard.writeText(token);
        this.copySuccess = true;
        setTimeout(() => {
          this.copySuccess = false;
        }, 2500);
      } catch (_) {
        alert("Clipboard copy failed. Please select and copy manually.");
      }
    },
    async copySnippet(text) {
      if (!text) return;
      try {
        await navigator.clipboard.writeText(text);
        alert("Copied to clipboard!");
      } catch (_) {
        alert("Failed to copy snippet.");
      }
    },
  },
};
</script>
