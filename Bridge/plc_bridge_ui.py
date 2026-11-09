"""
PLC Bridge — GUI Control Panel
================================
Run:  python plc_bridge_ui.py

Runs the bridge straight from the window:
  • IP / slot / type / interval setup
  • Saved device list (name + IP) for quick switching
  • SEVERAL PLCs at once — each session gets its own WebSocket port
  • Start / Stop / Reconnect per session
  • Connection status and polled tag count
  • Log with level and session filtering

Every started PLC listens on its own port (8765, 8767, 8768 …). Point one
browser tab at each URL and the tabs watch different controllers at the same
time; double-click a row in the sessions table to copy its URL.
"""

import sys, threading, asyncio, time, logging, json, os
from queue import Queue, Empty
from pathlib import Path

# ── Import bridge components ──────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
try:
    from plc_bridge_500 import (
        Config, MemoryLogHandler, LogBuffer, set_debug_save,
        SessionManager, DeviceStore, device_to_config, poll_interval_from,
        build_http_app, AIOHTTP_OK, DEFAULT_HTTP,
        LIBPLCTAG_OK, WS_OK,
        _CYCLE_WARN_MS as CYCLE_WARN_MS,
    )
except ImportError as e:
    import tkinter as tk
    from tkinter import messagebox
    tk.Tk().withdraw()
    messagebox.showerror("Error", f"plc_bridge_500.py not found next to this file:\n{e}")
    sys.exit(1)

import tkinter as tk
from tkinter import ttk, messagebox

# ── Palette ───────────────────────────────────────────────────────────────────
BG    = "#0a0f14"
CARD  = "#0d1820"
HDR   = "#06090e"
BORD  = "#182840"
ACC   = "#3ddc84"
ERR   = "#c03030"
WARN  = "#b07010"
TEXT  = "#8ab0c8"
TEXT2 = "#3a6080"
MONO  = ("Courier New", 11)
MONO9 = ("Courier New", 9)

LOG_FG = {
    "DEBUG":    "#3a6080",
    "INFO":     "#3a80c8",
    "WARNING":  "#b07010",
    "WARN":     "#b07010",
    "ERROR":    "#c03030",
    "CRITICAL": "#c03030",
}
LEVEL_ORD = {"DEBUG":0,"INFO":1,"WARNING":2,"WARN":2,"ERROR":3,"CRITICAL":4}
FILTER_THR = {"ALL":-1,"INFO":1,"WARN":2,"ERROR":3}

ALL_SESSIONS = "ALL"

# Longest the closing window waits for its sessions to stop. A stop takes
# milliseconds; this only bounds a WebSocket client that never answers the
# close. Whatever is still open past it is shut down at exit by the bridge.
CLOSE_WAIT_S = 5.0


# ── Bridge controller ─────────────────────────────────────────────────────────
class BridgeController:
    """Runs every PLC session in one asyncio loop on a daemon thread.

    The GUI thread never touches a session object: it submits coroutines and
    reads the snapshots that come back on the queue. `focus` names the session
    whose values are currently on screen — pushing values for every session on
    every tick would multiply the traffic by the number of controllers for
    rows nobody is looking at.
    """

    def __init__(self, gui_q: Queue):
        self.q           = gui_q
        self.log_buf     = LogBuffer()
        self.mgr         = SessionManager()
        self.records_dir = Config().records_dir
        self.focus       = ""      # session name whose values the GUI shows
        self._last_seq   = 0

        # all log output → memory buffer
        h = MemoryLogHandler(self.log_buf)
        h.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
        root_logger = logging.getLogger()
        root_logger.addHandler(h)
        root_logger.setLevel(logging.INFO)

        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self._submit(self._ui_loop())
        # The panel used to be WebSocket-only, so /sessions existed for the CLI
        # bridge and not for the GUI one. The tracer's connect dialog lists the
        # running controllers over REST (it is the only way to see a session on
        # a port outside the default range), so the GUI serves it too.
        self._http_runner = None
        self._submit(self._start_http())

    # ── Public (GUI thread) ───────────────────────────────────────────────────
    def _submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def start_session(self, name, cfg):
        self._submit(self._start(name, cfg))

    def stop_session(self, name):
        self._submit(self._stop(name))

    def stop_all(self):
        return self._submit(self._stop_all())

    def apply_and_reconnect(self, name, fields):
        self._submit(self._apply(name, fields))

    # ── Async internals ───────────────────────────────────────────────────────
    async def _start_http(self):
        """REST API on 8766 — best effort, the panel works without it."""
        if not AIOHTTP_OK:
            return
        try:
            from aiohttp import web
            runner = web.AppRunner(build_http_app(self.mgr, self.log_buf))
            await runner.setup()
            await web.TCPSite(runner, "0.0.0.0", DEFAULT_HTTP).start()
            self._http_runner = runner
            logging.getLogger("bridge500").info(
                f"HTTP REST  → http://localhost:{DEFAULT_HTTP}")
        except Exception as e:
            # Another bridge already owns the port, or the port is blocked —
            # not fatal, WebSocket sessions are unaffected.
            logging.getLogger("bridge500").warning(f"HTTP REST not started: {e}")

    async def _start(self, name, cfg):
        self.q.put(("busy", name))
        try:
            sess = await self.mgr.start(name, cfg)
        except Exception as e:
            logging.getLogger("bridge500").error(f"[{name}] not started: {e}")
            self.q.put(("failed", name, str(e)))
            return
        self.q.put(("started", sess.name, sess.cfg.port_ws))

    async def _stop(self, name):
        try:
            await self.mgr.stop(name)
        except Exception as e:
            logging.getLogger("bridge500").warning(f"[{name}] stop failed: {e}")
        self.q.put(("stopped", name))

    async def _stop_all(self):
        # The REST server outlives this: "Stop all" is a button, and starting a
        # session again afterwards must still be visible to the tracer. It goes
        # away with the process.
        await self.mgr.stop_all()
        self.q.put(("stopped", ""))

    async def _apply(self, name, f):
        """Push edited fields into a live session and reconnect if needed."""
        s = self.mgr.get(name)
        if not s:
            return
        cfg = s.cfg
        need_rc = (f["ip"]       != cfg.ip              or
                   f["slot"]     != cfg.slot            or
                   f["type"]     != cfg.controller_type or
                   f["rslinx"]   != cfg.via_rslinx)
        cfg.ip              = f["ip"]
        cfg.slot            = f["slot"]
        cfg.controller_type = f["type"]
        cfg.processor_type  = "Logix5000" if f["type"] == "logix" else "SLC"
        cfg.via_rslinx      = f["rslinx"]
        if f.get("records_dir"):
            cfg.records_dir = f["records_dir"]
        if f["interval"] != cfg.poll_interval:
            cfg.poll_interval = f["interval"]
            await s.conn.update_poll_interval(f["interval"])
        if f["groups"] != int(cfg.conn_groups or 0):
            # Handles carry their connection id from creation, so switching the
            # count means rebuilding them — the connection itself stays up.
            await s.conn.update_conn_groups(f["groups"])
        if "io_block" in f and bool(f["io_block"]) != bool(getattr(cfg, "io_block", False)):
            # Takes effect on the next cycle; the connection stays up.
            await s.conn.update_io_block(bool(f["io_block"]))
        if f.get("port") and f["port"] != cfg.port_ws:
            # The port is the address a browser tab is already pointed at, so it
            # is never moved under a live session.
            s.log.warning(f"WS port {f['port']} takes effect after Stop → Start "
                          f"(still serving {cfg.port_ws})")
        if need_rc:
            await s.reconnect()

    async def _ui_loop(self):
        """Push log + session snapshots + values to the GUI queue every ~0.8 s."""
        while True:
            try:
                new = self.log_buf.since(self._last_seq)
                if new:
                    self._last_seq = new[-1]["seq"]
                    for r in new:
                        self.q.put(("log", r))
                # Always push a snapshot (even empty) so the GUI can drop rows
                # for sessions that were stopped.
                self.q.put(("sessions", self.mgr.status_all()))
                s = self.mgr.get(self.focus) if self.focus else self.mgr.default()
                self.q.put(("values",
                            s.name if s else "",
                            dict(s.poller.values) if (s and s.poller) else {}))
            except Exception:
                pass
            await asyncio.sleep(0.8)


# ── App window ────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PLC Bridge")
        self.configure(bg=BG)
        self.geometry("900x780")
        self.minsize(760, 620)

        self.bridge      = BridgeController(Queue())
        self._autoscroll = tk.BooleanVar(value=True)
        self._log_filter = tk.StringVar(value="ALL")
        self._src_filter = tk.StringVar(value=ALL_SESSIONS)
        self._dbg_save   = tk.BooleanVar(value=False)
        self._all_log    = []   # full unfiltered list of log records
        self._devs       = DeviceStore()
        self._sessions   = {}   # name → last status dict
        self._sel        = ""   # selected session name
        self._slug2name  = {}   # log tag → session name (never pruned, old
                                # lines must stay readable after a stop)
        self._hint_job   = None # pending after() that clears the hint label

        self._build()
        self._poll()

    # ── Build UI ──────────────────────────────────────────────────────────────
    def _build(self):
        self._style()

        # ─ Header ─
        hdr = tk.Frame(self, bg=HDR, padx=16, pady=10)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="PLC Bridge", bg=HDR, fg="#b8d8f0",
                 font=("Courier New", 13, "bold")).pack(side=tk.LEFT)
        self._lbl_st = tk.Label(hdr, text="● NO SESSIONS", bg=HDR,
                                fg=TEXT2, font=MONO)
        self._lbl_st.pack(side=tk.LEFT, padx=14)
        self._lbl_ctrl = tk.Label(hdr, text="", bg=HDR, fg=TEXT2, font=MONO9)
        self._lbl_ctrl.pack(side=tk.LEFT)
        self._lbl_ws = tk.Label(hdr, text="", bg=HDR, fg=TEXT2, font=MONO9)
        self._lbl_ws.pack(side=tk.RIGHT)

        # ─ Config panel ─
        cf = tk.Frame(self, bg=CARD,
                      highlightbackground=BORD, highlightthickness=1)
        cf.pack(fill=tk.X, padx=12, pady=(10, 0))

        p = tk.Frame(cf, bg=CARD, padx=14, pady=12)
        p.pack(fill=tk.X)

        # Row 0 — saved devices
        r0 = tk.Frame(p, bg=CARD)
        r0.pack(fill=tk.X, pady=(0, 8))

        _lbl(r0, "Device").pack(side=tk.LEFT)
        self._v_dev  = tk.StringVar(value="")
        self._cb_dev = ttk.Combobox(r0, textvariable=self._v_dev, state="readonly",
                                    values=[], width=40, font=MONO9)
        self._cb_dev.pack(side=tk.LEFT, padx=(4, 8))
        self._cb_dev.bind("<<ComboboxSelected>>", self._on_dev_select)

        _btn_sm(r0, "💾 Save",   self._on_dev_save).pack(side=tk.LEFT, padx=(0, 4))
        _btn_sm(r0, "✕ Delete", self._on_dev_delete).pack(side=tk.LEFT)

        self._lbl_hint = tk.Label(r0, text="", bg=CARD, fg=TEXT2, font=MONO9)
        self._lbl_hint.pack(side=tk.LEFT, padx=10)

        # Row 1 — type · IP · slot
        r1 = tk.Frame(p, bg=CARD)
        r1.pack(fill=tk.X, pady=(0, 8))

        _lbl(r1, "Type").pack(side=tk.LEFT)
        self._v_type = tk.StringVar(value="slc")
        ttk.Combobox(r1, textvariable=self._v_type, state="readonly",
                     values=["slc", "logix"], width=7,
                     font=MONO9).pack(side=tk.LEFT, padx=(4, 18))

        _lbl(r1, "IP").pack(side=tk.LEFT)
        self._v_ip = tk.StringVar(value="192.168.1.1")
        _entry(r1, self._v_ip, width=17).pack(side=tk.LEFT, padx=(4, 18))

        _lbl(r1, "Slot").pack(side=tk.LEFT)
        self._v_slot = tk.StringVar(value="0")
        _entry(r1, self._v_slot, width=4).pack(side=tk.LEFT, padx=(4, 0))

        # Row 2 — interval · connections · ws port · rslinx
        r2 = tk.Frame(p, bg=CARD)
        r2.pack(fill=tk.X, pady=(0, 8))

        _lbl(r2, "Interval, s").pack(side=tk.LEFT)
        self._v_iv = tk.StringVar(value="0.2")
        _entry(r2, self._v_iv, width=6).pack(side=tk.LEFT, padx=(4, 18))

        # Parallel CIP connections. One connection = one request in flight, so a
        # long watch list is read strictly round-trip after round-trip; spreading
        # it over several connections overlaps them and shortens the cycle.
        _lbl(r2, "Connections").pack(side=tk.LEFT)
        self._v_groups = tk.StringVar(value="0")
        _entry(r2, self._v_groups, width=4).pack(side=tk.LEFT, padx=(4, 4))
        tk.Label(r2, text="0 = auto", bg=CARD, fg=TEXT2,
                 font=MONO9).pack(side=tk.LEFT, padx=(0, 18))

        # The port a browser tab connects to. Fixing it per device is what makes
        # "one tab per PLC" reproducible after a restart.
        _lbl(r2, "WS port").pack(side=tk.LEFT)
        self._v_port = tk.StringVar(value="0")
        _entry(r2, self._v_port, width=6).pack(side=tk.LEFT, padx=(4, 4))
        tk.Label(r2, text="0 = auto", bg=CARD, fg=TEXT2,
                 font=MONO9).pack(side=tk.LEFT, padx=(0, 18))

        self._v_rslinx = tk.BooleanVar(value=False)
        tk.Checkbutton(r2, text="RSLinx Gateway", variable=self._v_rslinx,
                       bg=CARD, fg=TEXT, selectcolor="#06090e",
                       activebackground=CARD, activeforeground=TEXT,
                       font=MONO9).pack(side=tk.LEFT)

        # Row 2b — records directory
        r2b = tk.Frame(p, bg=CARD)
        r2b.pack(fill=tk.X, pady=(0, 12))
        _lbl(r2b, "Records folder").pack(side=tk.LEFT)
        self._v_recdir = tk.StringVar(value=self.bridge.records_dir)
        _entry(r2b, self._v_recdir, width=44).pack(side=tk.LEFT, padx=(4, 18))

        # SLC: the whole I/O image in one request per I and O file instead of
        # one per I/O word. The slot layout comes from the tracer's .SLC project.
        self._v_ioblk = tk.BooleanVar(value=False)
        self._cb_ioblk = tk.Checkbutton(r2b, text="Read I/O as block (SLC)",
                                        variable=self._v_ioblk,
                                        bg=CARD, fg=TEXT, selectcolor="#06090e",
                                        activebackground=CARD, activeforeground=TEXT,
                                        disabledforeground=TEXT2, font=MONO9)
        self._cb_ioblk.pack(side=tk.LEFT)
        self._ioblk_keep = False      # the box as it was before Logix was picked
        self._v_type.trace_add("write", self._sync_ioblk)

        # Row 3 — buttons
        r3 = tk.Frame(p, bg=CARD)
        r3.pack(fill=tk.X)

        self._btn_start = _btn(r3, "▶  Start", self._on_start,
                               bg=ACC, fg="#040e08",
                               abg="#2ab060", afg="#040e08")
        self._btn_start.pack(side=tk.LEFT, padx=(0, 8))

        self._btn_stop = _btn(r3, "■  Stop", self._on_stop, state=tk.DISABLED)
        self._btn_stop.pack(side=tk.LEFT, padx=(0, 8))

        self._btn_rc = _btn(r3, "↺  Reconnect", self._on_reconnect,
                            state=tk.DISABLED)
        self._btn_rc.pack(side=tk.LEFT, padx=(0, 8))

        self._btn_copy = _btn(r3, "⧉  Copy URL", self._on_copy_url,
                              state=tk.DISABLED)
        self._btn_copy.pack(side=tk.LEFT, padx=(0, 8))

        self._btn_stopall = _btn(r3, "■■ Stop all", self._on_stop_all,
                                 state=tk.DISABLED)
        self._btn_stopall.pack(side=tk.RIGHT)

        # ─ Sessions table ─
        sess_box = tk.Frame(self, bg=BG, padx=12)
        sess_box.pack(fill=tk.X, pady=(10, 0))
        head = tk.Frame(sess_box, bg=BG)
        head.pack(fill=tk.X)
        _lbl(head, "PLC sessions").pack(side=tk.LEFT)
        tk.Label(head, text="— select a row to edit / stop it, double-click to copy its URL",
                 bg=BG, fg=TEXT2, font=MONO9).pack(side=tk.LEFT, padx=8)

        scols = ("name", "ip", "type", "port", "status", "tags", "cycle")
        self._sv = ttk.Treeview(sess_box, columns=scols, show="headings",
                                height=5, selectmode="browse")
        for c, t, w, st in (("name", "Session", 130, False),
                            ("ip",   "IP",      120, False),
                            ("type", "Type",     60, False),
                            ("port", "WS port",  70, False),
                            ("status", "Status", 150, True),
                            ("tags", "Tags",     60, False),
                            ("cycle", "Cycle",  170, False)):
            self._sv.heading(c, text=t, anchor=tk.W)
            self._sv.column(c, width=w, minwidth=50, stretch=st)
        self._sv.tag_configure("ok",   foreground=ACC)
        self._sv.tag_configure("bad",  foreground=ERR)
        self._sv.tag_configure("warn", foreground=WARN)
        self._sv.pack(fill=tk.X, pady=(4, 0))
        self._sv.bind("<<TreeviewSelect>>", self._on_sess_select)
        self._sv.bind("<Double-1>", lambda _e: self._on_copy_url())

        # ─ Watching strip ─
        strip = tk.Frame(self, bg=BG, padx=14, pady=5)
        strip.pack(fill=tk.X)
        _lbl(strip, "Tags polled:").pack(side=tk.LEFT)
        self._lbl_tags = tk.Label(strip, text="—", bg=BG, fg=TEXT, font=MONO9)
        self._lbl_tags.pack(side=tk.LEFT, padx=6)
        # Cycle time — green while the read fits the interval, amber when it
        # overruns it, red once tags start timing out.
        self._lbl_cycle = tk.Label(strip, text="", bg=BG, fg=TEXT2, font=MONO9)
        self._lbl_cycle.pack(side=tk.LEFT, padx=(12, 0))

        # ─ Separator ─
        tk.Frame(self, bg=BORD, height=1).pack(fill=tk.X)

        # ─ Notebook: Log | Tags/Values ─
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True)

        # ── Tab 1: Log ──────────────────────────────────────────────────────
        tab_log = tk.Frame(nb, bg=HDR)
        nb.add(tab_log, text="  Log  ")

        # log toolbar
        lt = tk.Frame(tab_log, bg=HDR, padx=12, pady=6)
        lt.pack(fill=tk.X)

        ttk.Combobox(lt, textvariable=self._log_filter,
                     values=["ALL", "INFO", "WARN", "ERROR"],
                     state="readonly", width=7,
                     font=MONO9).pack(side=tk.LEFT)
        self._log_filter.trace_add("write", lambda *_: self._redraw_log())

        # Which PLC the lines came from. With several controllers running this
        # is the difference between a readable log and a soup.
        self._cb_src = ttk.Combobox(lt, textvariable=self._src_filter,
                                    values=[ALL_SESSIONS], state="readonly",
                                    width=16, font=MONO9)
        self._cb_src.pack(side=tk.LEFT, padx=8)
        self._src_filter.trace_add("write", lambda *_: self._redraw_log())

        tk.Checkbutton(lt, text="auto-scroll", variable=self._autoscroll,
                       bg=HDR, fg=TEXT, selectcolor=HDR,
                       activebackground=HDR, activeforeground=TEXT,
                       font=MONO9).pack(side=tk.LEFT, padx=8)

        # Debug switch for save-online round-trip (watch_only / read_now traces)
        tk.Checkbutton(lt, text="Save debug", variable=self._dbg_save,
                       command=lambda: set_debug_save(self._dbg_save.get()),
                       bg=HDR, fg=TEXT, selectcolor=HDR,
                       activebackground=HDR, activeforeground=TEXT,
                       font=MONO9).pack(side=tk.LEFT, padx=8)

        tk.Button(lt, text="Clear", command=self._clear_log,
                  bg=HDR, fg=TEXT2, font=MONO9, bd=0, cursor="hand2",
                  activebackground=HDR, activeforeground=TEXT
                  ).pack(side=tk.RIGHT)

        tk.Frame(tab_log, bg=BORD, height=1).pack(fill=tk.X)

        log_frame = tk.Frame(tab_log, bg=HDR)
        log_frame.pack(fill=tk.BOTH, expand=True)

        sb = tk.Scrollbar(log_frame, bg=BORD, troughcolor=BG,
                          activebackground="#2a5070", width=10)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        self._log = tk.Text(log_frame, bg=HDR, fg=TEXT, font=MONO9,
                            bd=0, wrap=tk.WORD, state=tk.DISABLED,
                            insertbackground=TEXT,
                            highlightthickness=0, padx=12, pady=8,
                            yscrollcommand=sb.set)
        self._log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.config(command=self._log.yview)

        for lvl, col in LOG_FG.items():
            self._log.tag_config(f"L{lvl}", foreground=col)
        self._log.tag_config("Lts",  foreground=TEXT2)
        self._log.tag_config("Lsrc", foreground="#60a8d8")
        self._log.tag_config("Lmsg", foreground=TEXT)

        # ── Tab 2: Tags / Values ─────────────────────────────────────────────
        tab_vals = tk.Frame(nb, bg=HDR)
        nb.add(tab_vals, text="  Tags / Values  ")

        # toolbar
        vt = tk.Frame(tab_vals, bg=HDR, padx=12, pady=6)
        vt.pack(fill=tk.X)
        self._lbl_vcnt = tk.Label(vt, text="Tags: —", bg=HDR, fg=TEXT2, font=MONO9)
        self._lbl_vcnt.pack(side=tk.LEFT)
        tk.Button(vt, text="Clear", command=self._clear_values,
                  bg=HDR, fg=TEXT2, font=MONO9, bd=0, cursor="hand2",
                  activebackground=HDR, activeforeground=TEXT
                  ).pack(side=tk.RIGHT)

        tk.Frame(tab_vals, bg=BORD, height=1).pack(fill=tk.X)

        # treeview
        tv_frame = tk.Frame(tab_vals, bg=HDR)
        tv_frame.pack(fill=tk.BOTH, expand=True)

        tv_sb = tk.Scrollbar(tv_frame, bg=BORD, troughcolor=BG,
                              activebackground="#2a5070", width=10)
        tv_sb.pack(side=tk.RIGHT, fill=tk.Y)

        cols = ("tag", "value", "type", "updated")
        self._tv = ttk.Treeview(tv_frame, columns=cols, show="headings",
                                yscrollcommand=tv_sb.set, selectmode="browse")
        tv_sb.config(command=self._tv.yview)

        self._tv.heading("tag",     text="Address", anchor=tk.W)
        self._tv.heading("value",   text="Value",   anchor=tk.W)
        self._tv.heading("type",    text="Type",    anchor=tk.W)
        self._tv.heading("updated", text="Updated", anchor=tk.W)

        self._tv.column("tag",     width=160, minwidth=80,  stretch=False)
        self._tv.column("value",   width=140, minwidth=60,  stretch=True)
        self._tv.column("type",    width=80,  minwidth=50,  stretch=False)
        self._tv.column("updated", width=90,  minwidth=70,  stretch=False)

        self._tv.tag_configure("on",  foreground=ACC)
        self._tv.tag_configure("off", foreground="#904040")
        self._tv.tag_configure("err", foreground=ERR)
        self._tv.tag_configure("num", foreground="#60a8d8")

        self._tv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # Fill the preset list and pre-select the one used last time. Only the
        # input fields are restored — nothing connects until the user says so.
        self._refresh_dev_combo(select=self._devs.last)
        d = self._devs.find(self._devs.last)
        if d:
            self._apply_dev(d)

    def _style(self):
        s = ttk.Style()
        s.theme_use("default")
        s.configure("TCombobox",
            fieldbackground="#06090e", background="#06090e",
            foreground=TEXT, selectbackground="#06090e",
            selectforeground=TEXT, arrowcolor=TEXT2,
            bordercolor=BORD, lightcolor=BORD, darkcolor=BORD,
            insertcolor=TEXT)
        s.map("TCombobox",
              fieldbackground=[("readonly","#06090e")],
              foreground=[("readonly", TEXT)],
              selectbackground=[("readonly","#06090e")])
        s.configure("TNotebook",
            background=BG, borderwidth=0, tabmargins=[0,0,0,0])
        s.configure("TNotebook.Tab",
            background=CARD, foreground=TEXT2, font=MONO9,
            padding=[12, 5], borderwidth=0)
        s.map("TNotebook.Tab",
              background=[("selected", HDR)],
              foreground=[("selected", TEXT)])
        s.configure("Treeview",
            background="#06090e", foreground=TEXT,
            fieldbackground="#06090e", bordercolor=BORD,
            font=MONO9, rowheight=22)
        s.configure("Treeview.Heading",
            background=CARD, foreground=TEXT2, font=MONO9,
            relief="flat", borderwidth=1)
        s.map("Treeview",
              background=[("selected","#0d2030")],
              foreground=[("selected","#90d0f0")])

    # ── Fields ────────────────────────────────────────────────────────────────
    def _fields(self) -> dict:
        """Connection fields as typed, normalised. Raises ValueError on junk."""
        return {
            "ip":       self._v_ip.get().strip(),
            "slot":     int(self._v_slot.get() or 0),
            "type":     self._v_type.get(),
            "interval": poll_interval_from(self._v_iv.get()),
            "groups":   max(0, min(16, int(self._v_groups.get() or 0))),
            "port":     max(0, min(65535, int(self._v_port.get() or 0))),
            "rslinx":   self._v_rslinx.get(),
            "io_block": self._v_ioblk.get() and self._v_type.get() != "logix",
            "records_dir": self._v_recdir.get().strip(),
        }

    def _cfg_from_fields(self, f: dict) -> Config:
        cfg = device_to_config({
            "ip": f["ip"], "slot": f["slot"], "type": f["type"],
            "interval": f["interval"], "groups": f["groups"],
            "port": f["port"], "rslinx": f["rslinx"],
            "io_block": f["io_block"],
        })
        if f.get("records_dir"):
            cfg.records_dir = f["records_dir"]
        return cfg

    def _session_name(self, f: dict) -> str:
        """Preset name when one is picked, otherwise the address itself."""
        d = self._selected_dev()
        return (d["name"] if d else "") or f["ip"] or "plc"

    # ── Button handlers ───────────────────────────────────────────────────────
    def _on_start(self):
        try:
            f = self._fields()
        except ValueError:
            messagebox.showwarning("Invalid input",
                                   "Slot, interval, connections and port must be "
                                   "numbers.", parent=self)
            return
        if not f["ip"]:
            messagebox.showwarning("No address", "Enter an IP address first.",
                                   parent=self)
            return
        name = self._session_name(f)
        if name in self._sessions:
            messagebox.showinfo("Already running",
                                f"“{name}” is already running on port "
                                f"{self._sessions[name].get('ws_port')}.\n\n"
                                f"Pick another device (or change the name it is "
                                f"saved under) to start a second controller.",
                                parent=self)
            return
        self.bridge.start_session(name, self._cfg_from_fields(f))
        self._hint(f"starting “{name}”…")

    def _on_stop(self):
        if not self._sel:
            self._hint("select a session first")
            return
        self.bridge.stop_session(self._sel)
        self._hint(f"stopping “{self._sel}”…")

    def _on_stop_all(self):
        if not self._sessions:
            return
        if not messagebox.askyesno("Stop all?",
                                   f"Stop all {len(self._sessions)} PLC session(s)?",
                                   parent=self):
            return
        self.bridge.stop_all()

    def _on_reconnect(self):
        if not self._sel:
            self._hint("select a session first")
            return
        try:
            f = self._fields()
        except ValueError:
            messagebox.showwarning("Invalid input",
                                   "Slot, interval, connections and port must be "
                                   "numbers.", parent=self)
            return
        self.bridge.apply_and_reconnect(self._sel, f)

    def _on_copy_url(self):
        st = self._sessions.get(self._sel)
        if not st:
            self._hint("select a session first")
            return
        url = st.get("ws_url") or f"ws://localhost:{st.get('ws_port')}"
        self.clipboard_clear()
        self.clipboard_append(url)
        self._hint(f"{url} → clipboard")

    # ── Saved devices ─────────────────────────────────────────────────────────
    def _refresh_dev_combo(self, select: str = ""):
        labels = [DeviceStore.label(d) for d in self._devs.devices]
        self._cb_dev["values"] = labels
        d = self._devs.find(select) if select else None
        if d:
            self._v_dev.set(DeviceStore.label(d))
        elif self._v_dev.get() not in labels:
            self._v_dev.set("")

    def _selected_dev(self):
        """Current combobox row → device dict. Readonly combobox, so the index
        is always in sync with the list order used to fill it."""
        i = self._cb_dev.current()
        return self._devs.devices[i] if 0 <= i < len(self._devs.devices) else None

    def _apply_dev(self, d: dict):
        """Fill the connection fields from a preset. Deliberately does NOT
        reconnect: dropping a live connection because a list item was clicked
        would be a nasty surprise. The user presses Start/Reconnect."""
        self._v_type.set(d["type"])
        self._v_ip.set(d["ip"])
        self._v_slot.set(str(d["slot"]))
        self._v_iv.set(str(d["interval"]))
        self._v_groups.set(str(d.get("groups", 0)))
        self._v_port.set(str(d.get("port", 0)))
        self._v_rslinx.set(d["rslinx"])
        if d["type"] != "logix":                 # Logix keeps the box cleared
            self._v_ioblk.set(bool(d.get("io_block", False)))

    def _sync_ioblk(self, *_):
        """Read I/O as block is an SLC/MicroLogix mode — Logix has no I/O image
        to read in one request (tags go by name, libplctag packs them). Picking
        Logix greys the box out and clears it; picking SLC again gives back
        what it held."""
        logix = self._v_type.get() == "logix"
        greyed = str(self._cb_ioblk["state"]) == tk.DISABLED
        if logix and not greyed:
            self._ioblk_keep = self._v_ioblk.get()
            self._v_ioblk.set(False)
            self._cb_ioblk.config(state=tk.DISABLED)
        elif not logix and greyed:
            self._cb_ioblk.config(state=tk.NORMAL)
            self._v_ioblk.set(self._ioblk_keep)

    def _hint(self, text: str, ms: int = 5000):
        self._lbl_hint.config(text=text)
        if self._hint_job:
            self.after_cancel(self._hint_job)
        self._hint_job = self.after(ms, lambda: self._lbl_hint.config(text=""))

    def _on_dev_select(self, _evt=None):
        d = self._selected_dev()
        if not d:
            return
        self._apply_dev(d)
        self._devs.last = d["name"]
        self._devs.save()
        if d["name"] in self._sessions:
            self._select_session(d["name"])
            self._hint("already running — ↻ Reconnect applies the fields")
        else:
            self._hint("fields filled in — ▶ Start runs it alongside the rest")

    def _on_dev_save(self):
        try:
            f = self._fields()
        except ValueError:
            messagebox.showwarning("Invalid input",
                                   "Slot, interval, connections and port must be "
                                   "numbers.", parent=self)
            return
        if not f["ip"]:
            messagebox.showwarning("No address",
                                   "Enter an IP address first.", parent=self)
            return
        dev = {"name": "", "ip": f["ip"], "slot": f["slot"], "type": f["type"],
               "interval": f["interval"], "groups": f["groups"],
               "port": f["port"], "rslinx": f["rslinx"],
               "io_block": f["io_block"]}

        cur  = self._selected_dev()
        name = _ask_name(self, "Save device", "Device name:",
                         initial=(cur["name"] if cur else ""))
        if name is None:
            return
        name = name.strip()
        if not name:
            messagebox.showwarning("Empty name",
                                   "The name cannot be empty.", parent=self)
            return
        if self._devs.find(name) and not messagebox.askyesno(
                "Overwrite?",
                f"“{name}” is already in the list. Overwrite?", parent=self):
            return

        dev["name"] = name
        if self._devs.put(dev):
            self._refresh_dev_combo(select=name)
            self._hint(f"“{name}” saved")
        else:
            messagebox.showerror("Error",
                                 f"Could not write the list:\n{self._devs.path}",
                                 parent=self)

    def _on_dev_delete(self):
        d = self._selected_dev()
        if not d:
            self._hint("select a device in the list")
            return
        if not messagebox.askyesno("Delete?",
                                   f"Remove “{d['name']}” from the list?", parent=self):
            return
        if self._devs.remove(d["name"]):
            self._v_dev.set("")
            self._refresh_dev_combo()
            self._hint(f"“{d['name']}” deleted")
        else:
            messagebox.showerror("Error",
                                 f"Could not write the list:\n{self._devs.path}",
                                 parent=self)

    # ── Queue consumer ────────────────────────────────────────────────────────
    def _poll(self):
        try:
            while True:
                self._dispatch(self.bridge.q.get_nowait())
        except Empty:
            pass
        self.after(120, self._poll)

    def _dispatch(self, msg):
        kind = msg[0]
        if kind == "sessions":
            self._update_sessions(msg[1])
        elif kind == "values":
            _, name, values = msg
            self._update_values(name, values)
        elif kind == "log":
            self._append_log(msg[1])
        elif kind == "busy":
            self._hint(f"“{msg[1]}” connecting…")
        elif kind == "started":
            _, name, port = msg
            self._select_session(name)
            self._hint(f"“{name}” → ws://localhost:{port}")
        elif kind == "failed":
            _, name, err = msg
            messagebox.showerror("Session not started",
                                 f"“{name}” could not start:\n\n{err}", parent=self)
        elif kind == "stopped":
            pass   # the next snapshot removes the row

    # ── Sessions table ────────────────────────────────────────────────────────
    def _update_sessions(self, rows: list):
        self._sessions = {r["name"]: r for r in rows}
        for r in rows:
            if r.get("slug"):
                self._slug2name[r["slug"]] = r["name"]

        # drop rows that are gone
        for iid in self._sv.get_children():
            if iid not in self._sessions:
                self._sv.delete(iid)

        for r in rows:
            if r["connected"]:
                status, tag = f"CONNECTED  {r['controller'].get('name','')}".strip(), "ok"
            elif r.get("crashed"):
                # The poll loop died with the port still open and tabs still
                # attached. Amber would read as "coming up"; this one is not
                # coming up on its own — it needs Stop → Start.
                status, tag = f"POLLING STOPPED — {r['crashed']}", "bad"
            elif r["running"]:
                status, tag = (r["error"] or "connecting…"), "warn"
            else:
                status, tag = "stopped", "bad"
            cycle, _col = _cycle_text(r)
            vals = (r["name"], f"{r['ip']}:{r['slot']}", r["type"],
                    str(r["ws_port"]), status[:60], str(r["watching"]), cycle)
            if self._sv.exists(r["name"]):
                self._sv.item(r["name"], values=vals, tags=(tag,))
            else:
                self._sv.insert("", tk.END, iid=r["name"], values=vals, tags=(tag,))

        # keep a valid selection
        if self._sel not in self._sessions:
            self._sel = ""
            first = next(iter(self._sessions), "")
            if first:
                self._select_session(first)

        # source filter list
        wanted = [ALL_SESSIONS] + sorted(self._sessions.keys())
        if list(self._cb_src["values"]) != wanted:
            self._cb_src["values"] = wanted
            if self._src_filter.get() not in wanted:
                self._src_filter.set(ALL_SESSIONS)

        n_conn = sum(1 for r in rows if r["connected"])
        if not rows:
            self._lbl_st.config(text="● NO SESSIONS", fg=TEXT2)
            self._lbl_ctrl.config(text="")
            self._lbl_ws.config(text="")
            self._lbl_tags.config(text="—", fg=TEXT2)
            self._lbl_cycle.config(text="", fg=TEXT2)
        else:
            self._lbl_st.config(
                text=f"● {n_conn}/{len(rows)} CONNECTED",
                fg=ACC if n_conn == len(rows) else (WARN if n_conn else ERR))

        self._btn_stopall.config(state=tk.NORMAL if rows else tk.DISABLED)
        self._sync_sel_widgets()

    def _select_session(self, name: str):
        if name and self._sv.exists(name):
            self._sv.selection_set(name)
            self._sv.focus(name)
        self._sel = name
        self.bridge.focus = name
        self._sync_sel_widgets()

    def _on_sess_select(self, _evt=None):
        sel = self._sv.selection()
        name = sel[0] if sel else ""
        if name == self._sel:
            return
        self._sel = name
        self.bridge.focus = name
        st = self._sessions.get(name)
        if st:
            # Fill the fields from the live session so Reconnect edits THIS one.
            self._v_type.set(st["type"])
            self._v_ip.set(st["ip"])
            self._v_slot.set(str(st["slot"]))
            self._v_iv.set(str(st["interval"]))
            self._v_groups.set(str(st.get("groups", 0)))
            self._v_port.set(str(st["ws_port"]))
            self._v_rslinx.set(bool(st.get("rslinx")))
            if st["type"] != "logix":
                self._v_ioblk.set(bool(st.get("io_block")))
        self._sync_sel_widgets()

    def _sync_sel_widgets(self):
        st = self._sessions.get(self._sel)
        on = tk.NORMAL if st else tk.DISABLED
        self._btn_stop.config(state=on)
        self._btn_rc.config(state=on)
        self._btn_copy.config(state=on)
        if not st:
            self._lbl_ws.config(text="")
            self._lbl_ctrl.config(text="")
            self._lbl_tags.config(text="—", fg=TEXT2)
            self._lbl_cycle.config(text="", fg=TEXT2)
            return
        self._lbl_ws.config(text=f"{st['name']} → {st.get('ws_url','')}")
        self._lbl_ctrl.config(text=f"  {st['controller'].get('name','') or st.get('error','')}")
        n = st["watching"]
        self._lbl_tags.config(text=str(n) if n else "—",
                              fg=ACC if (st["connected"] and n) else TEXT2)
        self._update_stats(st)

    def _update_stats(self, st: dict):
        """Cycle time next to the tag count.

        Colour is the whole point of this readout: amber means the read of the
        current watch list no longer fits the poll interval, red means tags are
        already timing out. The interval itself is never touched automatically —
        it stays exactly where it was set, so this is the only place the
        overload becomes visible before values start dropping out.
        """
        text, col = _cycle_text(st)
        self._lbl_cycle.config(text=text, fg=col)

    # ── Log ───────────────────────────────────────────────────────────────────
    def _append_log(self, r):
        self._all_log.append(r)
        if self._passes(r):
            self._write(r)

    def _passes(self, r):
        f = self._log_filter.get()
        if LEVEL_ORD.get(r["level"], 1) < FILTER_THR.get(f, -1):
            return False
        src = self._src_filter.get()
        if src and src != ALL_SESSIONS:
            return self._slug2name.get(r.get("src", ""), r.get("src", "")) == src
        return True

    def _write(self, r):
        ts  = time.strftime("%H:%M:%S", time.localtime(r["ts"]))
        lvl = r["level"]
        src = r.get("src", "")
        t   = self._log
        t.config(state=tk.NORMAL)
        t.insert(tk.END, f"{ts}  ", "Lts")
        t.insert(tk.END, f"[{lvl}]", f"L{lvl}")
        if src:
            t.insert(tk.END, f" [{self._slug2name.get(src, src)}]", "Lsrc")
        t.insert(tk.END, f"  {r['msg']}\n", "Lmsg")
        t.config(state=tk.DISABLED)
        if self._autoscroll.get():
            t.see(tk.END)

    def _redraw_log(self):
        t = self._log
        t.config(state=tk.NORMAL)
        t.delete("1.0", tk.END)
        t.config(state=tk.DISABLED)
        for r in self._all_log:
            if self._passes(r):
                self._write(r)

    # ── Values tab ────────────────────────────────────────────────────────────
    def _update_values(self, name: str, values: dict):
        # Drop rows for tags that are no longer in the poller's value cache
        # (happens when graph is cleared or tags are removed via watch_only).
        incoming = set(values.keys())
        for iid in self._tv.get_children():
            if iid not in incoming:
                self._tv.delete(iid)

        for tag, info in sorted(values.items()):
            val   = info.get("value")
            typ   = info.get("type") or ""
            err   = info.get("error") or ""
            ts    = info.get("ts")
            ts_s  = time.strftime("%H:%M:%S", time.localtime(ts)) if ts else ""
            val_s = err if err else ("—" if val is None else str(val))

            if err:
                row_tag = ("err",)
            elif isinstance(val, bool) or (isinstance(val, int) and typ in ("BOOL","bool","")):
                row_tag = ("on",) if val else ("off",)
            elif isinstance(val, (int, float)):
                row_tag = ("num",)
            else:
                row_tag = ()

            if self._tv.exists(tag):
                self._tv.item(tag, values=(tag, val_s, typ, ts_s), tags=row_tag)
            else:
                self._tv.insert("", tk.END, iid=tag,
                                values=(tag, val_s, typ, ts_s), tags=row_tag)

        n = len(self._tv.get_children())
        head = f"Tags: {n}" if n else "Tags: —"
        self._lbl_vcnt.config(text=f"{head}   ·   {name}" if name else head)

    def _clear_values(self):
        self._tv.delete(*self._tv.get_children())
        self._lbl_vcnt.config(text="Tags: —")

    def _clear_log(self):
        self._all_log.clear()
        t = self._log
        t.config(state=tk.NORMAL)
        t.delete("1.0", tk.END)
        t.config(state=tk.DISABLED)

    def on_close(self):
        # Gone from the screen at once, but the interpreter ends only after the
        # sessions are really down. A fixed 600 ms used to end it while a
        # session was still stopping, with its libplctag handles still open.
        self.withdraw()
        done = self.bridge.stop_all()
        deadline = time.monotonic() + CLOSE_WAIT_S

        def wait():
            if done.done() or time.monotonic() >= deadline:
                self.destroy()
            else:
                self.after(50, wait)
        wait()


# ── Cycle readout ─────────────────────────────────────────────────────────────
def _cycle_text(st: dict):
    """(text, colour) for one session's poll cycle. Shared by the strip and the
    sessions table so both tell the same story."""
    if not st.get("connected"):
        return "", TEXT2
    if st.get("paused"):
        # Connected, but nothing is read until a tab (or an HTTP reader) attaches.
        return "Polling paused — no clients", TEXT2
    cycle = st.get("cycle_ms") or 0.0
    interval = st.get("interval") or 0.0
    timeouts = st.get("timeouts") or 0
    if cycle <= 0:
        return "", TEXT2
    text = f"Cycle: {cycle:.0f} ms · interval {interval:.2f} s"
    reads = st.get("reads")
    if reads:
        # Requests, not tags: bits fold into their word, timer members into
        # one structure read, and SLC runs into one block read.
        text += f" · requests: {reads}"
        # Payload beside it, because which of the two costs more depends on
        # the link: a request costs scan time on a direct connection, bytes
        # cost wire time behind a serial gateway. Both visible = tunable.
        nbytes = st.get("bytes") or 0
        if nbytes:
            text += (f" · {nbytes / 1024:.1f} KB" if nbytes >= 1024
                     else f" · {nbytes} B")
    groups = st.get("groups")
    if groups:
        # Shown because it is the knob that moves the cycle time: one
        # connection reads the watch list strictly round-trip by round-trip.
        text += f" · conn.: {groups}"
    if timeouts:
        text += f" · timeouts: {timeouts}"
        return text, ERR
    if cycle > CYCLE_WARN_MS:
        # Not "longer than the interval": a controller behind a serial gateway
        # never fits 200 ms, and a permanently amber readout says nothing.
        # Amber is kept for a cycle that is genuinely too long.
        return text, WARN
    return text, ACC


# ── Widget helpers ────────────────────────────────────────────────────────────
def _lbl(parent, text):
    return tk.Label(parent, text=text, bg=parent.cget("bg"),
                    fg=TEXT2, font=MONO9)

def _entry(parent, var, width=14):
    return tk.Entry(parent, textvariable=var, width=width,
                    bg="#06090e", fg=TEXT, insertbackground=TEXT,
                    font=MONO, bd=0,
                    highlightbackground=BORD, highlightthickness=1)

def _btn(parent, text, cmd, state=tk.NORMAL,
         bg=CARD, fg=TEXT2, abg="#0d2030", afg=TEXT):
    return tk.Button(parent, text=text, command=cmd, state=state,
                     bg=bg, fg=fg, font=MONO, bd=0, padx=14, pady=6,
                     cursor="hand2", activebackground=abg, activeforeground=afg,
                     highlightbackground=BORD, highlightthickness=1)

def _btn_sm(parent, text, cmd, bg=CARD, fg=TEXT2, abg="#0d2030", afg=TEXT):
    """Compact variant of _btn — fits inline next to a combobox."""
    return tk.Button(parent, text=text, command=cmd,
                     bg=bg, fg=fg, font=MONO9, bd=0, padx=8, pady=3,
                     cursor="hand2", activebackground=abg, activeforeground=afg,
                     highlightbackground=BORD, highlightthickness=1)


def _ask_name(parent, title, prompt, initial=""):
    """Modal single-line prompt in the app palette (simpledialog is stock-light
    and looks foreign here). Returns the string, or None if cancelled."""
    win = tk.Toplevel(parent, bg=CARD, padx=16, pady=14)
    win.title(title)
    win.resizable(False, False)
    win.transient(parent)
    out = {"v": None}

    tk.Label(win, text=prompt, bg=CARD, fg=TEXT, font=MONO9).pack(anchor=tk.W)
    var = tk.StringVar(value=initial)
    ent = _entry(win, var, width=34)
    ent.pack(fill=tk.X, pady=(6, 12))

    def ok(_e=None):
        out["v"] = var.get()
        win.destroy()

    row = tk.Frame(win, bg=CARD)
    row.pack(fill=tk.X)
    _btn_sm(row, "  OK  ", ok, bg=ACC, fg="#040e08",
            abg="#2ab060", afg="#040e08").pack(side=tk.RIGHT, padx=(6, 0))
    _btn_sm(row, "Cancel", win.destroy).pack(side=tk.RIGHT)

    ent.bind("<Return>", ok)
    win.bind("<Escape>", lambda _e: win.destroy())

    # Centre over the parent before grabbing focus, otherwise the dialog lands
    # at the top-left corner of the screen on Windows.
    win.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width()  - win.winfo_width())  // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - win.winfo_height()) // 3
    win.geometry(f"+{max(0, x)}+{max(0, y)}")

    ent.focus_set()
    ent.select_range(0, tk.END)
    win.grab_set()
    parent.wait_window(win)
    return out["v"]


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not LIBPLCTAG_OK:
        import tkinter.messagebox as mb
        tk.Tk().withdraw()
        mb.showerror("libplctag missing",
                     "Put plctag.dll into Bridge/libplctag_2.6.16_windows_x64/\n\n"
                     "Install dependencies:\n  pip install websockets aiohttp aiohttp-cors")
        sys.exit(1)

    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    if not WS_OK:
        # Sessions would still poll, but nothing could connect to them — say so
        # once, at start, instead of leaving the browser tab timing out.
        messagebox.showwarning(
            "websockets missing",
            "The `websockets` package is not installed, so PLC sessions will "
            "not serve a WebSocket port and the tracer cannot connect.\n\n"
            "  pip install websockets", parent=app)
    app.mainloop()
