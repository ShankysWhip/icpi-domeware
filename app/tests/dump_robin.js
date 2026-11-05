#!/usr/bin/env node
// Prints what Orbit's Robin reader (web/js/radar.js) makes of one XML file, as JSON, for the parity check against
// domeware's reader (python -m domeware.parity). The reader runs in a bare context like the tests: no require,
// process, files or network inside it.
//   node app/tests/dump_robin.js <file.xml>
'use strict';
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const APP = path.resolve(__dirname, '..');
const file = process.argv[2];
if (!file) { console.error('usage: node dump_robin.js <file.xml>'); process.exit(2); }

const ctx = vm.createContext({ console, setTimeout, clearTimeout, queueMicrotask, TextDecoder, TextEncoder });
for (const n of ['util', 'xml', 'radar']) {
  vm.runInContext(fs.readFileSync(path.join(APP, 'web', 'js', n + '.js'), 'utf8'), ctx, { filename: n + '.js' });
}
ctx.bytes = new Uint8Array(fs.readFileSync(file));
vm.runInContext(`(async () => {
  const st = await SF.radar.parseRobin(SF.util.decodeText(bytes), 'parity');
  return {
    reader: 'Orbit app/web/js/radar.js',
    messages: st.messages, snapshots: st.snapshots, updates: st.updates, alarms: st.alarms, t0: st.t0, t1: st.t1,
    tracks: st.tracks.map((tr) => ({ id: tr.id, main_class: tr.mainClass,
      updates: tr.t.map((t, i) => [t, tr.lat[i], tr.lon[i], tr.alt[i], tr.cls[i], tr.alarm[i]]) })),
    plots: st.plots.t.map((t, i) => [t, st.plots.lat[i], st.plots.lon[i], st.plots.alt[i]]),
  };
})()`, ctx).then((out) => process.stdout.write(JSON.stringify(out)), (e) => {
  console.error('Orbit reader failed: ' + ((e && e.stack) || e));
  process.exit(1);
});
