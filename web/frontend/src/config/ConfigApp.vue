<template>
  <div
    class="mss-cfg-shell relative overflow-hidden"
    :class="{ 'mss-cfg-shell-glass': !!bgMedia.src }"
    :style="shellStyle"
  >
    <!-- Background layer if custom background is active -->
    <div
      v-if="bgMedia.src"
      class="absolute inset-0 -z-10 overflow-hidden pointer-events-none"
    >
      <video
        v-if="bgMedia.kind === 'video'"
        :src="bgMedia.src"
        autoplay
        loop
        muted
        playsinline
        class="h-full w-full object-cover scale-105 filter blur-sm brightness-[0.35]"
      />
      <div
        v-else
        class="h-full w-full bg-cover bg-center scale-105 filter blur-sm brightness-[0.35]"
        :style="{ backgroundImage: `url('${bgMedia.src}')` }"
      />
      <div class="absolute inset-0 bg-gradient-to-b from-zinc-950/70 via-zinc-950/85 to-zinc-950/95" />
    </div>

    <!-- Header -->
    <header class="mss-cfg-header backdrop-blur-md bg-zinc-950/60 sticky top-0 z-20">
      <button
        v-if="activeTab"
        type="button"
        class="mss-cfg-btn-ghost h-9 px-3 gap-1.5 text-xs font-medium"
        aria-label="Back to main menu"
        @click="goHome"
      >
        <span>←</span>
        <span class="hidden sm:inline">Back</span>
      </button>
      <span v-else class="w-9" />

      <div class="flex-1 text-center">
        <h2 class="mss-cfg-title text-base sm:text-lg flex items-center justify-center gap-2">
          <span class="text-[#9660fa] font-bold">MSS-Login</span>
          <span class="text-zinc-400">·</span>
          <span>{{ title }}</span>
        </h2>
      </div>

      <button
        type="button"
        class="mss-cfg-btn-ghost h-9 w-9 p-0 text-zinc-400 hover:text-white"
        aria-label="Close"
        @click="$emit('close')"
      >
        ✕
      </button>
    </header>

    <!-- Body -->
    <div class="mss-cfg-body flex-1 overflow-y-auto px-3 sm:px-6 py-4">
      <!-- Home view -->
      <div v-if="!activeTab" class="mss-cfg-home space-y-4">
        <!-- User Status Card -->
        <div class="w-full max-w-4xl rounded-xl border border-zinc-800 bg-zinc-900/60 p-4 backdrop-blur-sm flex flex-col sm:flex-row items-center justify-between gap-3 text-center sm:text-left">
          <div class="flex items-center gap-3">
            <div class="h-10 w-10 rounded-full bg-gradient-to-tr from-[#9660fa] to-[#ff477e] flex items-center justify-center text-white font-bold text-sm shadow-md">
              {{ (currentUser?.username || "U").slice(0, 2).toUpperCase() }}
            </div>
            <div>
              <div class="text-sm font-semibold text-zinc-100 flex items-center gap-2 justify-center sm:justify-start">
                <span>{{ currentUser?.username || "Current User" }}</span>
                <span
                  v-if="isOwner"
                  class="rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-amber-300 border border-amber-500/30"
                >
                  Owner
                </span>
                <span
                  v-else-if="currentUser?.is_admin"
                  class="rounded bg-purple-500/20 px-1.5 py-0.5 text-[10px] font-semibold text-purple-300 border border-purple-500/30"
                >
                  Admin
                </span>
              </div>
              <p class="text-xs text-zinc-400 mt-0.5">
                Role(s): {{ rolesText }}
              </p>
            </div>
          </div>

          <div class="flex flex-wrap items-center justify-center gap-2">
            <button
              v-if="!isGuest"
              type="button"
              class="mss-cfg-btn-ghost text-xs py-1.5 px-3"
              @click="openTab('tokens')"
            >
              🔑 Manage Tokens
            </button>
            <button
              type="button"
              class="mss-cfg-btn-danger text-xs py-1.5 px-3"
              @click="handleLogout"
            >
              Logout
            </button>
          </div>
        </div>

        <p class="mss-cfg-note w-full text-center">
          Select a configuration section below:
        </p>

        <!-- Home Grid of Section Cards -->
        <div class="mss-cfg-home-grid">
          <button
            v-for="tab in tabs"
            :key="tab.id"
            type="button"
            class="mss-cfg-home-btn group flex flex-col items-center justify-center p-4 text-center transition"
            @click="openTab(tab.id)"
          >
            <span class="text-xl mb-1.5 group-hover:scale-110 transition-transform">
              {{ getTabIcon(tab.id) }}
            </span>
            <span class="font-medium text-sm text-zinc-200 group-hover:text-white">
              {{ tab.label }}
            </span>
          </button>
        </div>
      </div>

      <!-- Active Section View -->
      <template v-else>
        <div v-if="activeTab === 'tokens'">
          <TokensSection :current-user="currentUser" />
        </div>
        <div v-else-if="activeTab === 'shared-models'">
          <SharedModelsSection :users-list="usersList" />
        </div>
        <div v-else-if="activeTab === 'model-download'">
          <ModelDownloadSection :current-user="currentUser" />
        </div>
        <div v-else-if="activeTab === 'login-appearance'">
          <LoginAppearanceSection />
        </div>
        <div v-else-if="activeTab === 'experimental'">
          <ExperimentalSettingsSection />
        </div>
        <div v-else>
          <LegacySectionHost
            :tab-id="activeTab"
            :render-fn="legacyRenderers[activeTab] || null"
            :context="legacyContext"
          />
        </div>
      </template>
    </div>
  </div>
</template>

<script>
import SharedModelsSection from "./SharedModelsSection.vue";
import ModelDownloadSection from "./ModelDownloadSection.vue";
import LoginAppearanceSection from "./LoginAppearanceSection.vue";
import ExperimentalSettingsSection from "./ExperimentalSettingsSection.vue";
import TokensSection from "./TokensSection.vue";
import LegacySectionHost from "./LegacySectionHost.vue";
import { isOwnerUser, apiJson } from "./api.js";

export default {
  name: "ConfigApp",
  components: {
    SharedModelsSection,
    ModelDownloadSection,
    LoginAppearanceSection,
    ExperimentalSettingsSection,
    TokensSection,
    LegacySectionHost,
  },
  props: {
    currentUser: { type: Object, default: null },
    usersList: { type: Array, default: () => [] },
    groupsConfig: { type: Object, default: () => ({}) },
    extensionTabs: { type: Array, default: () => [] },
    legacyRenderers: { type: Object, default: () => ({}) },
    preferTab: { type: String, default: "" },
  },
  emits: ["close"],
  data() {
    return {
      activeTab: "",
      bgMedia: {
        src: "",
        kind: "image",
      },
    };
  },
  computed: {
    isOwner() {
      return isOwnerUser(this.currentUser);
    },
    isGuest() {
      const u = (this.currentUser?.username || "").toLowerCase();
      return !u || u === "guest";
    },
    rolesText() {
      const groups = Array.isArray(this.currentUser?.groups) ? this.currentUser.groups : [];
      if (groups.length) return groups.join(", ");
      if (this.currentUser?.is_admin) return "admin";
      return this.isGuest ? "guest" : "user";
    },
    shellStyle() {
      return {
        minHeight: "min(84vh, 760px)",
        maxHeight: "88vh",
        width: "min(1040px, 96vw)",
      };
    },
    tabs() {
      const owner = this.isOwner;
      const isAdmin = !!(this.currentUser && this.currentUser.is_admin);
      const canDownload = !!(
        this.currentUser &&
        (this.currentUser.can_download_models || isAdmin)
      );

      // Non-admins with download permission
      if (!isAdmin && canDownload) {
        return [
          { id: "model-download", label: "Model download", order: 0 },
          ...(!this.isGuest ? [{ id: "tokens", label: "Tokens & API Keys", order: 1 }] : []),
        ];
      }

      // Non-admins without download permission (standard user)
      if (!isAdmin) {
        return !this.isGuest ? [{ id: "tokens", label: "Tokens & API Keys", order: 0 }] : [];
      }

      // Admins & Owners have full matrix
      const builtIn = [
        { id: "users", label: "Users & Roles", order: 0 },
        { id: "perms", label: "Permissions & UI", order: 1 },
        { id: "tokens", label: "Tokens & API Keys", order: 2 },
        { id: "shared-models", label: "Shared Models", order: 3 },
        { id: "model-download", label: "Model download", order: 4 },
        ...(owner ? [{ id: "s3", label: "S3 Settings", order: 5 }] : []),
        { id: "ip", label: "IP Rules", order: 6 },
        { id: "env", label: "User Env", order: 7 },
        { id: "nsfw", label: "NSFW Management", order: 8 },
        { id: "token-storage", label: "Token Storage", order: 9 },
        { id: "users-db", label: "Users DB", order: 10 },
        ...(owner
          ? [{ id: "login-appearance", label: "Login Appearance", order: 11 }]
          : []),
        { id: "experimental", label: "Experimental Settings", order: 12 },
      ];

      const ext = (this.extensionTabs || []).map((t) => ({
        id: t.id,
        label: t.label,
        order: t.order ?? 100,
      }));

      return [...builtIn, ...ext].sort((a, b) => a.order - b.order);
    },
    title() {
      if (!this.activeTab) return "Control Center";
      const tab = this.tabs.find((t) => t.id === this.activeTab);
      return tab?.label || "Control Center";
    },
    legacyContext() {
      return {
        usersList: this.usersList,
        groupsConfig: this.groupsConfig,
        currentUser: this.currentUser,
      };
    },
  },
  async mounted() {
    if (this.preferTab) {
      this.activeTab = this.preferTab;
    }
    await this.loadBackgroundMedia();
  },
  methods: {
    goHome() {
      this.activeTab = "";
    },
    openTab(id) {
      this.activeTab = id;
    },
    handleLogout() {
      if (typeof window.mssLoginLogout === "function") {
        window.mssLoginLogout();
        return;
      }
      window.location.href = "/logout";
    },
    getTabIcon(tabId) {
      const icons = {
        users: "👥",
        perms: "🛡️",
        tokens: "🔑",
        "shared-models": "📦",
        "model-download": "⬇️",
        s3: "☁️",
        ip: "🌐",
        env: "⚙️",
        nsfw: "👁️",
        "token-storage": "🔐",
        "users-db": "🗄️",
        "login-appearance": "🎨",
        experimental: "🧪",
      };
      return icons[tabId] || "📁";
    },
    async loadBackgroundMedia() {
      try {
        const { ok, data } = await apiJson("/mss-login/api/login-background");
        if (ok && data?.enabled && data?.src) {
          this.bgMedia = {
            src: data.src,
            kind: data.media_kind || "image",
          };
        }
      } catch (_) {}
    },
  },
};
</script>
