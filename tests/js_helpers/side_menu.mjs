// CMX-277: the sidebar's group, row and VIEW menus are the kit's Menu (Radix) since slice B of
// the sidebar port, rendered into a portal on <body>. These drive the OPEN menu the way a user
// does: Enter on its <button> trigger, a real click on an item, Escape to close. Each event
// is dispatched inside React's act(), so the state it sets (open, closed, a new page) is drawn
// when the call returns, as the island's own synchronous repaint is.
import assert from 'node:assert/strict';
// The island's React (the frontend's node_modules), so act() flushes the island's roots.
import { act } from '../../chela/dashboard/frontend/node_modules/react/index.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const dispatch = (el, ev) => act(() => { el.dispatchEvent(ev); });

/** A right-click on `el` (a row, a group header), inside act(). */
export function rightClick(el) {
    assert.ok(el, 'nothing to right-click');
    dispatch(el, new window.MouseEvent('contextmenu', { bubbles: true, cancelable: true }));
}

/** The open menu, or null when none is open. */
export function menu() {
    return document.querySelector('[role="menu"]');
}

const entries = () => [...(menu() || { querySelectorAll: () => [] }).querySelectorAll('[role^="menuitem"]')];
const labelOf = i => ((i.querySelector('.ov-label') || i).textContent || '').trim();

/** The open menu's items: label, its value at the right (a submenu's), checked, disabled. */
export function menuItems() {
    return entries().map(i => ({
        text: labelOf(i),
        value: ((i.querySelector('.ov-val') || {}).textContent || '').trim(),
        submenu: i.getAttribute('aria-haspopup') === 'menu',
        checked: i.getAttribute('aria-checked') === 'true',
        disabled: i.hasAttribute('data-disabled'),
    }));
}

/** Open a menu from its trigger by keyboard (Enter), as a <button> must allow. */
export function openMenu(trigger) {
    assert.ok(trigger, 'no menu trigger');
    assert.equal(trigger.tagName, 'BUTTON', 'a menu trigger must be a <button>');
    dispatch(trigger, new window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true }));
    assert.ok(menu(), 'Enter on the trigger did not open its menu');
}

/** Click the open menu's item labelled `text` (it must be enabled). */
export function clickMenu(text) {
    const el = entries().find(i => labelOf(i) === text);
    assert.ok(el, `no "${text}" in the open menu: ${JSON.stringify(menuItems().map(i => i.text))}`);
    assert.ok(!el.hasAttribute('data-disabled'), `"${text}" is disabled`);
    dispatch(el, new window.MouseEvent('click', { bubbles: true, cancelable: true }));
}

/** Close the open menu (Escape), if any. */
export function closeMenu() {
    const m = menu();
    if (m) dispatch(m, new window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
    assert.equal(menu(), null, 'Escape did not close the menu');
}
