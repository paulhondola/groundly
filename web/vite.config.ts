import { svelte } from "@sveltejs/vite-plugin-svelte";
import tailwindcss from "@tailwindcss/vite";
import { defineConfig } from "vite-plus";

export default defineConfig({
  plugins: [svelte(), tailwindcss()],
  resolve: {
    conditions: ["browser"],
  },
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
        headers: {
          host: "127.0.0.1:8765",
          origin: "http://127.0.0.1:8765",
        },
      },
      "^/s/[^/]+/materials/": {
        target: "http://127.0.0.1:8765",
        changeOrigin: true,
        headers: {
          host: "127.0.0.1:8765",
        },
      },
    },
  },
  build: {
    outDir: "../groundly/web/static",
    emptyOutDir: false,
    cssCodeSplit: false,
    rolldownOptions: {
      input: "src/main.ts",
      output: {
        format: "iife",
        inlineDynamicImports: true,
        entryFileNames: "page.js",
        assetFileNames: "page.[ext]",
      },
    },
  },
  test: {
    environment: "jsdom",
  },
});
