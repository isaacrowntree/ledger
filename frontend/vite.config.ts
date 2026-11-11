import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  root: ".",
  server: {
    // 127.0.0.1, not localhost: another local service can hold port 5050 on
    // IPv6, and "localhost" resolves there first.
    proxy: { "/api": "http://127.0.0.1:5050" },
  },
  build: { outDir: "dist", emptyOutDir: true },
});
