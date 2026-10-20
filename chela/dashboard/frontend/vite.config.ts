import react from '@vitejs/plugin-react';
import { loadEnv } from 'vite';
import { defineConfig } from 'vitest/config';

// CMX-177: the build lands in Flask's static dir and is COMMITTED, so users never need Node.
// tests/test_frontend_build.py fails while it differs from a fresh build of src/.
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  // CMX-215: chunk and asset URLs relative to the entry's own (versioned) URL, so a dashboard
  // behind a path prefix resolves them and they inherit static/v/<ASSET_VERSION>/ (CMX-426).
  base: './',
  build: {
    outDir: '../static/app',
    emptyOutDir: true,
    manifest: 'manifest.json',
    rollupOptions: { input: 'src/main.tsx', preserveEntrySignatures: 'strict' },
  },
  // CMX-178: `pnpm dev` serves index.html (a page of island slots) with hot reload and sends
  // /api to a running dashboard: CHELA_DEV_API, default the dashboard's default port.
  server: {
    proxy: { '/api': loadEnv(mode, '.', 'CHELA_DEV_').CHELA_DEV_API ?? 'http://127.0.0.1:5001' },
  },
  // CMX-218: Vitest stubs CSS imports to '' unless included; the UI kit's layout tests read
  // style.css (`?raw`) to check the rules the page actually ships.
  test: { environment: 'jsdom', css: { include: [/static\/style\.css/] } },
}));
