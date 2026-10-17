import { createApp, h } from "vue";
import ConfigApp from "./config/ConfigApp.vue";
import "./config.css";

/**
 * Mount the Vue + Tailwind MSS-Login config dialog.
 * Returns an unmount function.
 *
 * @param {HTMLElement} el
 * @param {object} props
 */
export function mountMssLoginConfig(el, props = {}) {
	if (!el) throw new Error("mountMssLoginConfig: missing element");
	const app = createApp({
		render() {
			return h(ConfigApp, {
				currentUser: props.currentUser || null,
				usersList: props.usersList || [],
				groupsConfig: props.groupsConfig || {},
				extensionTabs: props.extensionTabs || [],
				legacyRenderers: props.legacyRenderers || {},
				preferTab: props.preferTab || "",
				onClose: () => {
					if (typeof props.onClose === "function") props.onClose();
				},
			});
		},
	});
	app.mount(el);
	return () => {
		try {
			app.unmount();
		} catch (_) {}
	};
}

window.mssLoginMountConfig = mountMssLoginConfig;
