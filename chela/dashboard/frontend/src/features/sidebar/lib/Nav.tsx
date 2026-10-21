// CMX-278: the nav rows (Wall, Work), from views.js's registry, and the Work badges work.js
// counts. The title is the label: it is all that is left on the collapsed rail.
import { icons } from '../../../shared/icons/index.ts';
import type { NavState } from '../model.ts';
import type { SidebarActions } from './actions.ts';

const NAV_ICON: Record<string, (typeof icons)[keyof typeof icons]> = { terminals: icons.wall, work: icons.work };

export function Nav({ nav, actions }: { nav: NavState; actions: SidebarActions }) {
  return (
    <>
      {nav.items.map(v => {
        const Icon = NAV_ICON[v.id];
        const badges = v.badges ?? [];
        return (
          <div key={v.id} className={`side-item${v.id === nav.active ? ' active' : ''}`} data-view={v.id}
            title={v.label || v.id} onClick={() => actions.selectView(v.id)}>
            <span className="side-item-icon">{Icon && <Icon size={18} aria-hidden="true" />}</span>
            <span className="side-item-label">{v.label || v.id}</span>
            {badges.length > 0 && (
              <span className="side-badges">
                {badges.map(b => (
                  <span key={b.id} className={`badge ${b.cls ?? ''}`} id={b.id} title={b.title ?? ''}>
                    {nav.badges[b.id] ?? b.text ?? ''}
                  </span>
                ))}
              </span>
            )}
          </div>
        );
      })}
    </>
  );
}
