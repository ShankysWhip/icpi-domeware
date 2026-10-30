"""dw app: one small window for the team. Pick a session, press a big button; the rest of the commands open from
a side panel. Every button runs the same dw command as the command line, so nothing here works differently.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from domeware.overview import DEFAULT_ROOT

# (button text, dw command, what it does) - the four everyday jobs
MAIN = [("Start replay", "start", "Opens the replay HUD and plays the test from its start"),
        ("Classic 3D", "view", "Opens the replay in the Rerun viewer with all its charts"),
        ("Google Earth", "earth", "Writes the Google Earth file and opens it"),
        ("Report", "open-report", "Writes the run report and opens it")]
# side panel: the commands people need now and then, most used first
MORE = [("Check data", "check", "Is everything there? Clocks, gaps, coverage (seconds)"),
        ("Rebuild replay", "replay", "After new files or a change in equipment.csv"),
        ("Convert new files", "convert", "Turns newly added raw files into data"),
        ("Equipment list", "equipment", "Everything at the test and where it was"),
        ("Radar vs drones", "compare", "Delays, errors and coverage per drone"),
        ("Scorecard values", "scorecard", "Values to import on the Run Scorecards page"),
        ("Timeline CSV", "timeline", "Every second in one table, for Excel"),
        ("COURAGEOUS export", "courageous", "Radar output in the CWA 18150 format"),
        ("Seal session", "seal", "Records a hash of every raw file"),
        ("Session info", "info", "What is in the session")]
ACCENT, BG, PANEL, FG, MUTED = "#2f6fed", "#15181d", "#1e232b", "#e8ebf0", "#9aa3af"


def sessions(root: Path = DEFAULT_ROOT) -> list[str]:
    """Real test days first, newest on top (they are named by date); simulated and test sessions after them."""
    found = {p.parent.name: p.stat().st_mtime for p in root.glob("*/manifest.json")}
    dated = sorted((n for n in found if n[:4].isdigit()), key=lambda n: (n[:10], found[n]), reverse=True)
    return dated + sorted(n for n in found if not n[:4].isdigit())


def command(job: str, session: Path) -> list[list[str]]:
    """The dw command line(s) behind a button; the same as typing them."""
    dw = [sys.executable, "-m", "domeware.cli"]
    s = str(session)
    replay_first = [] if (session / "views" / "replay.rrd").exists() else [[*dw, "replay", s]]
    return {"start": [*replay_first, [*dw, "hud", s, "--play"]],
            "view": [*replay_first, [*dw, "view", s]],
            "earth": [[*dw, "kml", s]],
            "open-report": [[*dw, "report", s]]}.get(job, [[*dw, job, s]])


def _open_file(path: Path) -> None:
    if path.exists():
        os.startfile(path) if sys.platform == "win32" else subprocess.Popen(["xdg-open", str(path)])  # noqa: S606


class App:
    def __init__(self, root: tk.Tk, base: Path = DEFAULT_ROOT):
        self.root, self.base, self.busy = root, base, False
        root.title("domeware")
        root.configure(bg=BG)
        root.minsize(560, 440)
        st = ttk.Style(root)
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))
        st.configure("Title.TLabel", font=("Segoe UI Semibold", 16), background=BG, foreground=FG)
        st.configure("Muted.TLabel", foreground=MUTED, background=BG)
        st.configure("Main.TButton", font=("Segoe UI Semibold", 12), padding=(16, 14), background=PANEL, foreground=FG,
                     borderwidth=0)
        st.map("Main.TButton", background=[("active", "#2a313b")])
        st.configure("Start.TButton", font=("Segoe UI Semibold", 13), padding=(16, 16), background=ACCENT,
                     foreground="white", borderwidth=0)
        st.map("Start.TButton", background=[("active", "#3f7ff5")])
        st.configure("Side.TButton", font=("Segoe UI", 10), padding=(10, 8), background=PANEL, foreground=FG,
                     borderwidth=0, anchor="w")
        st.map("Side.TButton", background=[("active", "#2a313b")])
        st.configure("TCombobox", fieldbackground=PANEL, background=PANEL, foreground=FG, arrowcolor=FG, padding=6)
        st.map("TCombobox", fieldbackground=[("readonly", PANEL)], foreground=[("readonly", FG)],
               selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", FG)])
        root.option_add("*TCombobox*Listbox.background", PANEL)
        root.option_add("*TCombobox*Listbox.foreground", FG)
        root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)

        main = ttk.Frame(root, padding=18)
        main.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        top = ttk.Frame(main)
        top.grid(row=0, column=0, sticky="ew")
        ttk.Label(top, text="domeware", style="Title.TLabel").pack(side="left")
        self.more_btn = ttk.Button(top, text="Commands  >", style="Side.TButton", command=self.toggle)
        self.more_btn.pack(side="right")
        ttk.Label(main, text="Session", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(14, 2))
        self.session = tk.StringVar()
        names = sessions(base)
        self.pick = ttk.Combobox(main, textvariable=self.session, values=names, state="readonly", font=("Segoe UI", 11))
        self.pick.grid(row=2, column=0, sticky="ew")
        if names:
            self.session.set(names[0])
        grid = ttk.Frame(main)
        grid.grid(row=3, column=0, sticky="nsew", pady=14)
        for i, (text, job, tip) in enumerate(MAIN):
            b = ttk.Button(grid, text=("▶  " if job == "start" else "") + text,
                           style="Start.TButton" if job == "start" else "Main.TButton", command=lambda j=job: self.run(j))
            b.grid(row=i // 2, column=i % 2, sticky="nsew", padx=4, pady=4)
            self._tip(b, tip)
        grid.columnconfigure((0, 1), weight=1, uniform="b")
        self.status = tk.StringVar(value="Pick a session and press a button.")
        ttk.Label(main, textvariable=self.status, style="Muted.TLabel").grid(row=4, column=0, sticky="w")
        self.log = tk.Text(main, height=9, bg=PANEL, fg=FG, insertbackground=FG, relief="flat", font=("Consolas", 9),
                           wrap="word")
        self.log.grid(row=5, column=0, sticky="nsew", pady=(6, 0))
        main.columnconfigure(0, weight=1)
        main.rowconfigure(5, weight=1)

        self.side = ttk.Frame(root, padding=(0, 18, 18, 18))
        ttk.Label(self.side, text="Commands", style="Title.TLabel").pack(anchor="w", pady=(0, 8))
        for text, job, tip in MORE:
            b = ttk.Button(self.side, text=text, style="Side.TButton", command=lambda j=job: self.run(j))
            b.pack(fill="x", pady=2)
            self._tip(b, tip)
        self.side_open = False

    def _tip(self, widget, text: str) -> None:
        widget.bind("<Enter>", lambda _e: self.status.set(text))
        widget.bind("<Leave>", lambda _e: self.status.set(""))

    def toggle(self) -> None:
        self.side_open = not self.side_open
        if self.side_open:
            self.side.grid(row=0, column=1, sticky="ns")
            self.more_btn.configure(text="Commands  <")
        else:
            self.side.grid_forget()
            self.more_btn.configure(text="Commands  >")

    def write(self, text: str) -> None:
        self.log.insert("end", text)
        self.log.see("end")

    def run(self, job: str) -> None:
        if self.busy or not self.session.get():
            return
        self.busy = True
        session = self.base / self.session.get()
        self.status.set(f"Working: {job} ...")
        self.write(f"\n> {job} {session.name}\n")

        def work():
            ok = True
            cmds = command(job, session)
            if job == "start":  # the HUD keeps serving while its window is open: start it and let it run
                for cmd in cmds[:-1]:
                    ok = ok and subprocess.run(cmd, capture_output=True).returncode == 0
                if ok:
                    subprocess.Popen(cmds[-1], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                msg = "HUD opening in its own window." if ok else "Rebuilding the replay failed."
                self.root.after(0, self.write, msg + "\n")
                self.root.after(0, self.done, ok)
                return
            for cmd in cmds:
                p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
                out = "\n".join(line for line in (p.stdout + p.stderr).splitlines() if " WARN " not in line)
                self.root.after(0, self.write, out[-4000:] + "\n")
                ok = ok and p.returncode == 0
            if ok and job == "earth":
                _open_file(session / "views" / "session.kml")
            if ok and job == "open-report":
                _open_file(session / "views" / "report.html")
            self.root.after(0, self.done, ok)

        threading.Thread(target=work, daemon=True).start()

    def done(self, ok: bool) -> None:
        self.busy = False
        self.status.set("Done." if ok else "Something failed: see the text above.")


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0
