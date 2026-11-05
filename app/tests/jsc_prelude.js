// Minimal browser stand-ins so the web/js readers can run in macOS's jsc shell.
if (typeof setTimeout === 'undefined') globalThis.setTimeout = (f) => { Promise.resolve().then(f); return 0; };
if (typeof console === 'undefined') globalThis.console = { log: (...a) => print(a.join(' ')), error: (...a) => print('ERR ' + a.join(' ')) };
const WEB = '/Users/severisalmi/Desktop/Orbit/web/js/';
function need(...names) { for (const n of names) load(WEB + n + '.js'); }
function bytesOf(path) { return new Uint8Array(readFile(path, 'binary')); }
function textOf(path) { return SF.util.decodeText(bytesOf(path)); }
function done(p) { p.then(() => print('OK'), (e) => print('FAIL ' + e + '\n' + (e && e.stack))); }
