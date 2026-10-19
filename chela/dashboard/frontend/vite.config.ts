import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

// CMX-177: the build lands in Flask's static dir and is COMMITTED, so users never need Node.
// tests/test_frontend_build.py fails while it differs from a fresh build of src/.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: '../static/app',
    emptyOutDir: true,
    manifest: 'manifest.json',
    rollupOptions: { input: 'src/main.tsx', preserveEntrySignatures: 'strict' },
  },
  test: { environment: 'jsdom' },
});
