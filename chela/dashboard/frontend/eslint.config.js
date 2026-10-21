import js from '@eslint/js';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  { ignores: ['dist'] },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.strict],
    languageOptions: { globals: globals.browser },
    plugins: { 'react-hooks': reactHooks },
    rules: reactHooks.configs.recommended.rules,
  },
  {
    // CMX-301: every React root is made by createIslandRoot, which marks its host for nav.js's
    // touch tap-tip; a bare createRoot would bring back taps that never reach React's onClick.
    files: ['src/**/*.{ts,tsx}'],
    ignores: ['src/shared/islands/index.tsx', 'src/**/tests/**', 'src/**/*.test.{ts,tsx}'],
    rules: {
      'no-restricted-imports': ['error', {
        paths: [{ name: 'react-dom/client', importNames: ['createRoot', 'hydrateRoot'], message: 'Use createIslandRoot (shared/islands): it marks the host [data-island] for nav.js (CMX-301).' }],
      }],
    },
  },
);
