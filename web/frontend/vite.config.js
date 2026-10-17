import path from "node:path";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
	plugins: [vue()],
	build: {
		outDir: path.resolve(__dirname, "../dist"),
		emptyOutDir: false,
		sourcemap: false,
		cssCodeSplit: true,
		rollupOptions: {
			input: {
				auth: path.resolve(__dirname, "src/auth-app.js"),
				config: path.resolve(__dirname, "src/config-app.js"),
			},
			output: {
				entryFileNames: "[name]-app.js",
				assetFileNames: "[name][extname]",
				chunkFileNames: "[name].js",
				manualChunks(id) {
					if (id.includes("node_modules")) {
						return "vue-vendor";
					}
				},
			},
		},
	},
});
