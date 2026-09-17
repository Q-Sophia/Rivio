// Package the existing frontend and sanitized snapshot; never read backend artifacts.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const source = path.join(root, 'frontend');
const target = path.join(root, 'rivio-demo');
const files = ['src/app.js', 'src/styles.css', 'src/workspace-projection.js',
  'src/demo-replay.js', 'src/demo-data-provider.js', 'src/demo-experience.js', 'demo/demo_run.json'];
for (const file of files) {
  fs.mkdirSync(path.dirname(path.join(target, file)), { recursive: true });
  fs.copyFileSync(path.join(source, file), path.join(target, file));
}
const html = fs.readFileSync(path.join(source, 'index.html'), 'utf8')
  .replace(/\s*<script src="\.\/src\/live-data-provider\.js"><\/script>/, '')
  .replace('<title>AI Research Team Workspace</title>', '<title>Rivio · Demo Replay</title>');
fs.writeFileSync(path.join(target, 'index.html'), html);
fs.writeFileSync(path.join(target, 'src/data-provider.js'),
  '// Standalone publication is always Demo, even with ?demo=0. No Live provider is shipped.\n' +
  'window.researchDataProvider = new window.DemoDataProvider();\n');
console.log('rivio-demo refreshed from existing frontend and sanitized JSON; no backend dependency.');
