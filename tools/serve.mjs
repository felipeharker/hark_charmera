#!/usr/bin/env node
/**
 * Serves this site locally, the way Render serves it.
 *
 *   node tools/serve.mjs              http://localhost:8000/
 *   node tools/serve.mjs --port 8002
 *
 * Pages are served straight from their folder, so an edit shows on the next
 * refresh. The shared stylesheet comes from felipeharker.org, as it does on
 * the live site, and the site links lead to the live sites.
 */

import { createServer } from 'node:http';
import { readFileSync } from 'node:fs';
import { dirname, extname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { SITE, resolveUrl } from './site.mjs';

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const DIR = join(ROOT, SITE.dir);
const args = process.argv.slice(2);
const port = Number(args[args.indexOf('--port') + 1]) || 8000;

const TYPES = {
  '.html': 'text/html; charset=utf-8', '.css': 'text/css; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8', '.json': 'application/json',
  '.txt': 'text/plain; charset=utf-8', '.svg': 'image/svg+xml',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
  '.webp': 'image/webp', '.ico': 'image/x-icon', '.woff2': 'font/woff2',
};

createServer((req, res) => {
  const urlPath = new URL(req.url, 'http://localhost').pathname;
  const file = resolveUrl(DIR, urlPath);
  // A folder's index reached without the trailing slash: redirect, so the
  // page's relative links resolve against the folder, as they do on Render.
  if (file && file.endsWith('index.html') && !urlPath.endsWith('/') && !urlPath.endsWith('.html')) {
    res.writeHead(301, { Location: `${urlPath}/` });
    return res.end();
  }
  if (!file) {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    return res.end(`Not found: ${urlPath}\n`);
  }
  res.writeHead(200, { 'Content-Type': TYPES[extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
  res.end(readFileSync(file));
}).listen(port, () => {
  console.log(`${SITE.name} (${SITE.url}) from ${SITE.dir}/ at http://localhost:${port}/`);
});
