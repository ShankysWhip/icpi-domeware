// Minimal browser stand-ins so the web/js readers run in macOS's jsc shell or under Node. Run from app/:
//   /System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc tests/jsc_prelude.js tests/t_cwa.js
//   node tests/run_tests_node.js            (every t_*.js; GitHub runs this on every pull request)
if (typeof setTimeout === 'undefined') globalThis.setTimeout = (f) => { Promise.resolve().then(f); return 0; };
if (typeof console === 'undefined') globalThis.console = { log: (...a) => print(a.join(' ')), error: (...a) => print('ERR ' + a.join(' ')) };
const WEB = 'web/js/';
const FIX = 'tests/fixtures/';
// Real recordings never go into git: put them (or a link to their folder) in tests/real-data/; under Node,
// ORBIT_REAL_DATA can name another folder. A test whose recording is missing prints SKIP and ends cleanly.
const REAL = (typeof ORBIT_REAL_DATA === 'string' && ORBIT_REAL_DATA) || 'tests/real-data/';
function need(...names) { for (const n of names) load(WEB + n + '.js'); }
function bytesOf(path) { return new Uint8Array(readFile(path, 'binary')); }
function textOf(path) { return SF.util.decodeText(bytesOf(path)); }
function realBytes(name) {
  try { return bytesOf(REAL + name); } catch (e) { print('SKIP no real recording: ' + REAL + name); return null; }
}
function done(p) { p.then(() => print('OK'), (e) => print('FAIL ' + e + '\n' + (e && e.stack))); }
