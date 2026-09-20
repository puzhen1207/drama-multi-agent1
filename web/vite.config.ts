import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.VITE_API_TARGET || "http://127.0.0.1:8000";
  return {
    plugins: [vue(), viteSingleFile()],
    base: "./",
    server: {
      port: 5173,
      proxy: {
        "/health": apiTarget,
        "/v1": apiTarget,
        "/docs": apiTarget,
      },
    },
    build: {
      outDir: "../frontend",
      emptyOutDir: true,
      assetsInlineLimit: 100_000_000,
      cssCodeSplit: false,
    },
  };
});
