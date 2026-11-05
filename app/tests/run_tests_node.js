#!/usr/bin/env node
// Runs every tests/t_*.js under Node the way jsc runs them on a Mac: each test in a fresh context that has only
// jsc's print/load/readFile and the browser pieces the readers use (no require, process, files or network).
//   node tests/run_tests_node.js [-v] [t_cwa.js ...]     (from app/, or node app/tests/run_tests_node.js)
// Real recordings: tests/real-data/ or the folder in ORBIT_REAL_DATA; without them those tests print SKIP.
// Exit code 1 when a test prints FAIL, throws or does not finish within 60 s.
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP = path.resolve(__dirname, '..');
const args = process.argv.slice(2);
const verbose = args.includes('-v');
const only = args.filter((a) => a !== '-v');
const real = process.env.ORBIT_REAL_DATA ? path.resolve(process.env.ORBIT_REAL_DATA) + path.sep : null;
const at = (p) => (path.isAbsolute(p) ? p : path.join(APP, p)); // jsc resolves against the folder it runs in

async function runTest(file) {
  const lines = [];
  let finish;
  const finished = new Promise((r) => { finish = r; });
  const ctx = vm.createContext({
    console, setTimeout, clearTimeout, queueMicrotask, TextDecoder, TextEncoder, DecompressionStream, Blob, Response,
    atob, btoa,
    print: (...a) => { const s = a.join(' '); lines.push(s); if (s === 'OK' || s.startsWith('FAIL ')) finish(s); },
  });
  ctx.load = (p) => vm.runInContext(fs.readFileSync(at(p), 'utf8'), ctx, { filename: p });
  ctx.readFile = (p, mode) => {
    const b = fs.readFileSync(at(p));
    return mode === 'binary' ? vm.runInContext('(b) => new Uint8Array(b)', ctx)(b) : b.toString('utf8');
  };
  if (real) ctx.ORBIT_REAL_DATA = real;
  let end = null;
  const t0 = Date.now();
  try {
    ctx.load('tests/jsc_prelude.js');
    ctx.load('tests/' + file);
    end = await Promise.race([finished, new Promise((r) => setTimeout(() => r(null), 60000).unref())]);
  } catch (e) {
    lines.push('THROWN ' + ((e && e.stack) || e));
  }
  const failed = lines.filter((l) => /\bFAIL\b|^THROWN /.test(l));
  const status = failed.length || end === null ? 'FAIL' : lines.some((l) => l.startsWith('SKIP ')) ? 'SKIP' : 'PASS';
  const checks = lines.filter((l) => /\bPASS\b/.test(l)).length;
  console.log(`${status}  ${file}  (${checks} checks, ${Date.now() - t0} ms${end === null && !failed.length ? ', did not finish' : ''})`);
  for (const l of verbose ? lines : lines.filter((x) => /\bFAIL\b|^THROWN |^SKIP /.test(x))) console.log('      ' + l.slice(0, 400));
  return status;
}

(async () => {
  const files = fs.readdirSync(path.join(APP, 'tests')).filter((f) => /^t_.*\.js$/.test(f)).sort()
    .filter((f) => !only.length || only.includes(f));
  const results = [];
  for (const f of files) results.push(await runTest(f));
  const n = (s) => results.filter((r) => r === s).length;
  console.log(`\n${files.length} test files: ${n('PASS')} passed, ${n('SKIP')} skipped (no real recording), ${n('FAIL')} failed`);
  process.exit(n('FAIL') ? 1 : 0);
})();
