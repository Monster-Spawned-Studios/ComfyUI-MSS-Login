<template>
  <div
    ref="host"
    class="mss-cfg-section min-h-[200px] text-left"
    @click.stop
    @mousedown.stop
  />
</template>

<script>
export default {
  name: "LegacySectionHost",
  props: {
    tabId: { type: String, required: true },
    renderFn: { type: Function, default: null },
    context: { type: Object, default: () => ({}) },
  },
  watch: {
    tabId: {
      immediate: true,
      async handler() {
        await this.$nextTick();
        await this.runRender();
      },
    },
  },
  methods: {
    async runRender() {
      const el = this.$refs.host;
      if (!el || typeof this.renderFn !== "function") {
        if (el) {
          el.innerHTML =
            '<p class="mss-cfg-note text-center">This section is unavailable.</p>';
        }
        return;
      }
      el.innerHTML = "";
      try {
        await this.renderFn(el, this.context);
      } catch (err) {
        console.error("[mss-login] legacy section render failed:", err);
        el.innerHTML = `<p class="mss-cfg-note text-center">Failed to render section: ${String(
          err?.message || err,
        )}</p>`;
      }
    },
  },
};
</script>
