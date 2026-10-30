"""dw app: one small window for the team. Pick a session, press one of the big buttons; every other job is in the
"All commands" panel, grouped by what you want to do, each with one line saying what it does. Every button runs the
same dw command as the command line, so nothing here works differently.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import ttk

from domeware.overview import DEFAULT_ROOT

# the four everyday jobs: (title, dw job, what it does, symbol)
MAIN = [("Start replay", "start", "Opens the replay HUD and plays the test from its start", "▶"),
        ("Classic 3D", "view", "The replay in the Rerun viewer, with all its charts", "▣"),
        ("Google Earth", "earth", "Paths, equipment and range rings in Google Earth", "◎"),
        ("Report", "open-report", "The run report as one page, opens on a phone too", "☰")]
# everything else, grouped by what you want to do
GROUPS = [
    ("CHECK THE DATA", [("Check data", "check", "Are all files there, clocks right, gaps? Takes seconds."),
                        ("Session info", "info", "What is in this session."),
                        ("Verify files", "verify", "Raw files still match their hashes.")]),
    ("BUILD", [("Convert new files", "convert", "Turns newly added raw files into data."),
               ("Rebuild replay", "replay", "After new data or a change in equipment.csv."),
               ("Equipment list", "equipment", "Everything at the test and where it was.")]),
    ("RESULTS", [("Radar vs drones", "compare", "Delays, errors and coverage per drone."),
                 ("Scorecard values", "scorecard", "Values to import on the Run Scorecards page."),
                 ("Timeline CSV", "timeline", "Every second in one table, for Excel.")]),
    ("SHARE AND ARCHIVE", [("COURAGEOUS export", "courageous", "Radar output in the CWA 18150 format."),
                           ("Seal session", "seal", "Records a hash of every raw file before sharing.")]),
]
MORE = [row for _, rows in GROUPS for row in rows]
BG, CARD, LINE, TEXT, MUTED, DIM = "#eef1f5", "#ffffff", "#e2e7ee", "#16202c", "#5d6a7a", "#97a2b0"
ACCENT, ACCENT_DARK, HOVER, GREEN, RED = "#0a8db6", "#0b6f91", "#f3f7fa", "#138a52", "#cf3a57"
PRESSED, ACCENT_PRESSED = "#e6edf3", "#095a76"
FONT, BOLD = ("Segoe UI", 10), ("Segoe UI Semibold", 10)


def sessions(root: Path = DEFAULT_ROOT) -> list[str]:
    """Real test days first, newest on top (they are named by date); simulated and test sessions after them."""
    found = {p.parent.name: p.stat().st_mtime for p in root.glob("*/manifest.json")}
    dated = sorted((n for n in found if n[:4].isdigit()), key=lambda n: (n[:10], found[n]), reverse=True)
    return dated + sorted(n for n in found if not n[:4].isdigit())


def summary(session: Path) -> str:
    """One line about a session: place, what data it has, whether the replay is built."""
    try:
        m = json.loads((session / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "not a domeware session"
    bits = [(m.get("site") or {}).get("name") or "no site yet"]
    hud = session / "views" / "hud" / "data.json"
    if hud.exists():
        try:
            st = json.loads(hud.read_text(encoding="utf-8"))["status"]
            bits += [f"radar {st['radar_tracks']} tracks" if st["radar_tracks"] else "radar pending",
                     f"{st['drones']} drone paths" if st["drones"] else
                     ("drone log encrypted" if st["drone_logs_encrypted"] else "no drone logs"),
                     f"{st['screens']} screenshots" if st["screens"] else ""]
        except (OSError, ValueError, KeyError):
            pass
    else:
        bits.append(f"{len(m.get('raw', []))} raw files")
    bits.append("replay ready" if (session / "views" / "replay.rrd").exists() else "no replay yet")
    if m.get("simulated"):
        bits.append("SIMULATED")
    return "  ·  ".join(b for b in bits if b)


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


def _hoverable(widgets, normal: str, hover: str, on_click, pressed: str | None = None) -> None:
    """A frame and its labels act as one button: hover colour, a darker colour while pressed, and the action runs on
    release, only if the pointer is still on the button (as real buttons do: slide off to cancel)."""
    pressed = pressed or hover

    def paint(c):
        for w in widgets:
            w.configure(bg=c)

    def release(e):
        if e.widget.winfo_containing(e.x_root, e.y_root) in widgets:
            paint(hover)
            on_click()
        else:
            paint(normal)

    for w in widgets:
        w.bind("<Enter>", lambda _e: paint(hover))
        w.bind("<Leave>", lambda _e: paint(normal))
        w.bind("<ButtonPress-1>", lambda _e: paint(pressed))
        w.bind("<ButtonRelease-1>", release)
        w.configure(cursor="hand2")


class App:
    def __init__(self, root: tk.Tk, base: Path = DEFAULT_ROOT):
        self.root, self.base, self.busy = root, base, False
        self.names = {}
        root.title("domeware")
        root.configure(bg=BG)
        root.minsize(620, 520)
        st = ttk.Style(root)
        st.theme_use("clam")
        st.configure("TCombobox", fieldbackground=CARD, background=CARD, foreground=TEXT, arrowcolor=MUTED, padding=7,
                     bordercolor=LINE, lightcolor=CARD, darkcolor=CARD)
        st.map("TCombobox", fieldbackground=[("readonly", CARD)], foreground=[("readonly", TEXT)],
               selectbackground=[("readonly", CARD)], selectforeground=[("readonly", TEXT)])
        st.configure("Busy.Horizontal.TProgressbar", troughcolor=LINE, background=ACCENT, bordercolor=LINE,
                     lightcolor=ACCENT, darkcolor=ACCENT, thickness=4)
        root.option_add("*TCombobox*Listbox.background", CARD)
        root.option_add("*TCombobox*Listbox.foreground", TEXT)
        root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
        root.option_add("*TCombobox*Listbox.font", FONT)

        main = tk.Frame(root, bg=BG, padx=20, pady=18)
        main.grid(row=0, column=0, sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        # header
        head = tk.Frame(main, bg=BG)
        head.grid(row=0, column=0, sticky="ew")
        tk.Label(head, text="domeware", bg=BG, fg=TEXT, font=("Segoe UI Semibold", 18)).pack(side="left")
        tk.Label(head, text="  C-UAS test replay", bg=BG, fg=MUTED, font=FONT).pack(side="left", pady=(7, 0))
        self.more_btn = tk.Label(head, text="All commands  ›", bg=CARD, fg=ACCENT_DARK, font=BOLD, padx=12, pady=6,
                                 highlightthickness=1, highlightbackground=LINE)
        self.more_btn.pack(side="right")
        _hoverable([self.more_btn], CARD, HOVER, self.toggle, PRESSED)
        # session
        box = tk.Frame(main, bg=CARD, highlightthickness=1, highlightbackground=LINE, padx=14, pady=12)
        box.grid(row=1, column=0, sticky="ew", pady=(16, 12))
        tk.Label(box, text="SESSION", bg=CARD, fg=MUTED, font=("Segoe UI Semibold", 8)).pack(anchor="w")
        self.session = tk.StringVar()
        names = sessions(base)
        self.pick = ttk.Combobox(box, textvariable=self.session, values=names, state="readonly", font=("Segoe UI", 11))
        self.pick.pack(fill="x", pady=(4, 6))
        self.info = tk.Label(box, text="", bg=CARD, fg=MUTED, font=("Segoe UI", 9), anchor="w", justify="left")
        self.info.pack(fill="x")
        self.pick.bind("<<ComboboxSelected>>", lambda _e: self.show_summary())
        if names:
            self.session.set(names[0])
        self.show_summary()
        # the four big buttons
        grid = tk.Frame(main, bg=BG)
        grid.grid(row=2, column=0, sticky="nsew")
        grid.columnconfigure((0, 1), weight=1, uniform="b")
        for i, (title, job, desc, sym) in enumerate(MAIN):
            self._big(grid, i, title, job, desc, sym, primary=job == "start")
        # status
        status = tk.Frame(main, bg=BG)
        status.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        self.status = tk.StringVar(value="Pick a session and press a button.")
        self.status_lbl = tk.Label(status, textvariable=self.status, bg=BG, fg=MUTED, font=FONT, anchor="w")
        self.status_lbl.pack(side="left", fill="x", expand=True)
        self.details_btn = tk.Label(status, text="Details ▾", bg=BG, fg=ACCENT_DARK, font=("Segoe UI", 9), cursor="hand2")
        self.details_btn.pack(side="right")
        self.details_btn.bind("<Button-1>", lambda _e: self.toggle_details())
        self.bar = ttk.Progressbar(main, mode="indeterminate", style="Busy.Horizontal.TProgressbar")  # shown only while working
        tk.Label(main, text="Enter  start replay      F5  refresh the list      Esc  close the panel", bg=BG, fg=DIM,
                 font=("Segoe UI", 8)).grid(row=6, column=0, sticky="e", pady=(10, 0))
        root.bind("<Return>", lambda _e: self.run("start"))
        root.bind("<F5>", lambda _e: self.refresh_sessions())
        root.bind("<Escape>", lambda _e: self.toggle() if self.side_open else None)
        self.log = tk.Text(main, height=8, bg=CARD, fg=TEXT, relief="flat", font=("Cascadia Mono", 9), wrap="word",
                           highlightthickness=1, highlightbackground=LINE, padx=8, pady=6)
        self.log_shown = False
        main.columnconfigure(0, weight=1)
        main.rowconfigure(5, weight=1)
        self.main = main
        # all commands, grouped
        self.side = tk.Frame(root, bg=CARD, highlightthickness=1, highlightbackground=LINE, padx=16, pady=16)
        tk.Label(self.side, text="All commands", bg=CARD, fg=TEXT, font=("Segoe UI Semibold", 14)).pack(anchor="w")
        tk.Label(self.side, text="Grouped by what you want to do. Click one to run it on this session.", bg=CARD, fg=MUTED,
                 font=("Segoe UI", 9), wraplength=300, justify="left").pack(anchor="w", pady=(2, 8))
        for group, rows in GROUPS:
            tk.Label(self.side, text=group, bg=CARD, fg=ACCENT_DARK, font=("Segoe UI Semibold", 8)).pack(anchor="w", pady=(10, 3))
            for title, job, desc in rows:
                self._row(self.side, title, job, desc)
        self.side_open = False

    def _big(self, parent, i: int, title: str, job: str, desc: str, sym: str, primary: bool) -> None:
        bg, hover = (ACCENT, ACCENT_DARK) if primary else (CARD, HOVER)
        fg, sub = ("white", "#d6f1fa") if primary else (TEXT, MUTED)
        f = tk.Frame(parent, bg=bg, highlightthickness=1, highlightbackground=ACCENT if primary else LINE, padx=14, pady=12)
        f.grid(row=i // 2, column=i % 2, sticky="nsew", padx=4, pady=4)
        icon = tk.Label(f, text=sym, bg=bg, fg="white" if primary else ACCENT, font=("Segoe UI Symbol", 20), width=2)
        icon.grid(row=0, column=0, rowspan=2, sticky="n", padx=(0, 10))
        t = tk.Label(f, text=title, bg=bg, fg=fg, font=("Segoe UI Semibold", 12), anchor="w")
        t.grid(row=0, column=1, sticky="w")
        d = tk.Label(f, text=desc, bg=bg, fg=sub, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=220)
        d.grid(row=1, column=1, sticky="w")
        _hoverable([f, icon, t, d], bg, hover, lambda: self.run(job), ACCENT_PRESSED if primary else PRESSED)
        self.names[job] = title

    def _row(self, parent, title: str, job: str, desc: str) -> None:
        f = tk.Frame(parent, bg=CARD, padx=10, pady=6)
        f.pack(fill="x")
        t = tk.Label(f, text=title, bg=CARD, fg=TEXT, font=BOLD, anchor="w")
        t.grid(row=0, column=0, sticky="w")
        d = tk.Label(f, text=desc, bg=CARD, fg=MUTED, font=("Segoe UI", 9), anchor="w", justify="left", wraplength=260)
        d.grid(row=1, column=0, sticky="w")
        arrow = tk.Label(f, text="›", bg=CARD, fg=DIM, font=("Segoe UI", 14))
        arrow.grid(row=0, column=1, rowspan=2, sticky="e")
        f.columnconfigure(0, weight=1)
        _hoverable([f, t, d, arrow], CARD, HOVER, lambda: self.run(job), PRESSED)
        self.names[job] = title

    def refresh_sessions(self) -> None:
        """Look for new sessions (e.g. one made in a terminal) without restarting the window."""
        names = sessions(self.base)
        self.pick.configure(values=names)
        if self.session.get() not in names:
            self.session.set(names[0] if names else "")
        self.show_summary()
        self.status.set(f"{len(names)} sessions found.")
        self.status_lbl.configure(fg=MUTED)

    def show_summary(self) -> None:
        name = self.session.get()
        self.info.configure(text=summary(self.base / name) if name else "No sessions yet: run  dw run <session> <files>")

    def toggle(self) -> None:
        self.side_open = not self.side_open
        if self.side_open:
            self.side.grid(row=0, column=1, sticky="ns", padx=(0, 16), pady=18)
            self.more_btn.configure(text="All commands  ‹")
        else:
            self.side.grid_forget()
            self.more_btn.configure(text="All commands  ›")

    def toggle_details(self, show: bool | None = None) -> None:
        self.log_shown = (not self.log_shown) if show is None else show
        if self.log_shown:
            self.log.grid(row=5, column=0, sticky="nsew", pady=(8, 0))
        else:
            self.log.grid_forget()
        self.details_btn.configure(text="Details ▴" if self.log_shown else "Details ▾")

    def write(self, text: str) -> None:
        self.log.insert("end", text)
        self.log.see("end")

    def run(self, job: str) -> None:
        if self.busy or not self.session.get():
            return
        self.busy = True
        session = self.base / self.session.get()
        title = self.names.get(job, job)
        self.status.set(f"Working: {title} …")
        self.status_lbl.configure(fg=MUTED)
        self.bar.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        self.bar.start(12)
        self.root.configure(cursor="watch")
        self.write(f"\n> {title}  ({session.name})\n")

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
                self.root.after(0, self.done, ok, title)
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
            self.root.after(0, self.done, ok, title)

        threading.Thread(target=work, daemon=True).start()

    def done(self, ok: bool, title: str = "") -> None:
        self.busy = False
        self.bar.stop()
        self.bar.grid_remove()
        self.root.configure(cursor="")
        self.status.set(f"✓  Done: {title}" if ok else f"✗  {title} failed: the details are open below.")
        self.status_lbl.configure(fg=GREEN if ok else RED)
        if not ok:
            self.toggle_details(True)
        self.show_summary()


def main() -> int:
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0
