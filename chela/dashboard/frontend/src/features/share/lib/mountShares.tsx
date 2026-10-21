// CMX-240: the share islands: the pill's two copies and the Active shares sheet (it portals to
// <body>; its slot only holds the root). A page without a slot (terminals off) mounts nothing.
import { mountIsland } from '../../../shared/islands/index.tsx';
import { SharesPill } from './SharesPill.tsx';
import { SharesSheet } from './SharesSheet.tsx';

export function mountShares(doc: Document = document) {
  mountIsland('shares-pill-float', SharesPill, { id: 'btn-shares' }, doc);
  mountIsland('shares-pill-row', SharesPill, { id: 'term-shares' }, doc);
  mountIsland('shares-sheet-island', SharesSheet, {}, doc);
}
