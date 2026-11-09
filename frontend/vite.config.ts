import { defineConfig } from 'vite';

// The client is built into the FastAPI static mount so the existing server-rendered pages can load it with
// a plain same-origin <script type="module"> (the app's CSP is script-src 'self'; no inline scripts, no CDN).
export default defineConfig({
  base: '/static/app/',
  build: {
    outDir: '../shopping/web/static/app',
    emptyOutDir: true,
    sourcemap: false,
    assetsDir: 'assets',
    rollupOptions: {
      input: { main: 'src/main.ts' },
      output: { entryFileNames: 'assets/[name].js', chunkFileNames: 'assets/[name].js', assetFileNames: 'assets/[name][extname]' },
    },
  },
  server: {
    port: 5173,
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
});
