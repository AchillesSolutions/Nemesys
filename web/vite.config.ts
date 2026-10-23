import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In development the Python server runs on :8000; Vite proxies the API (and its WebSockets) to it.
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: { input: { main: "index.html" } },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://127.0.0.1:8000", ws: true },
    },
  },
});
