import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// Built into cvmaker/static/editor and served by CVMaker at /editor/ (run `npm run build` after changing the editor).
// `npm run dev` serves the editor on :5179 with hot reload and sends API calls to CVMaker on :8000.
export default defineConfig({
  base: '/editor/',
  plugins: [react()],
  server: { port: 5179, proxy: { '/editor/api': 'http://localhost:8000', '/editor/uploads': 'http://localhost:8000' } },
  build: {
    outDir: '../cvmaker/static/editor',
    emptyOutDir: true,
    // Fixed names: CVMaker inlines these two files into the HTML it prints to PDF.
    rollupOptions: { output: { entryFileNames: 'editor.js', assetFileNames: 'editor.[ext]', inlineDynamicImports: true } },
  },
});
