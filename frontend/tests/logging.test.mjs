import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { createServer } from 'vite';

const source = fs.readFileSync(new URL('../src/logger.ts', import.meta.url), 'utf8').replaceAll('import.meta.hot', 'hot');
const code = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const sent = [], printed = [], intervals = new Map();
const context = {
  exports: {}, AbortController, hot: { send: (...args) => sent.push(args) },
  console: Object.fromEntries(['info', 'warn', 'error'].map(level => [level, (...args) => printed.push([level, ...args])])),
  window: { setTimeout, clearTimeout, setInterval: callback => { intervals.set(1, callback); return 1; }, clearInterval: id => intervals.delete(id) },
  fetch: async () => ({ ok: true, status: 200 }),
};
vm.runInNewContext(code, context);
const { logStep, loggedFetch } = context.exports;
logStep('camera.preview.playing', { width: 1920 });
assert.equal(sent[0][0], 'app:log');
assert.equal(sent[0][1].step, 'camera.preview.playing');
assert.equal(printed[0][0], 'info');
await loggedFetch('/api/sweeps', { method: 'POST' });
assert.equal(sent.at(-1)[1].step, 'http.finished');
assert.equal(intervals.size, 0);
context.fetch = async () => { throw new Error('Backend unavailable'); };
await assert.rejects(loggedFetch('/api/sweeps'));
assert.equal(sent.at(-1)[1].step, 'http.failed');
assert.equal(intervals.size, 0);
context.fetch = async (_, options) => new Promise((_, reject) => {
  options.signal.addEventListener('abort', () => reject(new Error('Aborted')), {once:true});
});
await assert.rejects(loggedFetch('/api/test-timeout', {}, 5), /server did not respond in time/);
assert.equal(intervals.size, 0);
context.hot.send = () => { throw new Error('Socket closed'); };
assert.doesNotThrow(() => logStep('capture.queued'));

// Exercise the actual Vite plugin and HMR socket used by a browser.
const output = [];
let received;
const delivered = new Promise(resolve => { received = resolve; });
const logger = {
  info: line => { output.push(line); if (line.includes('terminal.bridge.test')) received(); },
  warn: line => output.push(line), error: line => output.push(line),
  warnOnce() {}, clearScreen() {}, hasErrorLogged() { return false; }, hasWarned: false,
};
const server = await createServer({
  root: fileURLToPath(new URL('..', import.meta.url)),
  configFile: fileURLToPath(new URL('../vite.config.ts', import.meta.url)),
  customLogger: logger,
  server: { host: '127.0.0.1', port: 5197, strictPort: false },
});
let socket, timeout;
try {
  await server.listen();
  const port = server.httpServer.address().port;
  socket = new WebSocket(`ws://127.0.0.1:${port}/?token=${server.config.webSocketToken}`, 'vite-hmr');
  await new Promise((resolve, reject) => { socket.addEventListener('open', resolve, { once: true }); socket.addEventListener('error', reject, { once: true }); });
  socket.send(JSON.stringify({ type: 'custom', event: 'app:log', data: { time: new Date().toISOString(), level: 'info', step: 'terminal.bridge.test', fields: { status: 'passed' } } }));
  await Promise.race([delivered, new Promise((_, reject) => { timeout = setTimeout(() => reject(new Error('No terminal log received')), 5000); })]);
  assert.ok(output.some(line => line.includes('[frontend] INFO') && line.includes('terminal.bridge.test')));
} finally {
  clearTimeout(timeout);
  socket?.close();
  await server.close();
}
console.log('PASS: browser console logs, HTTP error/timer cleanup, socket-failure isolation, and real Vite terminal forwarding');
