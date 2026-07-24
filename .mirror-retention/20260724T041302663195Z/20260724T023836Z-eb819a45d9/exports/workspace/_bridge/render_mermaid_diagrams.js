const fs = require('fs');
const path = require('path');
const os = require('os');
const Module = require('module');

const ROOT = path.resolve(__dirname, '..');
const NODE_HOME = path.join(os.homedir(), '.cache', 'codex-runtimes', 'codex-primary-runtime', 'dependencies', 'node');
const NODE_MODULES = path.join(NODE_HOME, 'node_modules');
const PLAYWRIGHT_PKG = path.join(NODE_MODULES, '.pnpm', 'playwright@1.61.0', 'node_modules');
const PLAYWRIGHT_CORE_PKG = path.join(NODE_MODULES, '.pnpm', 'playwright-core@1.61.0', 'node_modules');
const EDGE_CANDIDATES = [
  process.env.MERMAID_EDGE_PATH,
  'C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe',
  'C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe',
].filter(Boolean);

process.env.NODE_PATH = [NODE_MODULES, PLAYWRIGHT_PKG, PLAYWRIGHT_CORE_PKG].join(path.delimiter);
Module._initPaths();

const { chromium } = require('playwright');

function usage() {
  console.log('Usage: node _bridge/render_mermaid_diagrams.js <input.md> <output-dir> [--prefix name]');
  process.exit(1);
}

function sanitize(name) {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80) || 'graph';
}

function extractMermaidBlocks(markdown) {
  const re = /```mermaid\s*\n([\s\S]*?)```/g;
  const blocks = [];
  let match;
  while ((match = re.exec(markdown)) !== null) {
    blocks.push(match[1].trim());
  }
  return blocks;
}

async function renderBlock(page, code, outPng) {
  const html = `<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <style>
    html, body { margin: 0; padding: 0; background: white; }
    #wrap { padding: 24px; display: inline-block; background: white; }
    .mermaid { background: white; }
  </style>
</head>
<body>
  <div id="wrap"><div class="mermaid">${code.replace(/&/g, '&amp;').replace(/</g, '&lt;')}</div></div>
</body>
</html>`;

  await page.setContent(html, { waitUntil: 'load' });
  await page.addScriptTag({ url: 'https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js' });
  await page.evaluate(() => {
    window.mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'strict',
      theme: 'default',
      flowchart: { useMaxWidth: false },
    });
  });
  await page.evaluate(async () => {
    await window.mermaid.run({ querySelector: '.mermaid' });
  });
  await page.waitForSelector('.mermaid svg');
  await page.locator('#wrap').screenshot({ path: outPng, omitBackground: false });
}

async function main() {
  const [inputMd, outputDir, ...rest] = process.argv.slice(2);
  if (!inputMd || !outputDir) usage();
  const prefixIndex = rest.indexOf('--prefix');
  const prefix = prefixIndex >= 0 && rest[prefixIndex + 1] ? rest[prefixIndex + 1] : 'graph';

  const markdown = fs.readFileSync(inputMd, 'utf8');
  const blocks = extractMermaidBlocks(markdown);
  if (!blocks.length) {
    console.error('No mermaid blocks found.');
    process.exit(2);
  }

  const edgePath = EDGE_CANDIDATES.find((p) => fs.existsSync(p));
  if (!edgePath) {
    console.error('Microsoft Edge executable not found.');
    process.exit(3);
  }

  fs.mkdirSync(outputDir, { recursive: true });
  const browser = await chromium.launch({ headless: true, executablePath: edgePath });
  const page = await browser.newPage({ viewport: { width: 1800, height: 1200 } });
  const outputs = [];

  for (let i = 0; i < blocks.length; i += 1) {
    const code = blocks[i];
    const firstLine = code.split('\n')[0] || `${prefix}-${i + 1}`;
    const slug = sanitize(firstLine.replace(/^flowchart\s+\w+\s*/, '').replace(/^sequenceDiagram\s*/, '').trim());
    const outPng = path.join(outputDir, `${String(i + 1).padStart(2, '0')}-${slug}.png`);
    await renderBlock(page, code, outPng);
    outputs.push(outPng);
    console.log(outPng);
  }

  await browser.close();
  fs.writeFileSync(path.join(outputDir, 'manifest.json'), JSON.stringify({ input: inputMd, outputs }, null, 2), 'utf8');
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
