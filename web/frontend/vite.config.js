import path from "node:path";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig({
	plugins: [vue()],
	build: {
		outDir: path.resolve(__dirname, "../dist"),
		emptyOutDir: false,
		rollupOptions: {
			input: {
				auth: path.resolve(__dirname, "src/auth-app.js"),
			},
			output: {
				entryFileNames: "[name]-app.js",
				assetFileNames: "[name][extname]",
			},
		},
	},
});
