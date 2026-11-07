import { status } from '../components/dom';

// Placeholder until this interface is built. It states that it is not built rather than showing fake data.
export function mountDeals(root: HTMLElement): void {
  root.replaceChildren(status('This interface is not built yet.', 'error'));
}
