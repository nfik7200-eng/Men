// Fails unless the committed bundle is byte-identical to a fresh build of the current sources.
// Content-based (not mtime-based), so a stale bundle cannot pass by having a newer timestamp.
import { execFileSync } from 'node:child_process';
import { mkdtempSync, readFileSync, existsSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const frontend = new URL('../frontend/', import.meta.url).pathname;
const bundle = new URL('../shopping/web/static/app/assets/main.js', import.meta.url).pathname;
if (!existsSync(bundle)) { console.error('bundle missing: run npm run build'); process.exit(1); }

const out = mkdtempSync(join(tmpdir(), 'wt-bundle-'));
// Build into a temporary directory with the same configuration, leaving the served bundle untouched.
execFileSync('npx', ['vite', 'build', '--outDir', out, '--emptyOutDir', '--logLevel', 'silent'], { cwd: frontend, stdio: 'inherit' });
const fresh = readFileSync(join(out, 'assets', 'main.js'));
const committed = readFileSync(bundle);
if (!fresh.equals(committed)) {
  console.error('bundle does not match a fresh build of the sources: run npm run build and commit the result');
  process.exit(1);
}
console.log('bundle matches a fresh build of the sources');
