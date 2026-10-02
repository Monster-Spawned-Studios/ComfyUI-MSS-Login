<template>
  <div
    class="mss-cfg-shell"
    style="min-height: min(80vh, 720px); width: min(960px, 96vw)"
  >
    <header class="mss-cfg-header">
      <button
        v-if="activeTab"
        type="button"
        class="mss-cfg-btn-ghost h-9 w-9 p-0"
        aria-label="Back to main menu"
        @click="goHome"
      >
        ←
      </button>
      <span v-else class="w-9" />
      <h2 class="mss-cfg-title">{{ title }}</h2>
      <button
        type="button"
        class="mss-cfg-btn-ghost h-9 w-9 p-0"
        aria-label="Close"
        @click="$emit('close')"
      >
        ✕
      </button>
    </header>

    <div class="mss-cfg-body">
      <div v-if="!activeTab" class="mss-cfg-home">
        <p class="mss-cfg-note mb-2">Choose a configuration section</p>
        <button
          v-for="tab in tabs"
          :key="tab.id"
          type="button"
          class="mss-cfg-home-btn"
          @click="openTab(tab.id)"
        >
          {{ tab.label }}
        </button>
      </div>

      <template v-else>
        <div v-if="activeTab === 'shared-models'">
          <SharedModelsSection :users-list="usersList" />
        </div>
        <div v-else-if="activeTab === 'model-download'">
          <ModelDownloadSection :current-user="currentUser" />
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
import LegacySectionHost from "./LegacySectionHost.vue";
import { isOwnerUser } from "./api.js";

export default {
  name: "ConfigApp",
  components: { SharedModelsSection, ModelDownloadSection, LegacySectionHost },
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
    };
  },
  computed: {
    tabs() {
      const owner = isOwnerUser(this.currentUser);
      const builtIn = [
        { id: "users", label: "Users & Roles", order: 0 },
        { id: "perms", label: "Permissions & UI", order: 1 },
        { id: "shared-models", label: "Shared Models", order: 2 },
        { id: "model-download", label: "Model download", order: 3 },
        ...(owner ? [{ id: "s3", label: "S3 Settings", order: 4 }] : []),
        { id: "ip", label: "IP Rules", order: 5 },
        { id: "env", label: "User Env", order: 6 },
        { id: "nsfw", label: "NSFW Management", order: 7 },
        { id: "token-storage", label: "Token Storage", order: 8 },
        { id: "users-db", label: "Users DB", order: 9 },
      ];
      const ext = (this.extensionTabs || []).map((t) => ({
        id: t.id,
        label: t.label,
        order: t.order ?? 100,
      }));
      return [...builtIn, ...ext].sort((a, b) => a.order - b.order);
    },
    title() {
      if (!this.activeTab) return "MSS-Login Security Policy";
      const tab = this.tabs.find((t) => t.id === this.activeTab);
      return tab?.label || "MSS-Login";
    },
    legacyContext() {
      return {
        usersList: this.usersList,
        groupsConfig: this.groupsConfig,
        currentUser: this.currentUser,
      };
    },
  },
  methods: {
    goHome() {
      this.activeTab = "";
    },
    openTab(id) {
      this.activeTab = id;
    },
  },
  mounted() {
    if (this.preferTab) {
      this.activeTab = this.preferTab;
    }
  },
};
</script>
