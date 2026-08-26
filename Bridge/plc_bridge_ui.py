"""
PLC Bridge — GUI Control Panel
================================
Run:  python plc_bridge_ui.py

Runs the bridge straight from the window:
  • IP / slot / type / interval setup
  • Saved device list (name + IP) for quick switching
  • Start / Stop / Reconnect buttons
  • Connection status and polled tag count
  • Log with level filtering

The WebSocket (port 8765) starts automatically — the tracer connects as usual.
"""

import sys, threading, asyncio, time, logging, json, os
from queue import Queue, Empty
from pathlib import Path

# ── Import bridge components ──────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
try:
    from plc_bridge_500 import (
        Config, LibplctagConnection, TagPoller,
        MemoryLogHandler, LogBuffer, ws_handler,
        create_connection, set_debug_save,
        LIBPLCTAG_OK, WS_OK, DEFAULT_WS,
        _CYCLE_WARN_MS as CYCLE_WARN_MS,
    )
except ImportError as e:
    import tkinter as tk
    from tkinter import messagebox
    tk.Tk().withdraw()
    messagebox.showerror("Error", f"plc_bridge_500.py not found next to this file:\n{e}")
    sys.exit(1)

try:
    import websockets
except ImportError:
    websockets = None

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


# ── Saved devices ─────────────────────────────────────────────────────────────
DEV_FILE = Path(__file__).parent / "plc_bridge_devices.json"


class DeviceStore:
    """Named connection presets ("devices"), persisted next to the script.

    File: {"version":1, "last":<name>, "devices":[{name, ip, slot, type,
    interval, rslinx}, ...]}.  A missing or broken file degrades to an empty
    list — the panel must stay usable without it, this is a convenience
    feature and never a precondition for connecting.
    """

    def __init__(self, path: Path = DEV_FILE):
        self.path    = path
        self.devices = []      # list[dict], sorted by name
        self.last    = ""      # name of the last selected device
        self.load()

    # ── persistence ───────────────────────────────────────────────────────────
    def load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except Exception as e:
            logging.getLogger("bridge500").warning(
                f"Device list not loaded ({self.path.name}): {e}")
            return
        self.last = str(raw.get("last", "") or "")
        for d in raw.get("devices", []):
            try:
                dev = self._norm(d)
            except Exception:
                continue          # skip a single malformed entry, keep the rest
            if dev["name"] and dev["ip"]:
                self.devices.append(dev)
        self._sort()

    def save(self) -> bool:
        """Atomic write via staging file — same approach as the recorder."""
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps({"version": 1, "last": self.last,
                            "devices": self.devices},
                           ensure_ascii=False, indent=2),
                encoding="utf-8")
            os.replace(tmp, self.path)
            return True
        except Exception as e:
            logging.getLogger("bridge500").error(
                f"Device list not saved ({self.path}): {e}")
            try:
                tmp.unlink()
            except OSError:
                pass
            return False

    # ── contents ──────────────────────────────────────────────────────────────
    @staticmethod
    def _norm(d: dict) -> dict:
        return {
            "name":     str(d.get("name", "")).strip(),
            "ip":       str(d.get("ip", "")).strip(),
            "slot":     int(d.get("slot", 0) or 0),
            "type":     "logix" if str(d.get("type", "slc")).lower() == "logix" else "slc",
            "interval": max(0.2, float(d.get("interval", 0.2) or 0.2)),
            # Parallel CIP connections; 0 = auto. Presets written before this
            # setting existed simply come back as auto.
            "groups":   max(0, min(16, int(d.get("groups", 0) or 0))),
            "rslinx":   bool(d.get("rslinx", False)),
        }

    @staticmethod
    def label(d: dict) -> str:
        return f"{d['name']}  ·  {d['ip']}:{d['slot']}  ·  {d['type']}"

    def _sort(self):
        self.devices.sort(key=lambda d: d["name"].lower())

    def find(self, name: str):
        n = (name or "").strip().lower()
        return next((d for d in self.devices if d["name"].lower() == n), None)

    def put(self, dev: dict) -> bool:
        """Add or overwrite by name (case-insensitive) and persist."""
        dev = self._norm(dev)
        old = self.find(dev["name"])
        if old:
            self.devices.remove(old)
        self.devices.append(dev)
        self._sort()
        self.last = dev["name"]
        return self.save()

    def remove(self, name: str) -> bool:
        d = self.find(name)
        if not d:
            return False
        self.devices.remove(d)
        if self.last.strip().lower() == name.strip().lower():
            self.last = ""
        return self.save()


# ── Bridge controller ─────────────────────────────────────────────────────────
class BridgeController:
    """Runs asyncio bridge in a daemon thread; pushes updates to gui_queue."""

    def __init__(self, gui_q: Queue):
        self.q         = gui_q
        self.cfg       = Config()
        self.log_buf   = LogBuffer()
        self.conn      = None
        self.poller    = None
        self._ws_srv   = None
        self._ptask    = None   # poller task
        self._uitask   = None   # ui-poll task
        self._running  = False
        self._last_seq = 0

        # all log output → memory buffer
        h = MemoryLogHandler(self.log_buf)
        h.setFormatter(logging.Formatter("%(asctime)s  %(message)s", datefmt="%H:%M:%S"))
        root_logger = logging.getLogger()
        root_logger.addHandler(h)
        root_logger.setLevel(logging.INFO)

        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

    # ── Public (GUI thread) ───────────────────────────────────────────────────
    def start(self):
        if self._running:
            return
        self._running = True
        asyncio.run_coroutine_threadsafe(self._start(), self.loop)

    def stop(self):
        self._running = False
        asyncio.run_coroutine_threadsafe(self._stop(), self.loop)

    def reconnect(self):
        asyncio.run_coroutine_threadsafe(self._reconnect(), self.loop)

    def apply_config(self, ip, slot, ctrl_type, interval, via_rslinx, records_dir="",
                     conn_groups=None):
        need_rc = (ip        != self.cfg.ip             or
                   slot      != self.cfg.slot           or
                   ctrl_type != self.cfg.controller_type or
                   via_rslinx!= self.cfg.via_rslinx)
        self.cfg.ip              = ip
        self.cfg.slot            = slot
        self.cfg.controller_type = ctrl_type
        self.cfg.processor_type  = "Logix5000" if ctrl_type == "logix" else "SLC"
        self.cfg.via_rslinx      = via_rslinx
        self.cfg.poll_interval   = interval
        if records_dir:
            self.cfg.records_dir = records_dir
        if self.poller:
            self.poller.cfg.poll_interval = interval
        if conn_groups is not None and conn_groups != self.cfg.conn_groups:
            # Handles carry their connection id from creation, so switching the
            # count means rebuilding them — the connection itself stays up.
            if self.conn and self._running:
                asyncio.run_coroutine_threadsafe(
                    self.conn.update_conn_groups(conn_groups), self.loop)
            else:
                self.cfg.conn_groups = conn_groups
        if need_rc and self._running:
            self.reconnect()

    # ── Async internals ───────────────────────────────────────────────────────
    async def _start(self):
        self.conn   = create_connection(self.cfg)
        self.poller = TagPoller(self.conn, self.cfg)
        self.q.put(("status", "CONNECTING…", WARN))
        ok = await self.conn.connect()
        self.q.put(("connected", ok, self.conn.controller_info, self.conn.error))

        self._ptask  = asyncio.create_task(self.poller.run())
        self._uitask = asyncio.create_task(self._ui_loop())

        if WS_OK and websockets:
            self._ws_srv = await websockets.serve(
                lambda ws: ws_handler(ws, self.poller, self.conn, self.cfg),
                "0.0.0.0", self.cfg.port_ws,
            )
            logging.getLogger("bridge500").info(
                f"WebSocket started → ws://localhost:{self.cfg.port_ws}")

    async def _stop(self):
        if self.poller:  self.poller.stop()
        for t in (self._ptask, self._uitask):
            if t: t.cancel()
        if self._ws_srv:
            self._ws_srv.close()
            await self._ws_srv.wait_closed()
            self._ws_srv = None
        if self.conn: await self.conn.disconnect()
        self.q.put(("connected", False, {}, "Stopped"))

    async def _reconnect(self):
        if not self.conn: return
        await self.conn.disconnect()
        # Recreate connection object — controller type may have changed (SLC ↔ Logix)
        self.conn = create_connection(self.cfg)
        if self.poller:
            self.poller.conn = self.conn
        ok = await self.conn.connect()
        self.q.put(("connected", ok, self.conn.controller_info, self.conn.error))

    async def _ui_loop(self):
        """Push log + status + values updates to GUI queue every ~1 s."""
        while self._running:
            new = self.log_buf.since(self._last_seq)
            if new:
                self._last_seq = new[-1]["seq"]
                for r in new:
                    self.q.put(("log", r))
            if self.conn and self.poller:
                self.q.put(("watching",
                            list(self.poller.watched),
                            self.conn.connected))
                # Real cost of one poll cycle. When it exceeds the interval the
                # bridge cannot keep up with the current watch list — that is
                # what produces "read timeout" on tags.
                self.q.put(("stats", {
                    "cycle_ms":  self.poller.last_cycle_ms,
                    "avg_ms":    self.poller.avg_cycle_ms,
                    "timeouts":  self.poller.last_timeouts,
                    "interval":  self.cfg.poll_interval,
                    "groups":    self.conn._effective_groups(),
                    "reads":     getattr(self.conn, "last_phys_reads", 0),
                    "bytes":     getattr(self.conn, "last_phys_bytes", 0),
                    "connected": self.conn.connected,
                }))
                # Always push a snapshot (even empty) so the GUI can drop stale rows
                # when tags are removed from the watch list.
                self.q.put(("values", dict(self.poller.values)))
            await asyncio.sleep(0.8)


# ── App window ────────────────────────────────────────────────────────────────
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PLC Bridge")
        self.configure(bg=BG)
        self.geometry("760x640")
        self.minsize(640, 520)

        self.bridge      = BridgeController(Queue())
        self._autoscroll = tk.BooleanVar(value=True)
        self._log_filter = tk.StringVar(value="ALL")
        self._dbg_save   = tk.BooleanVar(value=False)
        self._all_log    = []   # full unfiltered list of log records
        self._devs       = DeviceStore()
        self._started    = False  # bridge running — decides the hint after a preset switch
        self._hint_job   = None   # pending after() that clears the hint label

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
        self._lbl_st = tk.Label(hdr, text="● STOPPED", bg=HDR,
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
                                    values=[], width=36, font=MONO9)
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
        self._v_ip = tk.StringVar(value=self.bridge.cfg.ip)
        _entry(r1, self._v_ip, width=17).pack(side=tk.LEFT, padx=(4, 18))

        _lbl(r1, "Slot").pack(side=tk.LEFT)
        self._v_slot = tk.StringVar(value="0")
        _entry(r1, self._v_slot, width=4).pack(side=tk.LEFT, padx=(4, 0))

        # Row 2 — interval · rslinx
        r2 = tk.Frame(p, bg=CARD)
        r2.pack(fill=tk.X, pady=(0, 12))

        _lbl(r2, "Interval, s").pack(side=tk.LEFT)
        self._v_iv = tk.StringVar(value="0.2")
        _entry(r2, self._v_iv, width=6).pack(side=tk.LEFT, padx=(4, 20))

        # Parallel CIP connections. One connection = one request in flight, so a
        # long watch list is read strictly round-trip after round-trip; spreading
        # it over several connections overlaps them and shortens the cycle.
        _lbl(r2, "Connections").pack(side=tk.LEFT)
        self._v_groups = tk.StringVar(value="0")
        _entry(r2, self._v_groups, width=4).pack(side=tk.LEFT, padx=(4, 4))
        tk.Label(r2, text="0 = auto", bg=CARD, fg=TEXT2,
                 font=MONO9).pack(side=tk.LEFT, padx=(0, 20))

        self._v_rslinx = tk.BooleanVar(value=False)
        tk.Checkbutton(r2, text="RSLinx Gateway", variable=self._v_rslinx,
                       bg=CARD, fg=TEXT, selectcolor="#06090e",
                       activebackground=CARD, activeforeground=TEXT,
                       font=MONO9).pack(side=tk.LEFT)

        # Row 2b — records directory
        r2b = tk.Frame(p, bg=CARD)
        r2b.pack(fill=tk.X, pady=(0, 12))
        _lbl(r2b, "Records folder").pack(side=tk.LEFT)
        self._v_recdir = tk.StringVar(value=self.bridge.cfg.records_dir)
        _entry(r2b, self._v_recdir, width=36).pack(side=tk.LEFT, padx=(4, 0))

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
        self._btn_rc.pack(side=tk.LEFT)

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

    # ── Button handlers ───────────────────────────────────────────────────────
    def _on_start(self):
        self._sync_cfg()
        self.bridge.start()
        self._started = True
        self._btn_start.config(state=tk.DISABLED)
        self._btn_stop.config(state=tk.NORMAL)
        self._btn_rc.config(state=tk.NORMAL)
        if WS_OK and websockets:
            self._lbl_ws.config(
                text=f"ws://localhost:{self.bridge.cfg.port_ws}")

    def _on_stop(self):
        self.bridge.stop()
        self._started = False
        self._btn_start.config(state=tk.NORMAL)
        self._btn_stop.config(state=tk.DISABLED)
        self._btn_rc.config(state=tk.DISABLED)
        self._lbl_ws.config(text="")
        self._lbl_tags.config(text="—", fg=TEXT)
        self._lbl_cycle.config(text="", fg=TEXT2)

    def _on_reconnect(self):
        self._sync_cfg()
        self.bridge.reconnect()

    def _sync_cfg(self):
        try:
            self.bridge.apply_config(
                ip          = self._v_ip.get().strip(),
                slot        = int(self._v_slot.get() or 0),
                ctrl_type   = self._v_type.get(),
                interval    = max(0.2, float(self._v_iv.get() or 0.2)),
                via_rslinx  = self._v_rslinx.get(),
                records_dir = self._v_recdir.get().strip(),
                conn_groups = max(0, min(16, int(self._v_groups.get() or 0))),
            )
        except ValueError:
            pass

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
        self._v_rslinx.set(d["rslinx"])

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
        self._hint("↻ press Reconnect" if self._started else "fields filled in")

    def _on_dev_save(self):
        ip = self._v_ip.get().strip()
        if not ip:
            messagebox.showwarning("No address",
                                   "Enter an IP address first.", parent=self)
            return
        try:
            dev = {"name": "",
                   "ip": ip,
                   "slot": int(self._v_slot.get() or 0),
                   "type": self._v_type.get(),
                   "interval": float(self._v_iv.get() or 0.2),
                   "groups": int(self._v_groups.get() or 0),
                   "rslinx": self._v_rslinx.get()}
        except ValueError:
            messagebox.showwarning("Invalid input",
                                   "Slot and interval must be numbers.", parent=self)
            return

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
        if kind == "connected":
            _, ok, ctrl, err = msg
            if ok:
                self._lbl_st.config(text="● CONNECTED", fg=ACC)
                self._lbl_ctrl.config(text=f"  {ctrl.get('name','')}")
            else:
                self._lbl_st.config(text="○ DISCONNECTED", fg=ERR)
                self._lbl_ctrl.config(text=f"  {err or ''}")
        elif kind == "status":
            _, text, col = msg
            self._lbl_st.config(text=text, fg=col)
            self._lbl_ctrl.config(text="")
        elif kind == "watching":
            _, tags, connected = msg
            n = len(tags)
            self._lbl_tags.config(
                text=str(n) if n else "—",
                fg=ACC if (connected and n) else TEXT2)
        elif kind == "stats":
            self._update_stats(msg[1])
        elif kind == "log":
            self._append_log(msg[1])
        elif kind == "values":
            self._update_values(msg[1])

    def _update_stats(self, st: dict):
        """Cycle time next to the tag count.

        Colour is the whole point of this readout: amber means the read of the
        current watch list no longer fits the poll interval, red means tags are
        already timing out. The interval itself is never touched automatically —
        it stays exactly where it was set, so this is the only place the
        overload becomes visible before values start dropping out.
        """
        if not st.get("connected"):
            self._lbl_cycle.config(text="", fg=TEXT2)
            return
        cycle = st.get("cycle_ms") or 0.0
        interval = st.get("interval") or 0.0
        timeouts = st.get("timeouts") or 0
        if cycle <= 0:
            self._lbl_cycle.config(text="", fg=TEXT2)
            return
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
            col = ERR
        elif cycle > CYCLE_WARN_MS:
            # Not "longer than the interval": a controller behind a serial
            # gateway never fits 200 ms, and a permanently amber readout says
            # nothing. Amber is kept for a cycle that is genuinely too long.
            col = WARN
        else:
            col = ACC
        self._lbl_cycle.config(text=text, fg=col)

    # ── Log ───────────────────────────────────────────────────────────────────
    def _append_log(self, r):
        self._all_log.append(r)
        if self._passes(r["level"]):
            self._write(r)

    def _passes(self, level):
        f = self._log_filter.get()
        return LEVEL_ORD.get(level, 1) >= FILTER_THR.get(f, -1)

    def _write(self, r):
        ts  = time.strftime("%H:%M:%S", time.localtime(r["ts"]))
        lvl = r["level"]
        t   = self._log
        t.config(state=tk.NORMAL)
        t.insert(tk.END, f"{ts}  ", "Lts")
        t.insert(tk.END, f"[{lvl}]", f"L{lvl}")
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
            if self._passes(r["level"]):
                self._write(r)

    # ── Values tab ────────────────────────────────────────────────────────────
    def _update_values(self, values: dict):
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
        self._lbl_vcnt.config(text=f"Tags: {n}" if n else "Tags: —")

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
        self.bridge.stop()
        self.after(400, self.destroy)


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
    app.mainloop()
