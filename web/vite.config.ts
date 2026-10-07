import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `auto-app web --dev`: this dev server (hot reload) proxies /api to the Python server
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: { "/api": { target: process.env.AUTO_APP_API ?? "http://127.0.0.1:8765", changeOrigin: true } },
  },
});
