"""Build Orbit, the drone + radar + weather replay app, for macOS and Windows.

    python3 build.py            everything: dist/Orbit-<v>-mac.dmg and dist/Orbit-<v>-windows.zip
    python3 build.py mac        only the Mac app (build/Orbit.app) and its disk image
    python3 build.py windows    only the Windows zip
    python3 build.py check      syntax checks only

Needs Python 3.8+ (standard library only). The Mac part needs Xcode's command line tools
(swiftc, lipo, iconutil, codesign, hdiutil), so it runs on a Mac. The Windows zip bundles the
official Windows embeddable Python from vendor/python-win/.
"""
import datetime as dt, glob, os, plistlib, shutil, struct, subprocess, sys, zipfile

VERSION = '2.5.1'
ROOT = os.path.dirname(os.path.abspath(__file__))
BUILD = os.path.join(ROOT, 'build')
DIST = os.path.join(ROOT, 'dist')
WEB = os.path.join(ROOT, 'web')
PY_EMBED = os.path.join(ROOT, 'vendor', 'python-win', 'python-3.14.10-embed-amd64.zip')
JSC = '/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/Helpers/jsc'


def run(*cmd, **kw):
    subprocess.run(cmd, check=True, **kw)


def check():
    bad = []
    if os.path.exists(JSC):
        for f in sorted(glob.glob(os.path.join(WEB, 'js', '*.js'))):
            out = subprocess.run([JSC, '-e', 'try { new Function(readFile(%r)); } catch (e) { print("SYNTAX " + e); }' % f], capture_output=True, text=True).stdout
            if 'SYNTAX' in out:
                bad.append(os.path.basename(f) + ': ' + out.strip())
    import py_compile
    py_compile.compile(os.path.join(ROOT, 'server', 'orbit_server.py'), doraise=True)
    for f in glob.glob(os.path.join(ROOT, 'windows', '*.ps1')):
        try:
            open(f, encoding='ascii').read()
        except UnicodeDecodeError:
            bad.append(os.path.basename(f) + ': must be plain ASCII for Windows PowerShell 5.1')
    if bad:
        raise SystemExit('checks failed:\n' + '\n'.join(bad))
    print('checks ok')


# ------------------------------------------------------------------ icons
def icons():
    os.makedirs(BUILD, exist_ok=True)
    tool = os.path.join(BUILD, 'make_icon')
    src = os.path.join(ROOT, 'mac', 'make_icon.swift')
    if not os.path.exists(tool) or os.path.getmtime(tool) < os.path.getmtime(src):
        run('swiftc', '-swift-version', '5', '-O', src, '-o', tool)
    icondir = os.path.join(BUILD, 'icon')
    shutil.rmtree(icondir, ignore_errors=True)
    iconset = os.path.join(icondir, 'Orbit.iconset')
    os.makedirs(iconset)
    for base in (16, 32, 128, 256, 512):
        run(tool, str(base), os.path.join(iconset, 'icon_%dx%d.png' % (base, base)))
        run(tool, str(base * 2), os.path.join(iconset, 'icon_%dx%d@2x.png' % (base, base)))
    run('iconutil', '-c', 'icns', iconset, '-o', os.path.join(icondir, 'Orbit.icns'))
    flat = {}
    for px in (16, 24, 32, 48, 64, 128, 256):
        p = os.path.join(icondir, 'flat_%d.png' % px)
        run(tool, str(px), p, '--flat')
        flat[px] = open(p, 'rb').read()
    # Windows .ico with PNG images inside (supported since Windows Vista)
    sizes = sorted(flat)
    head = struct.pack('<HHH', 0, 1, len(sizes))
    entries, blobs, off = b'', b'', 6 + 16 * len(sizes)
    for px in sizes:
        b = flat[px]
        entries += struct.pack('<BBBBHHII', px % 256, px % 256, 0, 0, 1, 32, len(b), off)
        blobs += b
        off += len(b)
    with open(os.path.join(icondir, 'Orbit.ico'), 'wb') as fh:
        fh.write(head + entries + blobs)
    shutil.copy(os.path.join(icondir, 'flat_256.png'), os.path.join(WEB, 'img', 'icon-256.png'))
    return icondir


def copy_web(dest):
    shutil.copytree(WEB, dest, ignore=shutil.ignore_patterns('.DS_Store', '*.map'))


# ------------------------------------------------------------------ macOS
def mac(icondir):
    app = os.path.join(BUILD, 'Orbit.app')
    shutil.rmtree(app, ignore_errors=True)
    os.makedirs(os.path.join(app, 'Contents', 'MacOS'))
    os.makedirs(os.path.join(app, 'Contents', 'Resources'))
    src = os.path.join(ROOT, 'mac', 'main.swift')
    bins = []
    for arch in ('arm64', 'x86_64'):
        out = os.path.join(BUILD, 'Orbit-' + arch)
        run('swiftc', '-swift-version', '5', '-O', '-target', arch + '-apple-macos11.0', '-framework', 'Cocoa', '-framework', 'WebKit', '-framework', 'Network', src, '-o', out)
        bins.append(out)
    run('lipo', '-create', *bins, '-output', os.path.join(app, 'Contents', 'MacOS', 'Orbit'))
    plist = open(os.path.join(ROOT, 'mac', 'Info.plist'), encoding='utf-8').read()
    plist = plist.replace('__VERSION__', VERSION).replace('__BUILD__', dt.datetime.now().strftime('%Y%m%d%H%M'))
    with open(os.path.join(app, 'Contents', 'Info.plist'), 'w', encoding='utf-8') as fh:
        fh.write(plist)
    plistlib.loads(plist.encode())  # must parse
    shutil.copy(os.path.join(icondir, 'Orbit.icns'), os.path.join(app, 'Contents', 'Resources', 'Orbit.icns'))
    copy_web(os.path.join(app, 'Contents', 'Resources', 'web'))
    run('xattr', '-cr', app)
    run('codesign', '--force', '--deep', '--sign', '-', app)
    # disk image: the app plus a shortcut to Applications to drag it onto
    stage = os.path.join(BUILD, 'dmg')
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    run('ditto', app, os.path.join(stage, 'Orbit.app'))
    os.symlink('/Applications', os.path.join(stage, 'Applications'))
    with open(os.path.join(stage, 'Read me.txt'), 'w', encoding='utf-8') as fh:
        fh.write(MAC_README)
    os.makedirs(DIST, exist_ok=True)
    dmg = os.path.join(DIST, 'Orbit-%s-mac.dmg' % VERSION)
    if os.path.exists(dmg):
        os.remove(dmg)
    run('hdiutil', 'create', '-volname', 'Orbit', '-srcfolder', stage, '-ov', '-format', 'UDZO', '-quiet', dmg)
    print('built', dmg, '%.1f MB' % (os.path.getsize(dmg) / 1048576))
    return app, dmg


MAC_README = """Orbit %s for Mac

Drag Orbit onto the Applications folder, then open it from Applications.

The first time, macOS may say the app is from an unidentified developer (it is not signed with
an Apple developer certificate). Then open System Settings > Privacy & Security and click
"Open Anyway" next to Orbit, once.

Your settings, saved map tiles, unlocked DJI keys and live radar recordings are kept in
~/Library/Application Support/Orbit.
""" % VERSION


# ------------------------------------------------------------------ Windows
WIN_README = """{title}
{rule}

Replays a drone flight log against radar tracks and weather on a satellite map (2D and 3D),
with detection analysis, a live radar feed, and maps that keep working offline.

INSTALL
  1. Unzip this folder anywhere (right-click the zip > Extract All).
  2. Double-click "Install Orbit.cmd". No administrator rights are needed.
     It copies Orbit to %LOCALAPPDATA%\\Programs\\Orbit and adds Start menu and
     desktop shortcuts. Orbit is also offered under "Open with" for .txt, .bin, .tlog,
     .xml, .csv, .xlsx, .gpx, .kml and .json files (it never becomes their default app).
  If Windows shows "Windows protected your PC", click "More info" and "Run anyway".

RUN WITHOUT INSTALLING
  Double-click "Start Orbit (no install).cmd" in the unzipped folder.

HOW IT RUNS
  Orbit opens in a Microsoft Edge app window (Edge is part of Windows 10 and 11).
  A small helper runs in the background with the bundled Python ({py}, from python.org,
  signed by the Python Software Foundation). The helper stops by itself about 45 seconds
  after you close the window.

  Settings, saved map tiles, unlocked DJI keys and live radar recordings are kept in
  %LOCALAPPDATA%\\Orbit. A log for troubleshooting is in helper.log there.

LIVE RADAR
  In "Wait for the radar to connect" and "UDP" modes Windows Firewall asks once whether
  pythonw.exe may accept connections. Allow it on private networks.

UNINSTALL
  Settings > Apps > Installed apps > Orbit > Uninstall.

EARLIER ORBIT
  If the earlier Orbit KML 3D viewer is installed on this PC, installing this Orbit
  replaces it, because both use the same name and folder.
"""


def windows(icondir):
    stage_root = os.path.join(BUILD, 'win')
    shutil.rmtree(stage_root, ignore_errors=True)
    top = os.path.join(stage_root, 'Orbit')
    os.makedirs(top)
    if not os.path.exists(PY_EMBED):
        raise SystemExit('missing ' + PY_EMBED + ' (fetch it: python3 tools/fetch_vendor.py)')
    with zipfile.ZipFile(PY_EMBED) as z:
        z.extractall(os.path.join(top, 'python'))
    os.makedirs(os.path.join(top, 'app'))
    shutil.copy(os.path.join(ROOT, 'server', 'orbit_server.py'), os.path.join(top, 'app', 'orbit_server.py'))
    copy_web(os.path.join(top, 'web'))
    shutil.copy(os.path.join(icondir, 'Orbit.ico'), os.path.join(top, 'Orbit.ico'))
    crlf = lambda s: s.replace('\r\n', '\n').replace('\n', '\r\n')
    for name in ('install.ps1', 'uninstall.ps1'):
        with open(os.path.join(ROOT, 'windows', name), encoding='ascii') as fh:
            text = fh.read()
        with open(os.path.join(top, name), 'w', encoding='ascii', newline='') as fh:
            fh.write(crlf(text))
    files = {
        'Install Orbit.cmd': '@echo off\npowershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1"\nif errorlevel 1 (echo. & echo Installation failed. The message above says why.)\npause\n',
        'Start Orbit (no install).cmd': '@echo off\nstart "" "%~dp0python\\pythonw.exe" "%~dp0app\\orbit_server.py" --exit-when-idle 45 %*\n',
        'README.txt': WIN_README.format(title='Orbit %s for Windows' % VERSION, rule='=' * len('Orbit %s for Windows' % VERSION), v=VERSION, py=os.path.basename(PY_EMBED).replace('python-', 'Python ').replace('-embed-amd64.zip', '')),
        'VERSION.txt': VERSION + '\n',
    }
    for name, text in files.items():
        with open(os.path.join(top, name), 'w', encoding='utf-8', newline='') as fh:
            fh.write(crlf(text))
    os.makedirs(DIST, exist_ok=True)
    out = os.path.join(DIST, 'Orbit-%s-windows.zip' % VERSION)
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for root, _, names in os.walk(top):
            for n in sorted(names):
                full = os.path.join(root, n)
                z.write(full, os.path.relpath(full, stage_root))
    print('built', out, '%.1f MB' % (os.path.getsize(out) / 1048576))
    return out


def main():
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    check()
    if what == 'check':
        return
    icondir = icons()
    if what in ('all', 'mac'):
        mac(icondir)
    if what in ('all', 'windows'):
        windows(icondir)


if __name__ == '__main__':
    main()
