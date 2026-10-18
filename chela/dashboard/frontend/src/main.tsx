// The build's entry (CMX-177). Nothing loads it yet: CMX-212 P01 wires it into the page.
import { createRoot } from 'react-dom/client';
import { Hello } from './features/hello/index.ts';

export function mount(el: Element) {
  createRoot(el).render(<Hello />);
}
