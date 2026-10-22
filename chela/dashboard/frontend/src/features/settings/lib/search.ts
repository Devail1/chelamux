// CMX-396: VS Code-style search across EVERY tab, over the rows the tabs rendered. Filtering
// only marks them (data-s-hide / data-s-hit, attributes React never renders, so a re-render
// keeps them): nothing is re-rendered or moved, and an empty query strips every mark, leaving
// the modal exactly as drawn.
//
// The unit is a labelled row (one knob: a FieldRow). A section whose OWN text (its heading,
// help text .s-desc / .s-examples, or data-keywords) matches shows whole; otherwise only its
// labelled rows whose label or data-keywords match. Unlabelled rows (a Save button) stay with
// any section that shows, so a filtered Timing row can still be saved.
import { searchMatches, searchTerms } from '../model.ts';

export const HIDE = 'data-s-hide';
export const HIT = 'data-s-hit';

function sectionText(sec: Element): string {
  const h = sec.querySelector('h4');
  const own = [...sec.querySelectorAll('.s-desc, .s-examples')].map((el) => el.textContent);
  return [h?.textContent ?? '', sec.getAttribute('data-keywords') ?? '', ...own].join(' ');
}

const labelOf = (row: Element) => row.querySelector(':scope > .field-label');
const rowText = (row: Element) => `${labelOf(row)?.textContent ?? ''} ${row.getAttribute('data-keywords') ?? ''}`;

/** Mark `body`'s panels for `query`. Returns how many settings show, or null for no query. */
export function applySearch(body: Element, query: string): number | null {
  for (const el of body.querySelectorAll(`[${HIDE}]`)) el.removeAttribute(HIDE);
  for (const el of body.querySelectorAll(`[${HIT}]`)) el.removeAttribute(HIT);
  const terms = searchTerms(query);
  if (!terms.length) return null;
  let count = 0;
  for (const panel of body.querySelectorAll('.settings-tabpanel')) {
    let hits = 0;
    for (const sec of panel.querySelectorAll('.settings-section')) {
      const rows = [...sec.querySelectorAll('.field-row')].filter(labelOf);
      if (searchMatches(sectionText(sec), terms)) {
        hits += Math.max(rows.length, 1);
        continue;
      }
      const matched = rows.filter((r) => searchMatches(rowText(r), terms));
      if (!matched.length) {
        sec.setAttribute(HIDE, '');
        continue;
      }
      for (const r of rows) if (!matched.includes(r)) r.setAttribute(HIDE, '');
      hits += matched.length;
    }
    if (hits) panel.setAttribute(HIT, '');
    count += hits;
  }
  return count;
}
