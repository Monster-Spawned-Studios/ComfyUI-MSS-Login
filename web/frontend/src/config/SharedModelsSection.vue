<template>
  <div class="mss-cfg-section" @click.stop>
    <div class="mss-cfg-card space-y-3">
      <h3 class="text-center text-base font-semibold">
        Shared Models / LoRAs / VAEs
      </h3>
      <p class="mss-cfg-note text-center">
        Grant specific users access to specific ComfyUI items. Expand folders
        with the large chevron — clicking near the edge will not close this
        dialog.
      </p>
      <label class="mss-cfg-label">User</label>
      <select
        v-model="selectedUser"
        class="mss-cfg-input text-center"
        @change="onUserChange"
      >
        <option value="">-- Select user --</option>
        <option v-for="u in users" :key="u.username" :value="u.username">
          {{ u.username }}
        </option>
      </select>
      <div class="flex justify-center gap-2">
        <button
          type="button"
          class="mss-cfg-btn-ghost"
          @click.stop="refreshFolders"
        >
          Refresh folders
        </button>
      </div>
      <p v-if="status" class="mss-cfg-note text-center">{{ status }}</p>
    </div>

    <div v-if="selectedUser" class="mss-cfg-card space-y-2">
      <h4 class="text-center text-sm font-semibold">Toggle model access</h4>
      <p v-if="!folders.length" class="mss-cfg-note text-center">
        No folders loaded. Click Refresh folders.
      </p>
      <div
        v-for="folder in folders"
        :key="folder"
        class="space-y-1"
        @mousedown.stop
        @click.stop
      >
        <div class="mss-cfg-folder-row">
          <button
            type="button"
            class="mss-cfg-folder-chevron"
            :aria-expanded="openFolders[folder] ? 'true' : 'false'"
            :aria-label="
              (openFolders[folder] ? 'Collapse ' : 'Expand ') + folder
            "
            @click.stop.prevent="toggleFolder(folder)"
          >
            {{ openFolders[folder] ? "▾" : "▸" }}
          </button>
          <button
            type="button"
            class="mss-cfg-folder-label"
            @click.stop.prevent="toggleFolder(folder)"
          >
            {{ folder }}
          </button>
        </div>
        <div
          v-if="openFolders[folder]"
          class="mss-cfg-flyout"
          @mousedown.stop
          @click.stop
        >
          <p v-if="folderLoading[folder]" class="mss-cfg-note">Loading…</p>
          <label
            v-for="item in folderItems[folder] || []"
            :key="folder + '|' + item"
            class="mb-1 flex items-center gap-2 text-sm"
          >
            <input
              type="checkbox"
              :checked="sharedSet.has(folder + '|' + item)"
              @change="onToggleItem(folder, item, $event.target.checked)"
            />
            <span class="break-all text-left">{{ item }}</span>
          </label>
          <p
            v-if="!folderLoading[folder] && !(folderItems[folder] || []).length"
            class="mss-cfg-note"
          >
            No items in this folder.
          </p>
        </div>
      </div>
    </div>

    <div v-if="selectedUser" class="mss-cfg-card">
      <h4 class="mb-2 text-center text-sm font-semibold">Currently shared</h4>
      <ul v-if="sharedList.length" class="space-y-1 text-left text-sm">
        <li v-for="it in sharedList" :key="it.folder + '|' + it.item_name">
          {{ it.folder }} / {{ it.item_name }}
        </li>
      </ul>
      <p v-else class="mss-cfg-note text-center">
        No shared items for this user.
      </p>
    </div>
  </div>
</template>

<script>
import { apiJson } from "./api.js";

export default {
  name: "SharedModelsSection",
  props: {
    usersList: { type: Array, default: () => [] },
  },
  data() {
    return {
      selectedUser: "",
      folders: [],
      openFolders: {},
      folderItems: {},
      folderLoading: {},
      sharedSet: new Set(),
      sharedList: [],
      status: "",
    };
  },
  computed: {
    users() {
      return (this.usersList || []).filter(
        (u) => (u.username || "").toLowerCase() !== "guest",
      );
    },
  },
  methods: {
    async onUserChange() {
      this.openFolders = {};
      this.folderItems = {};
      await this.fetchShared();
    },
    async fetchShared() {
      this.sharedSet = new Set();
      this.sharedList = [];
      if (!this.selectedUser) return;
      const { ok, data } = await apiJson(
        `/mss-login/api/users/${encodeURIComponent(this.selectedUser)}/shared-items`,
      );
      if (!ok) {
        this.status = data?.error || "Failed to load shared items";
        return;
      }
      const items = data?.items || [];
      this.sharedList = items;
      const next = new Set();
      items.forEach((it) =>
        next.add(`${it.folder || ""}|${it.item_name || ""}`),
      );
      this.sharedSet = next;
    },
    async refreshFolders() {
      this.status = "Loading folders…";
      let { ok, data } = await apiJson("/mss-login/api/model-cache/folders");
      if (!ok) {
        ({ ok, data } = await apiJson("/mss-login/api/model-download/folders"));
      }
      this.folders = data?.folders || [];
      this.status = this.folders.length
        ? `Loaded ${this.folders.length} folders`
        : "No folders found";
    },
    async toggleFolder(folder) {
      const willOpen = !this.openFolders[folder];
      this.openFolders = { ...this.openFolders, [folder]: willOpen };
      if (!willOpen) return;
      if (this.folderItems[folder]) return;
      this.folderLoading = { ...this.folderLoading, [folder]: true };
      let { ok, data } = await apiJson(
        `/mss-login/api/model-cache/folders/${encodeURIComponent(folder)}/items`,
      );
      if (!ok) {
        ({ ok, data } = await apiJson(
          `/mss-login/api/available-models/${encodeURIComponent(folder)}`,
        ));
      }
      this.folderItems = {
        ...this.folderItems,
        [folder]: data?.items || [],
      };
      this.folderLoading = { ...this.folderLoading, [folder]: false };
    },
    async onToggleItem(folder, item, checked) {
      if (!this.selectedUser) return;
      const key = `${folder}|${item}`;
      const url = `/mss-login/api/users/${encodeURIComponent(this.selectedUser)}/shared-items`;
      if (checked) {
        await apiJson(url, {
          method: "POST",
          body: { folder, item_name: item, source_backend: "local" },
        });
        this.sharedSet.add(key);
      } else {
        await apiJson(url, {
          method: "DELETE",
          body: { folder, item_name: item },
        });
        this.sharedSet.delete(key);
      }
      this.sharedSet = new Set(this.sharedSet);
      await this.fetchShared();
    },
  },
  async mounted() {
    await this.refreshFolders();
  },
};
</script>
