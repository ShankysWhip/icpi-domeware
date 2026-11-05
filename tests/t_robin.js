need('util', 'xml', 'radar');
done((async () => {
  const t0 = Date.now();
  const text = textOf('/Users/severisalmi/Desktop/OULUZONE DATAT/iCPI kopio 2.xml');
  const t1 = Date.now();
  const st = await SF.radar.parseRobin(text, 'iCPI');
  const t2 = Date.now();
  print('read ms', t1 - t0, 'parse ms', t2 - t1, 'chars', text.length);
  print('name', st.name, 'messages', st.messages, 'snapshots', st.snapshots, 'tracks', st.tracks.length, 'updates', st.updates, 'plots', st.plots.t.length, 'alarms', st.alarms);
  print('time', new Date(st.t0).toISOString(), new Date(st.t1).toISOString());
  print('classes', JSON.stringify([...st.classes]));
  const s = SF.radar.sensor(st);
  print('sensor', JSON.stringify(s));
  const drones = st.tracks.filter((t) => t.mainClass === 'DRONE' || t.mainClass === 'SUSPECTED_DRONE');
  print('drone-class tracks', drones.map((t) => t.id + ':' + t.mainClass + ':' + t.n).join(' '));
  print('events', st.events.length, st.events.slice(0, 3).map((e) => e.text).join(' | '));
  print('unknown', JSON.stringify(st.unknown));
})());
