import { useState } from 'react';
export function C({ on }: { on: boolean }) {
  if (on) { useState(0); }
  return <p>{String(on)}</p>;
}
