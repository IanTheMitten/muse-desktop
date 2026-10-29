import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run dev` talks to the real server over the tailnet, so the dev UI runs
// against the real 723 tracks rather than a fixture. MUSE_DEV_API overrides it.
const api = process.env.MUSE_DEV_API ?? "http://127.0.0.1:8080";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // reachable from the phone while developing
    proxy: { "/api": { target: api, changeOrigin: true } },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
