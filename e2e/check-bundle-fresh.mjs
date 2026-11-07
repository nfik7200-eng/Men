// Fails when the committed/served bundle is older than the sources it was built from. A stale bundle once
// shipped a placeholder page; this check makes that impossible to pass silently.
import { readdirSync, statSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const root = new URL('../frontend/', import.meta.url).pathname;
const bundle = new URL('../shopping/web/static/app/assets/main.js', import.meta.url).pathname;
if (!existsSync(bundle)) { console.error('bundle missing: run npm run build'); process.exit(1); }
const newestSource = (dir) => readdirSync(dir, { withFileTypes: true }).reduce((max, e) => {
  const p = join(dir, e.name);
  const m = e.isDirectory() ? newestSource(p) : (/\.ts$/.test(e.name) && !/\.test\.ts$/.test(e.name) ? statSync(p).mtimeMs : 0);
  return Math.max(max, m);
}, 0);
const src = newestSource(join(root, 'src'));
if (statSync(bundle).mtimeMs < src) { console.error('bundle is older than its sources: run npm run build'); process.exit(1); }
console.log('bundle is up to date with sources');
