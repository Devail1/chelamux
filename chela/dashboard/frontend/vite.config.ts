import react from '@vitejs/plugin-react';
import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';

// CMX-177: the build lands in Flask's static dir and is COMMITTED, so users never need Node.
// tests/test_frontend_build.py fails while it differs from a fresh build of src/.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  build: {
    outDir: '../static/app',
    emptyOutDir: true,
    manifest: 'manifest.json',
    rollupOptions: {
      // CMX-276: the sidebar model is a second entry at a FIXED name, so the legacy modules
      // (not in the Vite graph yet, CMX-212 P01) import it: `../app/sidebarmodel.js`.
      input: { main: 'src/main.tsx', sidebarmodel: 'src/features/sidebar/model.ts' },
      preserveEntrySignatures: 'strict',
      output: { entryFileNames: c => (c.name === 'sidebarmodel' ? '[name].js' : 'assets/[name]-[hash].js') },
    },
  },
  // CMX-178: `pnpm dev` serves index.html (a page of island slots) with hot reload and sends
  // /api to a running dashboard: CHELA_DEV_API, default the dashboard's default port.
  server: {
    proxy: { '/api': loadEnv(mode, '.', 'CHELA_DEV_').CHELA_DEV_API ?? 'http://127.0.0.1:5001' },
  },
  test: { environment: 'jsdom' },
}));
