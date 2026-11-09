import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Construction : `npm --prefix web run build` écrit `web/dist`, servi par le backend (`/j/<jeton>`,
// `/assets/...`). Deuxième page : `recording.html`, la mise en page filmée par Egress
// (`/recording-template`). Développement de l'interface seule : `npm --prefix web run dev`
// (port 5173) ; les appels `/v1` sont relayés vers le backend lancé sur le port 8000.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "dist",
    assetsDir: "assets",
    sourcemap: false,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      input: { main: "index.html", recording: "recording.html" },
      // Noms de fichiers neutres : rien de visible (inspecteur, onglet Réseau) ne nomme la brique média.
      output: {
        chunkFileNames: "assets/c-[hash].js",
        entryFileNames: "assets/e-[hash].js",
        assetFileNames: "assets/a-[hash][extname]",
      },
    },
  },
  server: { port: 5173, proxy: { "/v1": "http://localhost:8000" } },
});
