import { defineConfig } from "vite";

export default defineConfig({
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules")) {
            if (id.includes("/recharts/") || id.includes("/victory-vendor/"))
              return "charts";
            if (
              id.includes("/react/") ||
              id.includes("/react-dom/") ||
              id.includes("/scheduler/")
            )
              return "react-vendor";
          }
        },
      },
    },
  },
  server: { proxy: { "/api": "http://localhost:8000" } },
});
