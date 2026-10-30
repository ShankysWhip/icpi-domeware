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
    assert cmds[0][-2:] == ["replay", str(tmp_path)] and cmds[-1][-3:] == ["view", str(tmp_path), "--start"]
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
