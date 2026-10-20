// CMX-178: the demo island. The deploy's asset version (app.py ASSET_VERSION) from
// /api/version, shown in the Settings modal foot (CMX-291; it overlapped the sidebar foot's
// resources strip). It renders nothing until a version is known: a failed read never shows a
// made-up one.
import { useEffect, useState } from 'react';

export function VersionBadge({ base = window.location.pathname.replace(/\/$/, '') }: { base?: string }) {
  const [version, setVersion] = useState('');
  useEffect(() => {
    let live = true;
    fetch(`${base}/api/version`)
      .then((res) => (res.ok ? res.json() : null))
      .then((body: { version?: unknown } | null) => {
        if (live && typeof body?.version === 'string') setVersion(body.version);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [base]);
  if (!version) return null;
  return (
    <span className="version-badge" title="Dashboard build">
      build {version}
    </span>
  );
}
