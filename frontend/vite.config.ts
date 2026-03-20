import { defineConfig } from "vite";

export default defineConfig({
  root: ".",
  server: {
    proxy: {
      "/api": "http://localhost:5050",
    },
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
  },
});
