import { defineConfig } from "vite";

export default defineConfig({
  build: {
    rollupOptions: { output: {
      // Some static servers classify .mjs as text/plain; keep strict MIME/CSP checks intact.
      assetFileNames: asset => asset.names.some(name => name.startsWith("pdf.worker") && name.endsWith(".mjs"))
        ? "assets/[name]-[hash].js" : "assets/[name]-[hash][extname]",
    } },
  },
  server: {
    host: "127.0.0.1", port: 5173, strictPort: true,
    proxy: { "/api": { target: "http://127.0.0.1:8000", changeOrigin: false } },
  },
});
