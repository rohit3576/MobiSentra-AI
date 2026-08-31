import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

/**
 * Dev wiring (phase-9 plan, locked): same-origin everywhere — the Vite dev
 * server proxies `/api` and `/socket.io` (ws included) to the backend on
 * :3000, so the dashboard code never knows a CORS header exists.
 */
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://localhost:3000",
      "/socket.io": {
        target: "http://localhost:3000",
        ws: true,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    // `globals: false` — every test imports describe/it/expect from "vitest"
    // explicitly (matches verbatimModuleSyntax posture; no ambient types).
  },
});
