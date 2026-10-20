// The page's ONE module script (CMX-215, CMX-212 P01): index.html loads this build entry
// through the manifest. Imports run in order, so the islands install window.chelaBridge
// BEFORE the legacy module graph (booted by boot.ts, bundled with it) publishes to it.
import './islands.tsx';
import './boot.ts';
