"""
PLC Bridge — GUI Control Panel
================================
Запуск:  python plc_bridge_ui.py

Запускает мост прямо из окна:
  • Настройка IP / slot / тип / интервал
  • Кнопки Запустить / Остановить / Переподключить
  • Статус соединения и кол-во опрашиваемых тегов
  • Лог с фильтрацией по уровню

WebSocket (порт 8765) поднимается автоматически — tracer подключается как обычно.
"""

import sys, threading, asyncio, time, logging
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
    )
except ImportError as e:
    import tkinter as tk
    from tkinter import messagebox
    tk.Tk().withdraw()
    messagebox.showerror("Ошибка", f"plc_bridge_500.py не найден рядом:\n{e}")
    sys.exit(1)

try:
    import websockets
except ImportError:
    websockets = None

import tkinter as tk
from tkinter import ttk

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

    def apply_config(self, ip, slot, ctrl_type, interval, via_rslinx, records_dir=""):
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
        if need_rc and self._running:
            self.reconnect()

    # ── Async internals ───────────────────────────────────────────────────────
    async def _start(self):
        self.conn   = create_connection(self.cfg)
        self.poller = TagPoller(self.conn, self.cfg)
        self.q.put(("status", "ПОДКЛЮЧЕНИЕ…", WARN))
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
                f"WebSocket запущен → ws://localhost:{self.cfg.port_ws}")

    async def _stop(self):
        if self.poller:  self.poller.stop()
        for t in (self._ptask, self._uitask):
            if t: t.cancel()
        if self._ws_srv:
            self._ws_srv.close()
            await self._ws_srv.wait_closed()
            self._ws_srv = None
        if self.conn: await self.conn.disconnect()
        self.q.put(("connected", False, {}, "Остановлен"))

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
        self.geometry("760x600")
        self.minsize(640, 520)

        self.bridge      = BridgeController(Queue())
        self._autoscroll = tk.BooleanVar(value=True)
        self._log_filter = tk.StringVar(value="ALL")
        self._dbg_save   = tk.BooleanVar(value=False)
        self._all_log    = []   # full unfiltered list of log records

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
        self._lbl_st = tk.Label(hdr, text="● ОСТАНОВЛЕН", bg=HDR,
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

        # Row 1 — type · IP · slot
        r1 = tk.Frame(p, bg=CARD)
        r1.pack(fill=tk.X, pady=(0, 8))

        _lbl(r1, "Тип").pack(side=tk.LEFT)
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

        _lbl(r2, "Интервал, с").pack(side=tk.LEFT)
        self._v_iv = tk.StringVar(value="0.2")
        _entry(r2, self._v_iv, width=6).pack(side=tk.LEFT, padx=(4, 20))

        self._v_rslinx = tk.BooleanVar(value=False)
        tk.Checkbutton(r2, text="RSLinx Gateway", variable=self._v_rslinx,
                       bg=CARD, fg=TEXT, selectcolor="#06090e",
                       activebackground=CARD, activeforeground=TEXT,
                       font=MONO9).pack(side=tk.LEFT)

        # Row 2b — records directory
        r2b = tk.Frame(p, bg=CARD)
        r2b.pack(fill=tk.X, pady=(0, 12))
        _lbl(r2b, "Папка записей").pack(side=tk.LEFT)
        self._v_recdir = tk.StringVar(value=self.bridge.cfg.records_dir)
        _entry(r2b, self._v_recdir, width=36).pack(side=tk.LEFT, padx=(4, 0))

        # Row 3 — buttons
        r3 = tk.Frame(p, bg=CARD)
        r3.pack(fill=tk.X)

        self._btn_start = _btn(r3, "▶  Запустить", self._on_start,
                               bg=ACC, fg="#040e08",
                               abg="#2ab060", afg="#040e08")
        self._btn_start.pack(side=tk.LEFT, padx=(0, 8))

        self._btn_stop = _btn(r3, "■  Остановить", self._on_stop, state=tk.DISABLED)
        self._btn_stop.pack(side=tk.LEFT, padx=(0, 8))

        self._btn_rc = _btn(r3, "↺  Переподключить", self._on_reconnect,
                            state=tk.DISABLED)
        self._btn_rc.pack(side=tk.LEFT)

        # ─ Watching strip ─
        strip = tk.Frame(self, bg=BG, padx=14, pady=5)
        strip.pack(fill=tk.X)
        _lbl(strip, "Тегов в опросе:").pack(side=tk.LEFT)
        self._lbl_tags = tk.Label(strip, text="—", bg=BG, fg=TEXT, font=MONO9)
        self._lbl_tags.pack(side=tk.LEFT, padx=6)

        # ─ Separator ─
        tk.Frame(self, bg=BORD, height=1).pack(fill=tk.X)

        # ─ Notebook: Лог | Теги/Значения ─
        nb = ttk.Notebook(self)
        nb.pack(fill=tk.BOTH, expand=True)

        # ── Tab 1: Log ──────────────────────────────────────────────────────
        tab_log = tk.Frame(nb, bg=HDR)
        nb.add(tab_log, text="  Лог  ")

        # log toolbar
        lt = tk.Frame(tab_log, bg=HDR, padx=12, pady=6)
        lt.pack(fill=tk.X)

        ttk.Combobox(lt, textvariable=self._log_filter,
                     values=["ALL", "INFO", "WARN", "ERROR"],
                     state="readonly", width=7,
                     font=MONO9).pack(side=tk.LEFT)
        self._log_filter.trace_add("write", lambda *_: self._redraw_log())

        tk.Checkbutton(lt, text="авто-скролл", variable=self._autoscroll,
                       bg=HDR, fg=TEXT, selectcolor=HDR,
                       activebackground=HDR, activeforeground=TEXT,
                       font=MONO9).pack(side=tk.LEFT, padx=8)

        # Debug switch for save-online round-trip (watch_only / read_now traces)
        tk.Checkbutton(lt, text="Debug сохранения", variable=self._dbg_save,
                       command=lambda: set_debug_save(self._dbg_save.get()),
                       bg=HDR, fg=TEXT, selectcolor=HDR,
                       activebackground=HDR, activeforeground=TEXT,
                       font=MONO9).pack(side=tk.LEFT, padx=8)

        tk.Button(lt, text="Очистить", command=self._clear_log,
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
        nb.add(tab_vals, text="  Теги / Значения  ")

        # toolbar
        vt = tk.Frame(tab_vals, bg=HDR, padx=12, pady=6)
        vt.pack(fill=tk.X)
        self._lbl_vcnt = tk.Label(vt, text="Тегов: —", bg=HDR, fg=TEXT2, font=MONO9)
        self._lbl_vcnt.pack(side=tk.LEFT)
        tk.Button(vt, text="Очистить", command=self._clear_values,
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

        self._tv.heading("tag",     text="Адрес",    anchor=tk.W)
        self._tv.heading("value",   text="Значение", anchor=tk.W)
        self._tv.heading("type",    text="Тип",      anchor=tk.W)
        self._tv.heading("updated", text="Обновлено",anchor=tk.W)

        self._tv.column("tag",     width=160, minwidth=80,  stretch=False)
        self._tv.column("value",   width=140, minwidth=60,  stretch=True)
        self._tv.column("type",    width=80,  minwidth=50,  stretch=False)
        self._tv.column("updated", width=90,  minwidth=70,  stretch=False)

        self._tv.tag_configure("on",  foreground=ACC)
        self._tv.tag_configure("off", foreground="#904040")
        self._tv.tag_configure("err", foreground=ERR)
        self._tv.tag_configure("num", foreground="#60a8d8")

        self._tv.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

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
        self._btn_start.config(state=tk.DISABLED)
        self._btn_stop.config(state=tk.NORMAL)
        self._btn_rc.config(state=tk.NORMAL)
        if WS_OK and websockets:
            self._lbl_ws.config(
                text=f"ws://localhost:{self.bridge.cfg.port_ws}")

    def _on_stop(self):
        self.bridge.stop()
        self._btn_start.config(state=tk.NORMAL)
        self._btn_stop.config(state=tk.DISABLED)
        self._btn_rc.config(state=tk.DISABLED)
        self._lbl_ws.config(text="")
        self._lbl_tags.config(text="—", fg=TEXT)

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
            )
        except ValueError:
            pass

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
        elif kind == "log":
            self._append_log(msg[1])
        elif kind == "values":
            self._update_values(msg[1])

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
        self._lbl_vcnt.config(text=f"Тегов: {n}" if n else "Тегов: —")

    def _clear_values(self):
        self._tv.delete(*self._tv.get_children())
        self._lbl_vcnt.config(text="Тегов: —")

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


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    if not LIBPLCTAG_OK:
        import tkinter.messagebox as mb
        tk.Tk().withdraw()
        mb.showerror("Нет libplctag",
                     "Положите plctag.dll в папку Bridge/libplctag_2.6.16_windows_x64/\n\n"
                     "Установка зависимостей:\n  pip install websockets aiohttp aiohttp-cors")
        sys.exit(1)

    app = App()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()
