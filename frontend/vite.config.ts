import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

// The engine. In development the app runs on :5173 and every API call is
// proxied here; in production the engine serves the built app itself.
const ENGINE = process.env.MAHORAGA_ENGINE ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": path.resolve(__dirname, "src") },
  },
  server: {
    port: 5173,
    // brand/tokens.css lives one level up, shared with the landing page.
    fs: { allow: [".."] },
    proxy: {
      "/api": { target: ENGINE, changeOrigin: true },
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: false,
  },
});
