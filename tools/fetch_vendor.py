#!/usr/bin/env python3
"""Fetch the third-party files Orbit needs but git does not keep, and check each against its pinned SHA-256.

    python3 tools/fetch_vendor.py        (from app/; or python3 app/tools/fetch_vendor.py from the repo root)

The hashes are those of the files Orbit 2.5.1 shipped (ORBIT-ICPI c48639e), which are the publishers' own files.
A file with any other hash is refused and never saved. Standard library only, like the rest of Orbit's Python.
"""
import hashlib
import os
import sys
import urllib.request

APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FILES = [
    # MapLibre GL JS 5.24.0 (BSD-3-Clause, web/vendor/LICENSE-maplibre.txt): the map in the web app
    ('web/vendor/maplibre-gl.js', 'https://cdn.jsdelivr.net/npm/maplibre-gl@5.24.0/dist/maplibre-gl.js',
     '45a9b07a9189ce56054c620a947ccf41e291e58c95e9b61533b740aaa65ee5cb'),
    ('web/vendor/maplibre-gl.css', 'https://cdn.jsdelivr.net/npm/maplibre-gl@5.24.0/dist/maplibre-gl.css',
     'ab1e70d59ec40465bae7e7030da2f3ccf28133fd502e62bd598eefbadfd7a732'),
    # Windows embeddable Python 3.14.10 (PSF licence; its SBOM is next to it in git): the helper in the Windows build
    ('vendor/python-win/python-3.14.10-embed-amd64.zip',
     'https://www.python.org/ftp/python/3.14.10/python-3.14.10-embed-amd64.zip',
     '90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159'),
]


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def main():
    bad = 0
    for rel, url, pin in FILES:
        dst = os.path.join(APP, *rel.split('/'))
        if os.path.exists(dst):
            with open(dst, 'rb') as f:
                if sha256(f.read()) == pin:
                    print('  OK    ' + rel)
                    continue
        try:
            with urllib.request.urlopen(url, timeout=120) as r:
                data = r.read()
        except OSError as e:
            print('  FAIL  %s: %s' % (rel, e))
            bad += 1
            continue
        got = sha256(data)
        if got != pin:
            print('  FAIL  %s: the server sent a different file (sha256 %s...); not saved' % (rel, got[:12]))
            bad += 1
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(dst + '.part', 'wb') as f:
            f.write(data)
        os.replace(dst + '.part', dst)
        print('  GOT   %s (%.1f MB, hash checked)' % (rel, len(data) / 1e6))
    print('All third-party files in place.' if not bad else '%d file(s) missing: run this again when online.' % bad)
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
