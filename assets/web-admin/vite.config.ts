import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig(({ mode }) => ({
  define: {
    "import.meta.env.VITE_STUDIO_DESKTOP": JSON.stringify(
      mode === "desktop" ? "true" : "false",
    ),
  },
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: {
      "/api": {
        target: "http://127.0.0.1:18880",
        changeOrigin: true,
        headers: { origin: "http://127.0.0.1:18880" },
      },
    },
  },
}));
