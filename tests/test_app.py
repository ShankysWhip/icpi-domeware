"""dw app: every button runs a real dw command; the window builds and its side panel opens and closes."""

import pytest

from domeware import app, cli


@pytest.mark.parametrize("job", [j for _, j, _ in app.MORE])
def test_every_side_button_is_a_dw_command(job):
    with pytest.raises(SystemExit) as e:
        cli.main([job, "--help"])
    assert e.value.code == 0


def test_main_buttons_build_the_replay_first_when_missing(tmp_path):
    cmds = app.command("start", tmp_path)
    assert cmds[0][-2:] == ["replay", str(tmp_path)] and cmds[-1][-3:] == ["hud", str(tmp_path), "--play"]
    (tmp_path / "views").mkdir()
    (tmp_path / "views" / "replay.rrd").write_bytes(b"")
    assert len(app.command("start", tmp_path)) == 1


def test_window_builds_and_side_panel_toggles(tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        (tmp_path / "S1").mkdir()
        (tmp_path / "S1" / "manifest.json").write_text("{}")
        w = app.App(root, base=tmp_path)
        assert w.session.get() == "S1"
        w.toggle()
        root.update()
        assert w.side.winfo_ismapped()
        w.toggle()
        root.update()
        assert not w.side.winfo_ismapped()
    finally:
        root.destroy()


def test_icpi_logo_draws_the_same_strokes_as_its_svg(tmp_path):
    from domeware import brand, report

    svg = brand.svg()
    assert svg.startswith("<svg") and 'viewBox="0 0 80 38"' in svg and "#000" not in svg  # no black background
    html = report.to_html("# Run report\n\nAll fine.", tmp_path)
    assert "<header><svg" in html and "domeware · test report" in html
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        c = tk.Canvas(root)
        assert round(brand.draw_tk(c, 38)) == 80
        assert len(c.find_all()) == 12  # four letters and eight spark strokes
    finally:
        root.destroy()


def test_new_sessions_show_up_with_f5(tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    try:
        w = app.App(root, base=tmp_path)
        assert w.session.get() == ""
        (tmp_path / "2026-11-01_new").mkdir()
        (tmp_path / "2026-11-01_new" / "manifest.json").write_text("{}")
        w.refresh_sessions()
        assert w.session.get() == "2026-11-01_new" and "1 sessions" in w.status.get()
    finally:
        root.destroy()


def test_hud_script_parses():
    """The HUD's modules have no syntax error (node --check), and every name a module imports is exported by the
    module it names, when node is on this laptop."""
    import re
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node") or r"C:\Program Files\nodejs\node.exe"
    if not Path(node).exists():
        pytest.skip("node not installed")
    web = Path(app.__file__).with_name("web")
    mods = [web / "hud.mjs", *sorted((web / "js").glob("*.mjs"))]
    assert len(mods) > 5
    for js in mods:
        r = subprocess.run([node, "--check", str(js)], capture_output=True, text=True)
        assert r.returncode == 0, (js.name, r.stderr)
        for names, src in re.findall(r'^import \{ (.+?) \} from "\./(.+?)";', js.read_text(encoding="utf-8"), re.M):
            exported = set(re.findall(r"^export (?:async )?(?:function|const|let) ([\w$]+)", (js.parent / src).read_text(encoding="utf-8"), re.M))
            assert set(names.split(", ")) <= exported, (js.name, src)
