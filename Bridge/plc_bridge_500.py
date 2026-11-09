"""
PLC Logic Tracer 500 — Live Bridge
====================================
Reads live tag values from controllers via libplctag (EtherNet/IP):
  • SLC 5/05, MicroLogix 1100/1200/1400/1500  — SLC addressing (N7:0, B3/5, …)
  • ControlLogix / CompactLogix (Logix 5000)  — named tags (MotorRun, T1.ACC, …)
  • RSLinx Classic Gateway (для DH+ / DH-485 routing)

Usage:
    python plc_bridge_500.py                          # интерактивная настройка
    python plc_bridge_500.py --ip 192.168.1.10        # SLC / MicroLogix
    python plc_bridge_500.py --ip 192.168.1.10 --type logix  # ControlLogix
    python plc_bridge_500.py --rslinx --ip 192.168.1.10
    python plc_bridge_500.py --config config500.json
    python plc_bridge_500.py --plc Line1 --plc Line2    # several PLCs at once
    python plc_bridge_500.py --all-plc                  # every saved device

Several controllers:
    Each PLC session gets its own WebSocket port (8765, 8767, 8768 …), printed
    at start-up. Point one browser tab at each URL — the tabs then watch
    different controllers at the same time.

Requirements:
    pip install websockets aiohttp aiohttp-cors
    plctag.dll in Bridge/libplctag_2.6.16_windows_x64/
"""

import asyncio, atexit, collections, json, logging, argparse, os, re, socket, sys, threading, time, zlib
from pathlib import Path

# ── Where files live ─────────────────────────────────────────────────────────
# Running from source, everything sits next to this file. Inside a PyInstaller
# build the two halves split: read-only payload (plctag.dll) is unpacked into a
# private folder (sys._MEIPASS — a temp dir that is deleted on exit for a
# one-file build), while anything the user must find, edit or keep — the device
# presets, the recordings — has to live next to the .exe instead. Writing them
# into _MEIPASS would silently throw away every saved device and recording.
FROZEN = bool(getattr(sys, "frozen", False))

def res_path(*parts) -> Path:
    """Read-only file shipped with the build."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base or Path(__file__).resolve().parent).joinpath(*parts)

def data_path(*parts) -> Path:
    """User-visible file: beside the .exe when frozen, beside the source if not."""
    base = Path(sys.executable).resolve().parent if FROZEN \
           else Path(__file__).resolve().parent
    return base.joinpath(*parts)

# Console output must never kill the bridge. The start-up banner and the log
# lines contain non-ASCII (arrows, Russian), and a Windows console or a
# redirected stdout in the ANSI codepage raises UnicodeEncodeError on them —
# which used to take the whole process down right after the sessions started.
# A windowed build has no console at all: there sys.stdout/stderr are None and
# every logging call would blow up inside the handler, so they get a sink.
if FROZEN:
    for _name in ("stdout", "stderr"):
        if getattr(sys, _name, None) is None:
            try:
                setattr(sys, _name, open(os.devnull, "w", encoding="utf-8"))
            except Exception:
                pass
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── libplctag (SLC 5/05, MicroLogix via PCCC + ControlLogix/CompactLogix via CIP) ─────────────────────
try:
    import ctypes
    # Next to the .exe first: that copy is replaceable without a rebuild, which
    # is the whole point of shipping the driver as a file. The bundled one is
    # the fallback (and the only one that exists when running from source).
    _dll_name = "libplctag_2.6.16_windows_x64"
    _dll_path = next((str(p) for p in (data_path(_dll_name, "plctag.dll"),
                                       res_path(_dll_name, "plctag.dll"))
                      if p.is_file()), str(res_path(_dll_name, "plctag.dll")))
    _dll_dir = Path(_dll_path).parent
    _libplctag = ctypes.cdll.LoadLibrary(_dll_path)

    # --- function signatures ---
    _libplctag.plc_tag_create.argtypes  = [ctypes.c_char_p, ctypes.c_int]
    _libplctag.plc_tag_create.restype   = ctypes.c_int32

    _libplctag.plc_tag_destroy.argtypes = [ctypes.c_int32]
    _libplctag.plc_tag_destroy.restype  = ctypes.c_int

    _libplctag.plc_tag_status.argtypes  = [ctypes.c_int32]
    _libplctag.plc_tag_status.restype   = ctypes.c_int

    _libplctag.plc_tag_read.argtypes    = [ctypes.c_int32, ctypes.c_int]
    _libplctag.plc_tag_read.restype     = ctypes.c_int

    _libplctag.plc_tag_decode_error.argtypes = [ctypes.c_int]
    _libplctag.plc_tag_decode_error.restype  = ctypes.c_char_p

    _libplctag.plc_tag_get_int8.argtypes    = [ctypes.c_int32, ctypes.c_int]
    _libplctag.plc_tag_get_int8.restype     = ctypes.c_int8

    _libplctag.plc_tag_get_uint8.argtypes   = [ctypes.c_int32, ctypes.c_int]
    _libplctag.plc_tag_get_uint8.restype    = ctypes.c_uint8

    _libplctag.plc_tag_get_int16.argtypes   = [ctypes.c_int32, ctypes.c_int]
    _libplctag.plc_tag_get_int16.restype    = ctypes.c_int16

    _libplctag.plc_tag_get_uint16.argtypes  = [ctypes.c_int32, ctypes.c_int]
    _libplctag.plc_tag_get_uint16.restype   = ctypes.c_uint16

    _libplctag.plc_tag_get_int32.argtypes   = [ctypes.c_int32, ctypes.c_int]
    _libplctag.plc_tag_get_int32.restype    = ctypes.c_int32

    _libplctag.plc_tag_get_uint32.argtypes  = [ctypes.c_int32, ctypes.c_int]
    _libplctag.plc_tag_get_uint32.restype   = ctypes.c_uint32

    _libplctag.plc_tag_get_int64.argtypes   = [ctypes.c_int32, ctypes.c_int]
    _libplctag.plc_tag_get_int64.restype    = ctypes.c_int64

    _libplctag.plc_tag_get_float32.argtypes = [ctypes.c_int32, ctypes.c_int]
    _libplctag.plc_tag_get_float32.restype  = ctypes.c_float

    _libplctag.plc_tag_get_float64.argtypes = [ctypes.c_int32, ctypes.c_int]
    _libplctag.plc_tag_get_float64.restype  = ctypes.c_double

    _libplctag.plc_tag_get_bit.argtypes     = [ctypes.c_int32, ctypes.c_int]
    _libplctag.plc_tag_get_bit.restype      = ctypes.c_int

    _libplctag.plc_tag_get_size.argtypes    = [ctypes.c_int32]
    _libplctag.plc_tag_get_size.restype     = ctypes.c_int

    # String accessors (Logix STRING: LEN int32 + DATA[82] bytes)
    try:
        _libplctag.plc_tag_get_string_length.argtypes = [ctypes.c_int32, ctypes.c_int]
        _libplctag.plc_tag_get_string_length.restype  = ctypes.c_int
        _libplctag.plc_tag_get_string.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        _libplctag.plc_tag_get_string.restype  = ctypes.c_int
        _LIBPLCTAG_HAS_STRING = True
    except AttributeError:
        _LIBPLCTAG_HAS_STRING = False

    # Raw byte read for @tags browse and UDT
    try:
        _libplctag.plc_tag_get_raw_bytes.argtypes = [ctypes.c_int32, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
        _libplctag.plc_tag_get_raw_bytes.restype  = ctypes.c_int
        _LIBPLCTAG_HAS_RAW = True
    except AttributeError:
        _LIBPLCTAG_HAS_RAW = False

    # Runtime attribute update — retargets read_cache_ms on live handles
    # when the poll interval changes (no handle re-creation needed).
    try:
        _libplctag.plc_tag_set_int_attribute.argtypes = [ctypes.c_int32, ctypes.c_char_p, ctypes.c_int]
        _libplctag.plc_tag_set_int_attribute.restype  = ctypes.c_int
        _LIBPLCTAG_HAS_SET_ATTR = True
    except AttributeError:
        _LIBPLCTAG_HAS_SET_ATTR = False

    # Cancel a request whose reply is not coming. Without it a lost reply kept
    # its tag "in flight" for the life of the connection.
    try:
        _libplctag.plc_tag_abort.argtypes = [ctypes.c_int32]
        _libplctag.plc_tag_abort.restype  = ctypes.c_int
        _LIBPLCTAG_HAS_ABORT = True
    except AttributeError:
        _LIBPLCTAG_HAS_ABORT = False

    # Library teardown — see _libplctag_teardown for why the bridge calls it.
    try:
        _libplctag.plc_tag_shutdown.argtypes = []
        _libplctag.plc_tag_shutdown.restype  = None
        _LIBPLCTAG_HAS_SHUTDOWN = True
    except AttributeError:
        _LIBPLCTAG_HAS_SHUTDOWN = False

    LIBPLCTAG_OK = True
    PLCTAG_STATUS_OK = 0
    PLCTAG_STATUS_PENDING = 1
    PLCTAG_ERR_TIMEOUT = -32  # common libplctag timeout code
    PLCTAG_ERR_BUSY = -39     # a read from an earlier cycle is still in flight
except Exception as _e:
    _libplctag = None
    LIBPLCTAG_OK = False
    PLCTAG_STATUS_OK = 0
    PLCTAG_STATUS_PENDING = 1
    PLCTAG_ERR_TIMEOUT = -32
    PLCTAG_ERR_BUSY = -39
    _LIBPLCTAG_HAS_STRING = False
    _LIBPLCTAG_HAS_RAW = False
    _LIBPLCTAG_HAS_SET_ATTR = False
    _LIBPLCTAG_HAS_ABORT = False
    _LIBPLCTAG_HAS_SHUTDOWN = False
    print(f"[WARN] libplctag not loaded: {_e}")


def _libplctag_teardown():
    """Shut libplctag down while its own threads are still running.

    plctag.dll runs the same teardown by itself from its exit handler, but that
    one fires while Windows unloads the DLL — after every other thread of the
    process, the library's I/O threads included, is already gone — and it waits
    for those threads to close their sessions. With a single tag still open the
    wait never ends: the window is gone, the .exe stays in the task list, and
    not even taskkill /F can end it (Windows already counts it as exiting).

    Run from atexit — after Python has joined the executor threads, so no read
    can open a new tag behind it — and from the console handler below. The
    DLL's own call then finds nothing left to do.
    """
    if _LIBPLCTAG_HAS_SHUTDOWN:
        try:
            _libplctag.plc_tag_shutdown()
        except Exception:
            pass

atexit.register(_libplctag_teardown)

# Closing the console window (the CLI's X button), logoff and shutdown never
# reach the interpreter's exit: Windows ends the process straight from a console
# control handler, so atexit does not run and the DLL's teardown hung until
# Windows killed the process 5 s later. Same teardown, run first; returning 0
# leaves the ending of the process to the default handler.
# Ctrl+Break takes that same road: Python has no handler for it, so the default
# one ends the process at once — with tags open that left a process no user
# could kill (its last thread is the system's handler thread), holding the
# WebSocket and REST ports until a reboot. Ctrl+C is not in the list: Python
# turns it into KeyboardInterrupt and the bridge stops its sessions itself.
if LIBPLCTAG_OK and os.name == "nt":
    _CTRL_END_EVENTS = (1, 2, 5, 6)     # CTRL_BREAK, CTRL_CLOSE, CTRL_LOGOFF, CTRL_SHUTDOWN

    @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
    def _console_ctrl(event):
        if event in _CTRL_END_EVENTS:
            _libplctag_teardown()
        return 0

    try:
        ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_ctrl, True)
    except Exception:
        pass

# Transient read conditions: the handle is still perfectly good, only this
# cycle's read did not finish in time. Destroying the handle here used to start
# a rebuild storm — every timed-out tag was re-created on the next cycle, which
# made that cycle slower still, so the timeouts fed themselves until the user
# shrank the scene. Keep the handle: the in-flight read lands a few ms later.
_SOFT_READ_ERRS = frozenset({PLCTAG_ERR_TIMEOUT, PLCTAG_ERR_BUSY})

# Read errors that are the CONTROLLER's answer: the request crossed the link and
# came back refused — bad address, a block longer than it will serve, a reply of
# the wrong size. Only these may shrink an SLC block (see _slc_shrink): a dropped
# connection, a socket error or a cancelled request says nothing about what the
# PLC accepts. Numbers as libplctag.h has them, checked against
# plc_tag_decode_error of the bundled 2.6.16 DLL.
_REFUSAL_ERRS = frozenset({
    -4,    # PLCTAG_ERR_BAD_DATA
    -8,    # PLCTAG_ERR_BAD_REPLY
    -9,    # PLCTAG_ERR_BAD_STATUS
    -18,   # PLCTAG_ERR_NOT_ALLOWED
    -19,   # PLCTAG_ERR_NOT_FOUND
    -21,   # PLCTAG_ERR_NO_DATA
    -27,   # PLCTAG_ERR_OUT_OF_BOUNDS
    -29,   # PLCTAG_ERR_REMOTE_ERR
    -33,   # PLCTAG_ERR_TOO_LARGE
    -34,   # PLCTAG_ERR_TOO_SMALL
    -35,   # PLCTAG_ERR_UNSUPPORTED
})

try:
    import aiohttp
    from aiohttp import web
    import aiohttp_cors
    AIOHTTP_OK = True
except ImportError:
    AIOHTTP_OK = False

try:
    import websockets
    from websockets.exceptions import ConnectionClosed as _WsClosed
    WS_OK = True
    _WS_CLOSED = (_WsClosed,)
except ImportError:
    WS_OK = False
    _WS_CLOSED = ()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
LOG_ROOT = "bridge500"
log = logging.getLogger(LOG_ROOT)


def session_logger(slug: str) -> logging.Logger:
    """Logger for one PLC session.

    With several controllers polled at the same time an unlabelled log is
    unreadable, so every session logs through a child of the root logger. The
    handler that feeds the GUI turns that name back into a session tag.
    """
    return logging.getLogger(f"{LOG_ROOT}.{slug}") if slug else log

# ── Save-path debug switch (toggled from the UI) ──────────────────────────────
# When True, the bridge emits detailed traces around the "save online project"
# round-trip so we can diagnose stale values, missing tags AND request behaviour:
#   • watch_only — payload / tagTypes / added-removed deltas
#   • read_now   — requested vs returned, program-scope, missing, errors
#   • _read_sync — request de-duplication and the word-read optimisation:
#       requested → unique physical reads, bits grouped into words, handle
#       cache reuse (proves no duplicate CIP/PCCC reads are issued) and the
#       wall-clock time of the physical batch read.
DEBUG_SAVE = False

def set_debug_save(enabled: bool) -> None:
    global DEBUG_SAVE
    DEBUG_SAVE = bool(enabled)
    log.info(f"DEBUG_SAVE {'ON' if DEBUG_SAVE else 'OFF'}")

# ── In-memory log buffer (for UI) ─────────────────────────────────────────────
class LogBuffer:
    def __init__(self, capacity=500):
        self._buf = collections.deque(maxlen=capacity)
        self._seq = 0

    def add(self, level: str, msg: str, src: str = ""):
        # `src` is the PLC session the line came from ("" = the bridge itself).
        self._seq += 1
        self._buf.append({"seq": self._seq, "ts": time.time(), "level": level,
                          "msg": msg, "src": src})

    def since(self, seq: int) -> list:
        return [r for r in self._buf if r["seq"] > seq]

class MemoryLogHandler(logging.Handler):
    def __init__(self, buf: LogBuffer):
        super().__init__()
        self.buf = buf
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        try:
            name = record.name or ""
            src = (name[len(LOG_ROOT) + 1:]
                   if name.startswith(LOG_ROOT + ".") else "")
            self.buf.add(record.levelname, self.format(record), src)
        except Exception:
            pass

DEFAULT_WS   = 8765
DEFAULT_HTTP = 8766
POLL_IV      = 0.2
POLL_IV_MIN  = 0.01   # the shortest pause between poll cycles any setter accepts
MAX_BATCH    = 40
RECONNECT    = 5


def poll_interval_from(value, default: float = POLL_IV) -> float:
    """Seconds between poll cycles, from whatever sets them: the GUI field, a
    saved preset, the CLI, set_interval over WebSocket, REST. Missing or blank
    gives `default`; anything under POLL_IV_MIN is raised to it. A decimal comma
    is taken as typed ("0,05"); text that is not a number raises ValueError, as
    float() does.

    On Windows the pause comes out about 10 ms longer than asked — the event
    loop wakes on the system timer tick: 0.01 s measured 0.020 s, 0.2 s 0.211 s.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return default
    v = float(value.replace(",", ".") if isinstance(value, str) else value)
    if v != v or v in (float("inf"), float("-inf")):
        raise ValueError(f"poll interval {value!r} is not a number of seconds")
    return max(POLL_IV_MIN, v)

# SLC 500 data file prefixes that are physical I/O
PHYSICAL_PREFIXES = ('I:', 'O:', 'I/', 'O/')

def is_physical(addr: str) -> bool:
    return addr.upper().startswith(PHYSICAL_PREFIXES)

# ─────────────────────────────────────────────────────────────────────────────
class Config:
    def __init__(self):
        self.ip            = "192.168.1.1"
        self.slot          = 0         # for SLC in chassis; MicroLogix = 0
        self.via_rslinx    = False
        self.processor_type= "SLC"     # "SLC" | "MicroLogix" | "Logix5000"
        self.controller_type = "slc"   # "slc" | "logix"  — drives test-read logic
        self.poll_interval = POLL_IV
        # How many parallel CIP connections the watch list is spread over.
        # libplctag serialises every request that shares a session: one socket,
        # one request in flight, so a 500-tag watch list is ~50 round-trips
        # end to end. Tags carrying different connection_group_id land on
        # separate sessions (own socket + own I/O thread) and their round-trips
        # overlap. 0 = auto (see _effective_groups).
        self.conn_groups   = 0
        # SLC/PCCC: read contiguous runs of a data file in one request instead
        # of one request per address. Off only for diagnosing a controller that
        # mishandles multi-element reads.
        self.slc_blocks    = True
        # Wasted bytes worth carrying inside an SLC block to save one request.
        # Low for a serial-gateway link (bytes are the cost), high for a direct
        # Ethernet connection (requests are the cost). See _SLC_GAP_BYTES.
        self.slc_gap_bytes = _SLC_GAP_BYTES
        # SLC/PCCC: read the I/O image in block requests too (one per I and O
        # file instead of one per I/O word). Needs the slot layout, which the
        # tracer sends from its .SLC project with every watch list. Opt-in per
        # controller — GUI checkbox, "io_block": true in the device preset.
        self.io_block      = False
        self.watched_tags  = []
        self.slc_path      = ""        # loaded .SLC / .APS file for tag list
        self.port_ws       = DEFAULT_WS
        self.port_http     = DEFAULT_HTTP
        self.records_dir   = str(data_path("RECORDS"))

    def build_ip_path(self):
        return self.ip   # libplctag builds gateway= from plain IP

    # No path given → beside the .exe (or the source). A bare "config500.json"
    # used to resolve against the working directory, which for a double-clicked
    # shortcut is wherever the shortcut points.
    def save(self, path=None):
        path = path or data_path("config500.json")
        with open(path,"w") as f: json.dump(self.__dict__,f,indent=2)
        log.info(f"Config saved → {path}")

    def load(self, path=None):
        path = path or data_path("config500.json")
        with open(path) as f: self.__dict__.update(json.load(f))
        self.poll_interval = poll_interval_from(self.poll_interval)


# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Saved devices — named connection presets shared by the GUI and the CLI
# ─────────────────────────────────────────────────────────────────────────────
DEV_FILE = data_path("plc_bridge_devices.json")


class DeviceStore:
    """Named connection presets ("devices"), persisted next to the script.

    File: {"version":1, "last":<name>, "devices":[{name, ip, slot, type,
    interval, groups, port, rslinx[, io_block]}, ...]}.  A missing or broken
    file degrades to an empty list — the panel must stay usable without it, this
    is a convenience feature and never a precondition for connecting.

    Optional settings are written only when switched on: a preset without the
    key reads as off, so files saved before a setting existed load unchanged.

    It lives here rather than in the GUI because the headless bridge starts the
    same presets:  python plc_bridge_500.py --plc Line1 --plc Line2
    """

    def __init__(self, path: Path = None):
        self.path    = Path(path) if path else DEV_FILE
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
            log.warning(f"Device list not loaded ({self.path.name}): {e}")
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
            log.error(f"Device list not saved ({self.path}): {e}")
            try:
                tmp.unlink()
            except OSError:
                pass
            return False

    # ── contents ──────────────────────────────────────────────────────────────
    @staticmethod
    def _norm(d: dict) -> dict:
        dev = {
            "name":     str(d.get("name", "")).strip(),
            "ip":       str(d.get("ip", "")).strip(),
            "slot":     int(d.get("slot", 0) or 0),
            "type":     "logix" if str(d.get("type", "slc")).lower() == "logix" else "slc",
            "interval": poll_interval_from(d.get("interval")),
            # Parallel CIP connections; 0 = auto. Presets written before this
            # setting existed simply come back as auto.
            "groups":   max(0, min(16, int(d.get("groups", 0) or 0))),
            # WebSocket port this controller is served on. 0 = take the first
            # free one at start; a fixed port is what a browser tab bookmarks,
            # which is the whole point of running several PLCs at once.
            "port":     max(0, min(65535, int(d.get("port", 0) or 0))),
            "rslinx":   bool(d.get("rslinx", False)),
        }
        # I/O image in block reads (SLC). Present only when on — no key, no mode.
        if bool(d.get("io_block", False)):
            dev["io_block"] = True
        return dev

    @staticmethod
    def label(d: dict) -> str:
        port = f"  ·  ws:{d['port']}" if d.get("port") else ""
        return f"{d['name']}  ·  {d['ip']}:{d['slot']}  ·  {d['type']}{port}"

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


# Cache for saved_device_for() — the device file is re-read only when it
# changes on disk, because status() runs once per GUI tick per session.
_DEV_CACHE = {"mtime": None, "store": None}


def saved_device_for(cfg: "Config", name: str):
    """The preset in plc_bridge_devices.json a running session came from.

    Matched by session name first (that is what --plc / the GUI pass), then by
    ip+slot+type for ad-hoc sessions started with --ip that happen to describe a
    saved controller. Returns None for a session that is not in the file — the
    UI then shows its port number instead of a name.
    """
    try:
        mtime = DEV_FILE.stat().st_mtime
    except OSError:
        mtime = None
    if _DEV_CACHE["store"] is None or _DEV_CACHE["mtime"] != mtime:
        _DEV_CACHE["store"] = DeviceStore()
        _DEV_CACHE["mtime"] = mtime
    store = _DEV_CACHE["store"]
    dev = store.find(name)
    if dev:
        return dev
    return next((d for d in store.devices
                 if d["ip"] == cfg.ip
                 and int(d["slot"]) == int(cfg.slot)
                 and d["type"] == cfg.controller_type), None)


def device_to_config(dev: dict, base: "Config" = None) -> "Config":
    """Turn a saved preset into the Config one PlcSession runs on."""
    cfg = base or Config()
    cfg.ip              = dev.get("ip", cfg.ip)
    cfg.slot            = int(dev.get("slot", cfg.slot) or 0)
    cfg.controller_type = dev.get("type", cfg.controller_type)
    cfg.processor_type  = "Logix5000" if cfg.controller_type == "logix" else "SLC"
    cfg.poll_interval   = poll_interval_from(dev.get("interval"), cfg.poll_interval)
    cfg.conn_groups     = int(dev.get("groups", cfg.conn_groups) or 0)
    cfg.via_rslinx      = bool(dev.get("rslinx", False))
    cfg.io_block        = bool(dev.get("io_block", False))
    # 0 means "auto" here — SessionManager picks the first free port.
    cfg.port_ws         = int(dev.get("port", 0) or 0)
    if dev.get("records_dir"):
        cfg.records_dir = dev["records_dir"]
    return cfg


# Recorder — one per PLC session
#
# This used to be a module-level singleton, which was right while the bridge
# served exactly one controller. With several sessions running side by side it
# destroys recordings: a browser tab pressing REC on PLC-B would silently close
# PLC-A's session, because rec_start() always stopped "the" active one. Each
# session now owns a Recorder; the module-level functions below still work and
# operate on a default instance.


def _slug(text: str) -> str:
    """Session name reduced to something safe for file and logger names."""
    out = re.sub(r"[^0-9A-Za-z_.\-]+", "_", str(text or "")).strip("_.")
    return out[:32]


class Recorder:
    """Chunked .ndrec writer for one PLC session."""

    def __init__(self, label: str = "", logger: logging.Logger | None = None):
        # The label goes into the file name, so two controllers recorded in the
        # same second cannot end up writing the same .ndrec.
        self.label = _slug(label)
        self.log = logger or log
        # A dict, mutated in place and never rebound, so the module-level `_rec`
        # alias below stays a live view of the default recorder's state.
        self.st: dict = {
            'active':    False,
            'tmp_path':  None,   # Path to _tmp file (header + all chunks so far)
            'main_path': None,   # Path to final .ndrec (mirror of tmp, rebuilt each chunk)
            'header':    {},     # current header dict (tagIndex/graphSnap updated on each chunk)
            'owner':     None,   # websocket that started it, so a dropped client closes it
        }

    @property
    def active(self) -> bool:
        return bool(self.st['active'])

    @property
    def file_name(self) -> str:
        p = self.st['main_path']
        return p.name if p else ""

    def start(self, stamp: str, graph_snap: dict, tag_index: list,
              records_dir, owner=None):
        self.stop()  # stop any previous session of THIS recorder
        records_dir = Path(records_dir)
        records_dir.mkdir(parents=True, exist_ok=True)
        tail = f"_{self.label}" if self.label else ""
        st = self.st
        st['tmp_path']  = records_dir / f"plc_rec_{stamp}{tail}_tmp.ndrec"
        st['main_path'] = records_dir / f"plc_rec_{stamp}{tail}.ndrec"
        st['header'] = {
            "type": "header", "version": 2,
            "createdAt": time.time(),
            "tagIndex":  tag_index,
            "graphSnap": graph_snap,
        }
        st['tmp_path'].write_text(
            json.dumps(st['header'], ensure_ascii=False) + "\n", encoding="utf-8"
        )
        st['active'] = True
        st['owner']  = owner
        self.log.info(f"[REC] started → {st['main_path'].name}")

    def chunk(self, tag_index: list, frames: list, graph_snap: dict | None = None):
        st = self.st
        if not st['active']:
            return
        # 1. Update tagIndex in header
        st['header']['tagIndex'] = tag_index
        # The tracer re-snapshots the scene when it captures its FIRST frame: REC
        # is normally pressed before a tag is picked, so the snapshot taken at
        # start() describes an empty canvas. Take the later one when it arrives.
        if graph_snap:
            st['header']['graphSnap'] = graph_snap
        # 2. Read existing tmp lines (header + previous chunks)
        existing = st['tmp_path'].read_text(encoding="utf-8").splitlines(keepends=True)
        # 3. Replace header line and append new chunk
        lines = [json.dumps(st['header'], ensure_ascii=False) + "\n"]
        lines += existing[1:]  # previous chunks (skip old header)
        if frames:
            lines.append(json.dumps({"type": "chunk", "frames": frames},
                                    ensure_ascii=False) + "\n")
        content = "".join(lines)
        # 4. Atomic write: staging file -> rename to main, then refresh tmp
        staging = st['main_path'].with_suffix('.ndrec._writing')
        staging.write_text(content, encoding="utf-8")
        os.replace(str(staging), str(st['main_path']))  # atomic rename
        st['tmp_path'].write_text(content, encoding="utf-8")
        self.log.info(f"[REC] chunk flushed ({len(frames)} frames) → {st['main_path'].name}")

    def stop(self):
        st = self.st
        if not st['active']:
            return
        # Remove the tmp scratch file; the main file is already up to date
        if st['tmp_path'] and st['tmp_path'].exists():
            st['tmp_path'].unlink()
        self.log.info(f"[REC] stopped → {st['main_path'].name}")
        st['active']    = False
        st['tmp_path']  = None
        st['main_path'] = None
        st['header']    = {}
        st['owner']     = None

    def stop_if_owner(self, websocket):
        """Close the session whose client just disappeared.

        A browser that closes mid-recording never sends rec_stop, which used to
        leave the session 'active' with its _tmp scratch file on disk forever.
        The .ndrec itself is already complete — every chunk rewrites it — so
        this only has to release the session.
        """
        if self.st['active'] and self.st['owner'] is websocket:
            self.log.info("[REC] client disconnected — closing the recording session")
            self.stop()


# Default recorder used by the module-level API below. `_rec` stays a live view
# of its state so existing callers keep reading the dict they always did.
_rec_default = Recorder()
_rec = _rec_default.st


def rec_start(stamp: str, graph_snap: dict, tag_index: list, records_dir: Path,
              owner=None):
    _rec_default.start(stamp, graph_snap, tag_index, records_dir, owner)


def rec_chunk(tag_index: list, frames: list, graph_snap: dict | None = None):
    _rec_default.chunk(tag_index, frames, graph_snap)


def rec_stop():
    _rec_default.stop()


def rec_stop_if_owner(websocket):
    _rec_default.stop_if_owner(websocket)


# ─────────────────────────────────────────────────────────────────────────────
# libplctag helpers: SLC address → connection string + value extraction
# ─────────────────────────────────────────────────────────────────────────────
import re as _re

# Timer/Counter/Control word layout (byte offsets in a 6-byte element, elem_size=6)
# Word 0 (bytes 0-1): control/status bits
# Word 1 (bytes 2-3): PRE (timer/counter preset)   | LEN (control length)
# Word 2 (bytes 4-5): ACC (timer/counter accum)    | POS (control position)
# The keys are shared across T/C/R because the paired members sit in the same
# word in all three element types.
_TC_BYTE_OFF = {'PRE': 2, 'LEN': 2, 'ACC': 4, 'POS': 4}

# Timer/Counter/Control status bits in word 0 (bit positions)
_T_BITS = {'EN': 15, 'TT': 14, 'DN': 13}
_C_BITS = {'CU': 15, 'CD': 14, 'DN': 13, 'OV': 12, 'UN': 11}
# CONTROL (R file) — used by FFL/FFU/LFL/LFU, BSL/BSR, SQO/SQC/SQL.
# Same order as the Logix 32-bit layout in _LGX_STRUCT_BITS['CONTROL'],
# shifted into the top half of a 16-bit word.
_R_BITS = {'EN': 15, 'EU': 14, 'DN': 13, 'EM': 12, 'ER': 11, 'UL': 10, 'IN': 9, 'FD': 8}

# Per-prefix status-bit maps for the 6-byte element types
_STRUCT_BITS = {'T': _T_BITS, 'C': _C_BITS, 'R': _R_BITS}

# Slash-notation aliases for timer/counter/control status bits
_TC_SLASH_BITS = {**_T_BITS, **_C_BITS, **_R_BITS}

# Updated regex:
#   group 1: letter prefix (one or more letters, e.g. T, C, N, B, F, I, O, S)
#   group 2: optional file number digits
#   group 3: element number after colon
#   group 4: optional /bit-number  OR  /WORD (e.g. /DN /EN /TT /CU /CD /ACC /PRE)
#   group 5: optional .sub-field   (e.g. .ACC .PRE .DN .EN)
_SLC_FILE_RE = _re.compile(
    r'^([A-Za-z]+?)(\d+)?:(\d+)'       # prefix (greedy-minimal letters) + optional file_num + : + element
    r'(?:/(\w+))?'                      # optional /bit-or-name  (e.g. /5  /DN  /EN  /ACC)
    r'(?:\.(\w+))?$',                   # optional .sub-field    (e.g. .ACC  .PRE  .DN)
    _re.IGNORECASE
)

# SLC I/O module-specific form: I:<slot>.<word>/<bit>  or  O:<slot>.<word>/<bit>
# Examples: I:1.10/0 → slot=1 word=10 bit=0 ; O:2.0/5 → slot=2 word=0 bit=5
# Also supports bit-only form:  I:1/3 → slot=1 word=0 bit=3  (handled via _SLC_FILE_RE)
_SLC_IO_RE = _re.compile(
    r'^([IO]):(\d+)\.(\d+)/(\d+)$',
    _re.IGNORECASE
)

# SLC I/O word-only form (no /bit): I:<slot>.<word> or O:<slot>.<word>
# Used by SCL/IIM/IOM when the whole 16-bit word is the operand.
# Example: I:26.0 → slot=26 word=0 → read uint16 of I1:26.0
_SLC_IO_WORD_RE = _re.compile(
    r'^([IO]):(\d+)\.(\d+)$',
    _re.IGNORECASE
)

# Default file numbers for single-letter prefixes that allow omitting the number
# SLC 500 fixed files: I = file 1, O = file 0, S = file 2, B = file 3 (typical)
_SLC_DEFAULT_FILE = {'I': 1, 'O': 0, 'S': 2, 'B': 3}


def _slc_parse_addr(name: str):
    """Parse SLC address into components.

    Returns (prefix, file_num, element, bit_or_none, sub_or_none) or None.

    For SLC I/O modules addressed as I:<slot>.<word>/<bit> the `element`
    in libplctag terms is the slot, and `sub` carries the word offset as
    the string "W<n>" so downstream code can compute a per-word read.
    Example: I:1.10/0 → prefix=I, file_num=1, element=1, bit=0, sub='W10'

    Supported formats:
      T4:0          → prefix=T, file_num=4, element=0, bit=None, sub=None
      T4:0.ACC      → prefix=T, file_num=4, element=0, bit=None, sub='ACC'
      T4:0.DN       → prefix=T, file_num=4, element=0, bit=None, sub='DN'
      T4:0/DN       → prefix=T, file_num=4, element=0, bit=None, sub='DN'  (slash alias)
      T4:0/13       → prefix=T, file_num=4, element=0, bit=13,   sub=None
      N7:0          → prefix=N, file_num=7, element=0, bit=None, sub=None
      B3:0/5        → prefix=B, file_num=3, element=0, bit=5,    sub=None
      I:0/3         → prefix=I, file_num=1, element=0, bit=3,    sub=None
      I:1.10/0      → prefix=I, file_num=1, element=1, bit=0,    sub='W10'
      O:2.0/5       → prefix=O, file_num=0, element=2, bit=5,    sub='W0'
      C5:0.PRE      → prefix=C, file_num=5, element=0, bit=None, sub='PRE'
    """
    stripped = name.strip()

    # SLC I/O module form I:<slot>.<word>/<bit> — handle BEFORE generic parser
    mio = _SLC_IO_RE.match(stripped)
    if mio:
        prefix = mio.group(1).upper()
        slot   = int(mio.group(2))
        word   = int(mio.group(3))
        bit    = int(mio.group(4))
        file_num = _SLC_DEFAULT_FILE[prefix]  # I→1, O→0
        return prefix, file_num, slot, bit, f'W{word}'

    # SLC I/O word-only form I:<slot>.<word> — whole 16-bit word (no bit).
    # Must match BEFORE generic parser, otherwise `.word` is misread as a sub-field.
    miow = _SLC_IO_WORD_RE.match(stripped)
    if miow:
        prefix = miow.group(1).upper()
        slot   = int(miow.group(2))
        word   = int(miow.group(3))
        file_num = _SLC_DEFAULT_FILE[prefix]
        return prefix, file_num, slot, None, f'W{word}'

    m = _SLC_FILE_RE.match(stripped)
    if not m:
        return None

    prefix = m.group(1).upper()
    # File number: explicit digits, or lookup default for known prefixes
    if m.group(2) is not None:
        file_num = int(m.group(2))
    else:
        # Single-letter prefixes with known defaults
        file_num = _SLC_DEFAULT_FILE.get(prefix)
        if file_num is None:
            return None  # multi-letter prefix always needs a file number

    element = int(m.group(3))

    # /xxx: numeric bit index OR named status word (DN, EN, TT, CU, ACC, PRE …)
    slash_field = m.group(4).upper() if m.group(4) else None
    dot_field   = m.group(5).upper() if m.group(5) else None

    # Resolve slash field → bit (int) or sub (string)
    bit = None
    sub = dot_field  # default: use .sub if present

    if slash_field is not None:
        if slash_field.isdigit():
            # /5  → numeric bit
            bit = int(slash_field)
        else:
            # /DN /EN /TT /CU /ACC /PRE → treat same as .sub
            sub = slash_field

    return prefix, file_num, element, bit, sub


def _slc_tag_path(ip: str, slot: int, name: str) -> str | None:
    """Build libplctag connection string for an SLC 5/05 / MicroLogix tag.

    Key insight for Timer/Counter/Control (T, C, R files):
      libplctag SLC driver addresses T/C/R by ELEMENT number, not word offset.
      Each element is 3 words (6 bytes).  We read the whole element
      (elem_size=6, elem_count=1) and extract sub-fields in _slc_read_value.
      R (CONTROL) is laid out identically: status word, LEN, POS.

    Returns None if the address can't be parsed.
    """
    parsed = _slc_parse_addr(name)
    if not parsed:
        return None
    prefix, file_num, element, bit, sub = parsed

    if prefix in ('T', 'C', 'R'):
        # Always read the full 6-byte (3-word) timer/counter/control element.
        # Sub-field extraction happens in _slc_read_value via byte offsets.
        return (f"protocol=ab-eip&gateway={ip}&cpu=SLC"
                f"&elem_size=6&elem_count=1"
                f"&name={prefix}{file_num}:{element}")

    # SLC I/O modules addressed per-word: I1:<slot>.<word> or O0:<slot>.<word>
    # libplctag forbids /bit on I/O (see pccc.c parse_pccc_bit_num), so we
    # read the 16-bit word and extract the bit ourselves in _slc_read_value.
    # Fallback: bit-only form like I:26/0 (no explicit word) → assume word 0.
    if prefix in ('I', 'O'):
        if isinstance(sub, str) and sub.startswith('W'):
            word = int(sub[1:])
        elif bit is not None:
            word = 0
        else:
            word = None
        if word is not None:
            return (f"protocol=ab-eip&gateway={ip}&cpu=SLC"
                    f"&elem_size=2&elem_count=1"
                    f"&name={prefix}{file_num}:{element}.{word}")

    elif prefix == 'F':
        elem_size = 4
    else:
        # N, B, I, O, S, A — all 16-bit words (R is handled above as a 6-byte
        # CONTROL element, alongside T and C)
        elem_size = 2

    return (f"protocol=ab-eip&gateway={ip}&cpu=SLC"
            f"&elem_size={elem_size}&elem_count=1"
            f"&name={prefix}{file_num}:{element}")


# ─────────────────────────────────────────────────────────────────────────────
# Logix (ControlLogix / CompactLogix) path + value helpers
# ─────────────────────────────────────────────────────────────────────────────

# Mapping from L5X / common dataType strings → libplctag elem_size + reader key.
# "reader" is a short token used by _logix_read_value to pick the right accessor.
_LGX_TYPE_MAP = {
    'BOOL':   (1, 'bool'),
    'SINT':   (1, 'sint'),
    'USINT':  (1, 'usint'),
    'BYTE':   (1, 'usint'),
    'INT':    (2, 'int'),
    'UINT':   (2, 'uint'),
    'WORD':   (2, 'uint'),
    'DINT':   (4, 'dint'),
    'UDINT':  (4, 'udint'),
    'DWORD':  (4, 'udint'),
    'REAL':   (4, 'real'),
    'LINT':   (8, 'lint'),
    'ULINT':  (8, 'lint'),
    'LREAL':  (8, 'lreal'),
    'STRING': (88, 'string'),
}

# Timer/Counter member suffixes → type hint
_LGX_TIMER_MEMBERS = {
    'ACC': 'DINT', 'PRE': 'DINT',
    'EN': 'BOOL', 'TT': 'BOOL', 'DN': 'BOOL',
}
_LGX_COUNTER_MEMBERS = {
    'ACC': 'DINT', 'PRE': 'DINT',
    'CU': 'BOOL', 'CD': 'BOOL', 'DN': 'BOOL', 'OV': 'BOOL', 'UN': 'BOOL',
}

# ── Atomic TIMER / COUNTER / CONTROL structure layout (Logix) ────────────────
# A Logix TIMER/COUNTER/CONTROL is a 12-byte predefined structure:
#   bytes 0-3  control DINT  (status bits live in the high bits)
#   bytes 4-7  PRE / LEN     (DINT)
#   bytes 8-11 ACC / POS     (DINT)
# Reading the whole 12-byte element ONCE and extracting every member from that
# single snapshot guarantees the members are mutually consistent (.DN can never
# disagree with .ACC), which a per-member read cannot promise while the timer is
# actively running on the PLC.
_LGX_STRUCT_SIZE = 12
# DINT word members → byte offset within the element
_LGX_STRUCT_DINT_OFF = {'PRE': 4, 'ACC': 8, 'LEN': 4, 'POS': 8}
# Status bit members → bit index within the control DINT (offset 0).
# Bit positions differ per structure type, so they are keyed by type.
_LGX_STRUCT_BITS = {
    'TIMER':   {'EN': 31, 'TT': 30, 'DN': 29},
    'COUNTER': {'CU': 31, 'CD': 30, 'DN': 29, 'OV': 28, 'UN': 27},
    'CONTROL': {'EN': 31, 'EU': 30, 'DN': 29, 'EM': 28, 'ER': 27,
                'UL': 26, 'IN': 25, 'FD': 24},
}
# Internal sentinel appended to a base tag name to key the shared 12-byte
# structure handle (kept distinct from any real tag's per-scalar handle).
_LGX_STRUCT_SENTINEL = "\x00STRUCT"

# Bit-selector regex: TagName.17  or  Path.To.Tag.31
_LGX_BIT_SEL_RE = _re.compile(r'^(?P<base>.+)\.(?P<bit>\d{1,2})$')

# Local:N:I/O module struct field → (elem_size, reader).
# Used for WHOLE-WORD reads of module I/O fields (e.g. Save Online Project).
# Per-bit reads (Local:1:I.Data.14) are handled earlier by the bit-selector.
_LOCAL_IO_FIELD_TYPES = {
    'DATA':     (2, 'int'),    # INT  — digital input/output data word
    'READBACK': (2, 'int'),    # INT  — output module read-back word
    'FAULT':    (4, 'dint'),   # DINT — module fault word
}

# ── Accessor ↔ element size, used to re-fit a guessed reader to reality ──────
# Every Logix reader below is a *guess*: it comes from the L5X dataType hint,
# a name heuristic, or the blanket DINT default. When the guess is wider than
# the data the controller actually sent, libplctag's getter reads past the end
# of the tag buffer and returns the type's sentinel — plc_tag_get_int32 gives
# INT32_MIN (-2147483648), plc_tag_get_int16 gives -32768 — which the tracer
# then paints as a permanently TRUE contact. _logix_fit_reader() sizes the
# accessor off the buffer libplctag really filled, so the value is right even
# when no type hint ever arrives (the common case: an L5X Alias tag carries no
# DataType attribute, so `SAF1_BG03_St2 → Local:2:I.Pt07.Status` was read as a
# 4-byte DINT although the controller returns a 1-byte BOOL).
_LGX_READER_WIDTH = {
    'bool': 1, 'sint': 1, 'usint': 1,
    'int':  2, 'uint':  2,
    'dint': 4, 'udint': 4, 'real': 4,
    'lint': 8, 'lreal': 8,
}
# Element size (bytes) → accessor to fall back on.
_LGX_READER_BY_SIZE = {1: 'bool', 2: 'int', 4: 'dint', 8: 'lint'}
# Readers that came from an explicit 1-byte numeric hint — a SINT holding 3 is
# a number, not a bit, so these are never re-read as BOOL.
_LGX_BYTE_NUMERIC = ('sint', 'usint')
# One log line per (tag, correction) — the fit repeats on every poll cycle.
_LGX_FIT_LOGGED: set = set()


def _logix_actual_size(tag_handle: int) -> int:
    """Bytes libplctag actually holds for this tag (0 if unavailable)."""
    try:
        return int(_libplctag.plc_tag_get_size(tag_handle))
    except Exception:
        return 0


def _logix_fit_reader(reader: str, actual: int, meta: dict) -> str:
    """Re-fit `reader` to the element size the controller really returned."""
    if actual <= 0 or reader == 'string':
        return reader
    want = _LGX_READER_WIDTH.get(reader)
    if want is None or want == actual:
        return reader
    if actual not in _LGX_READER_BY_SIZE:
        return reader                      # struct / array payload — leave alone
    if reader in ('real', 'lreal'):
        return reader                      # a float of odd width is not an int
    if actual == 1:
        fitted = reader if reader in _LGX_BYTE_NUMERIC else 'bool'
    else:
        fitted = _LGX_READER_BY_SIZE[actual]
    if fitted != reader:
        key = f"{meta.get('logix_name') or '?'}|{reader}->{fitted}"
        if key not in _LGX_FIT_LOGGED:
            _LGX_FIT_LOGGED.add(key)
            log.info(f"type fit: '{meta.get('logix_name') or '?'}' assumed "
                     f"{reader.upper()} ({want}B) but controller returned "
                     f"{actual}B → reading as {fitted.upper()}")
    return fitted


def _infer_logix_type(name: str, explicit: str | None = None) -> tuple[int, str, int | None]:
    """Pick (elem_size, reader, bit_idx) for a Logix tag.

    Priority:
      1. explicit type from caller (L5X dataType) — but only if address has no
         sub-field or bit selector (`.ACC`, `.EN`, `.17`) that overrides it.
      2. suffix heuristics (.ACC/.PRE → DINT, .EN/.DN/.TT/... → BOOL)
      3. bit-selector (.0..31) → parent DINT + bit extraction
      4. default DINT

    Returns bit_idx = None if tag is not a bit-extract from DINT.
    """
    # Bit-selector "Something.17" → read parent DINT, extract bit
    m = _LGX_BIT_SEL_RE.match(name)
    if m:
        bit_idx = int(m.group('bit'))
        if 0 <= bit_idx <= 31:
            # Ensure the trailing numeric chunk is really a bit, not an array idx.
            # Logix uses `Tag[5]` for arrays — so if `.N` is here, it's a bit.
            return 4, 'dint_bit', bit_idx

    # Suffix heuristics for TIMER / COUNTER / UDT-like paths
    if '.' in name:
        suffix = name.rsplit('.', 1)[1].upper()
        if suffix in _LGX_TIMER_MEMBERS:
            hint = _LGX_TIMER_MEMBERS[suffix]
            sz, rd = _LGX_TYPE_MAP[hint]
            return sz, rd, None
        if suffix in _LGX_COUNTER_MEMBERS:
            hint = _LGX_COUNTER_MEMBERS[suffix]
            sz, rd = _LGX_TYPE_MAP[hint]
            return sz, rd, None

    # Explicit L5X dataType hint
    if explicit:
        base = explicit.strip().upper()
        # Strip Rockwell-specific prefixes: "BOOL[32]" → "BOOL"
        base = _re.sub(r'\[.*$', '', base)
        if base in _LGX_TYPE_MAP:
            sz, rd = _LGX_TYPE_MAP[base]
            return sz, rd, None
        # TIMER / COUNTER bare struct — treat as DINT (48-byte UDT read would need sub-field)
        if base in ('TIMER', 'COUNTER', 'CONTROL'):
            return 4, 'dint', None

    # Local:N:I/O per-point member — Local:2:I.Pt07.Status, Local:1:O.Pt03.Data.
    # Point-level members of a digital module are BOOL (1 byte on the wire) and
    # are the usual AliasFor target of a ladder tag; the alias itself carries no
    # DataType in the L5X, so without this the address falls through to the DINT
    # default. Placed after the explicit hint so a real tagTypes hint still wins.
    if _re.match(r'^Local:\d+:[IO]\.Pt\d+\.\w+$', name, _re.IGNORECASE):
        return 1, 'bool', None

    # Local:N:I/O module fields — size by field, never blind DINT.
    # Covers whole-word reads issued by "Save Online Project":
    #   Local:1:I.Data, Local:2:O.Data, Local:2:I.ReadBack  → INT  (2 bytes)
    #   Local:1:I.Fault, Local:2:I.Fault                    → DINT (4 bytes)
    # Without this, these read as DINT (4 bytes) → wrong int32 → fmtNum emits a
    # 32-bit binary string into the .L5X instead of the correct 16-bit value.
    # Placed after the explicit hint so a future tagTypes hint still wins.
    m_local = _re.match(r'^Local:\d+:[IO]\.(\w+)', name, _re.IGNORECASE)
    if m_local:
        field = m_local.group(1).upper()
        sz, rd = _LOCAL_IO_FIELD_TYPES.get(field, (2, 'int'))
        return sz, rd, None

    # Default: assume DINT (4 bytes) — safest for Logix where most values are 32-bit
    return 4, 'dint', None


def _logix_tag_path(cfg, name: str, elem_size: int, read_cache_ms: int) -> str:
    """Build libplctag connection string for a Logix 5000 named tag.

    For bit-extract addresses like `MyTag.17`, caller strips the `.N` and passes
    the parent tag name so libplctag reads the full DINT.
    """
    path_suffix = f"1,{cfg.slot}"
    parts = [
        "protocol=ab-eip",
        f"gateway={cfg.ip}",
        f"path={path_suffix}",
        "cpu=LGX",
        f"elem_size={elem_size}",
        "elem_count=1",
        "allow_packing=1",
    ]
    if read_cache_ms > 0:
        parts.append(f"read_cache_ms={read_cache_ms}")
    parts.append(f"name={name}")
    return "&".join(parts)


def _with_conn_group(conn_str: str | None, group) -> str | None:
    """Pin a tag to one of several parallel CIP connections.

    libplctag keys its sessions on (gateway, path, connection_group_id), so two
    tags with different ids get two sockets and two I/O threads, and their
    request/response round-trips overlap instead of queueing behind each other.
    `name=` stays last purely for readability of the logged string.
    """
    if not conn_str or group is None:
        return conn_str
    attr = f"connection_group_id={int(group)}"
    if "&name=" in conn_str:
        return conn_str.replace("&name=", f"&{attr}&name=", 1)
    return f"{conn_str}&{attr}"


def _build_tag_path(cfg, name: str, type_hint: str | None = None,
                    read_cache_ms: int = 0, group=None) -> tuple[str | None, dict]:
    """Universal path builder.

    Returns (connection_string, meta) where meta = {
      'kind': 'slc' | 'lgx',
      'reader': <key for value extractor>,
      'elem_size': int,
      'bit_idx': int | None,     # Logix DINT bit extract
      'logix_name': str,         # Logix tag name minus bit-selector
    }
    Returns (None, {}) if unparseable.
    """
    if cfg.controller_type == 'logix':
        elem_size, reader, bit_idx = _infer_logix_type(name, type_hint)
        lgx_name = name
        if bit_idx is not None:
            # Strip trailing ".<bit>" for the actual read; we extract in value layer
            lgx_name = name.rsplit('.', 1)[0]
        conn_str = _with_conn_group(
            _logix_tag_path(cfg, lgx_name, elem_size, read_cache_ms), group)
        return conn_str, {
            'kind': 'lgx',
            'reader': reader,
            'elem_size': elem_size,
            'bit_idx': bit_idx,
            'logix_name': lgx_name,
        }

    # SLC / MicroLogix — delegate to existing parser
    conn_str = _with_conn_group(_slc_tag_path(cfg.ip, cfg.slot, name), group)
    return conn_str, {'kind': 'slc'} if conn_str else {}


def _logix_read_value(tag_handle: int, meta: dict):
    """Extract typed value from a libplctag Logix tag handle."""
    reader = meta.get('reader', 'dint')
    bit_idx = meta.get('bit_idx')

    if bit_idx is not None:
        # Use plc_tag_get_bit — it respects the tag's actual buffer size, so
        # this works whether the parent is INT (2 bytes, Module I/O) or DINT.
        # Reading uint32 on a 2-byte tag yields 0xFFFF____ garbage in the
        # upper half, making bits 8-31 always read as True.
        rc = int(_libplctag.plc_tag_get_bit(tag_handle, bit_idx))
        if rc < 0:
            # Negative = libplctag error code (bit beyond the element, e.g. .17
            # on a 2-byte INT word). bool(-6) is True — a silent always-on cell.
            err = _libplctag.plc_tag_decode_error(rc)
            raise ValueError(f"bit {bit_idx} out of range: "
                             f"{err.decode() if err else rc}")
        return bool(rc)

    # Guess vs. reality: shrink/grow the accessor to the buffer libplctag has.
    fitted = _logix_fit_reader(reader, _logix_actual_size(tag_handle), meta)
    if fitted != reader:
        reader = fitted
        meta['reader'] = fitted     # handle is cached — correct it once, for good

    if reader == 'bool':
        return bool(_libplctag.plc_tag_get_uint8(tag_handle, 0) & 1)
    if reader == 'sint':
        return int(_libplctag.plc_tag_get_int8(tag_handle, 0))
    if reader == 'usint':
        return int(_libplctag.plc_tag_get_uint8(tag_handle, 0))
    if reader == 'int':
        return int(_libplctag.plc_tag_get_int16(tag_handle, 0))
    if reader == 'uint':
        return int(_libplctag.plc_tag_get_uint16(tag_handle, 0))
    if reader == 'udint':
        return int(_libplctag.plc_tag_get_uint32(tag_handle, 0))
    if reader == 'lint':
        return int(_libplctag.plc_tag_get_int64(tag_handle, 0))
    if reader == 'real':
        return round(float(_libplctag.plc_tag_get_float32(tag_handle, 0)), 6)
    if reader == 'lreal':
        return round(float(_libplctag.plc_tag_get_float64(tag_handle, 0)), 6)
    if reader == 'string':
        if _LIBPLCTAG_HAS_STRING:
            try:
                slen = _libplctag.plc_tag_get_string_length(tag_handle, 0)
                if slen <= 0:
                    return ""
                buf = ctypes.create_string_buffer(max(slen + 1, 2))
                rc = _libplctag.plc_tag_get_string(tag_handle, 0, buf, slen + 1)
                if rc == PLCTAG_STATUS_OK:
                    return buf.value.decode('latin-1', errors='replace')
            except Exception:
                pass
        # Fallback: read raw LEN + DATA[82] structure
        try:
            ln = _libplctag.plc_tag_get_int32(tag_handle, 0)
            ln = max(0, min(82, int(ln)))
            chars = bytes(_libplctag.plc_tag_get_uint8(tag_handle, 4 + i)
                          for i in range(ln))
            return chars.decode('latin-1', errors='replace')
        except Exception:
            return None

    # default — DINT
    return int(_libplctag.plc_tag_get_int32(tag_handle, 0))


def _read_tag_value(tag_handle: int, name: str, meta: dict, base_off: int = 0):
    """Dispatch to SLC or Logix value extractor based on meta.kind."""
    if meta.get('kind') == 'lgx':
        return _logix_read_value(tag_handle, meta)
    return _slc_read_value(tag_handle, name, base_off)


# ── SLC block reads ──────────────────────────────────────────────────────────
# PCCC has no equivalent of the CIP Multi-Service Packet: every tag handle is
# its own request/response, and an SLC 5/05 answers them one at a time no matter
# how many connections are open — which is why extra connections only add
# timeouts there. What PCCC *does* offer is a multi-element read: one request
# can fetch a whole run of a data file. Reading N7:0..N7:79 in a single request
# instead of eighty replaces eighty round-trips with one.
_SLC_BLOCK_SENTINEL = "\x00BLK"

# Bytes per element, matching what _slc_tag_path builds for a single element —
# block extraction must land on exactly the same layout.
# 'R' must be here as well as in _slc_tag_path: without it the block planner
# strides R elements by 2 bytes and every element after the first is read from
# inside its predecessor — wrong values, no error.
_SLC_ELEM_SIZE = {'T': 6, 'C': 6, 'R': 6, 'F': 4}

# Payload ceiling per PCCC read. An SLC 5/05 tops out around 236 bytes of data
# per request; 224 keeps a margin for the reply header while filling the packet.
# A controller that refuses this size does not lose block reads — the cap for
# that file is halved on the spot (see _slc_block_cap).
_SLC_BLOCK_BYTES = 224
_SLC_BLOCK_BYTES_MIN = 16     # 8 words — below this a block barely beats singles

# A block is cut down only when the controller refuses it this many cycles in a
# row: a one-off refusal on a busy line must not cost the file its block size.
_SLC_REFUSALS_TO_SHRINK = 3
# One plain word every SLC / MicroLogix has (the connect probe reads it too):
# read beside blocks that were all refused, it tells a block the controller will
# not serve from a controller that answers nothing at all.
_SLC_PROBE = "S2:0"
# A cut-down file tries its previous size again after this long without a
# refusal; a try that is refused again doubles the wait, up to the ceiling.
_SLC_REGROW_S = 300.0
_SLC_REGROW_MAX_S = 3600.0

# Bytes of unwanted data worth carrying inside a block to avoid one more
# request. This is the whole trade, and its answer depends on the link:
#   • plain Ethernet to the CPU — a request costs a slice of the program scan
#     (~40 ms measured) while bytes are free, so a generous value wins;
#   • serial port behind a COM-to-Ethernet converter — every byte is paid for on
#     the wire (at 38400 baud a byte is ~0.26 ms), so filling holes to save a
#     request quickly costs more than the request did.
# The default is the frugal side: it never makes a cycle much worse, and it is
# the setup that is measurably sensitive. Raise Config.slc_gap_bytes on a
# directly connected controller.
_SLC_GAP_BYTES = 16

# I/O image block reads (Config.io_block). A hole wider than one maximal SLC card
# (32 words = 64 bytes, roughly what one request costs on a 19200-baud line)
# splits the block — up to that, carrying another card's words is cheaper than
# asking twice.
_SLC_IO_GAP_WORDS = 32
# A block is trusted only after its words agree with the same words read one by
# one. One disagreement can be an input that changed between the two reads; the
# same word disagreeing this many checks in a row means the slot layout is wrong.
_IO_CHECK_MISMATCHES = 3
_IO_CHECK_MAX_TRIES = 10      # checks without one clean pass → give up on the file
# Two reads of a word further apart than this tell nothing about which of its
# bits are moving right now (see LibplctagConnection._io_settle).
_IO_MOVE_WINDOW_S = 10.0
# A confirmed block is compared again this often. A layout that is one card off
# passes a check whenever the shifted words happen to hold equal values (an idle
# machine: all zeros), so the check has to come back while the plant runs. One
# slow cycle a minute costs about 1 % of the update rate.
_IO_RECHECK_S = 60.0


def _slc_block_elem_size(prefix: str) -> int:
    return _SLC_ELEM_SIZE.get(prefix, 2)


def _slc_block_key(prefix: str, file_num: int, start: int, count: int) -> str:
    return f"{prefix}{file_num}:{start}{_SLC_BLOCK_SENTINEL}{count}"


def _slc_parse_block_key(key: str):
    """Block cache key → (name_of_first_element, elem_size, count) or None."""
    if _SLC_BLOCK_SENTINEL not in key:
        return None
    head, _, cnt = key.partition(_SLC_BLOCK_SENTINEL)
    parsed = _slc_parse_addr(head)
    if not parsed:
        return None
    return head, _slc_block_elem_size(parsed[0]), int(cnt)


def _slc_block_file(key: str):
    """Block cache key → its data file (prefix, file_num), or None."""
    blk = _slc_parse_block_key(key)
    parsed = _slc_parse_addr(blk[0]) if blk else None
    return (parsed[0], parsed[1]) if parsed else None


def _phys_label(name: str) -> str:
    """A physical read as the log prints it: N7:0 ×28, MyTimer(struct)."""
    return (name.replace(_LGX_STRUCT_SENTINEL, '(struct)')
                .replace(_SLC_BLOCK_SENTINEL, ' ×'))


def _plan_slc_blocks(names, skip_files=(), cap_for=None, gap_bytes=None):
    """Group single-element SLC addresses into multi-element block reads.

    Returns {phys_name: (block_key, byte_offset)} for every address that a block
    covers; addresses left out (I/O files, unparseable names, files opted out
    after a failure) simply keep their own handle.

    Only whole-element addresses arrive here — bits were already folded into
    their parent word upstream — so a block is a plain contiguous run.

    A block grows until it would exceed the payload cap, and a hole inside it is
    carried along only while the wasted bytes stay under `gap_bytes` — the point
    where skipping a request stops paying for itself. See _SLC_GAP_BYTES for why
    that budget, not the request count alone, is what has to be tuned.

    `cap_for((prefix, file_num))` yields the byte cap for one file, letting a
    controller that refused a large request keep smaller blocks.
    """
    by_file = {}
    for name in names:
        parsed = _slc_parse_addr(name)
        if not parsed:
            continue
        prefix, file_num, element, bit, sub = parsed
        # I/O files are addressed as <P><file>:<slot>.<word>: the element is the
        # slot and the word rides in `sub`, so a run of elements is not a run of
        # words. Left alone — an I/O watch list is a handful of words anyway.
        if prefix in ('I', 'O'):
            continue
        if (prefix, file_num) in skip_files:
            continue
        by_file.setdefault((prefix, file_num), []).append((element, name))

    plan = {}
    for (prefix, file_num), items in by_file.items():
        if len(items) < 2:
            continue                      # one address is already one request
        items.sort()
        esz = _slc_block_elem_size(prefix)
        cap = _SLC_BLOCK_BYTES if cap_for is None else cap_for((prefix, file_num))
        max_elems = max(1, int(cap) // esz)
        gap_b = _SLC_GAP_BYTES if gap_bytes is None else int(gap_bytes)
        max_gap = max(0, gap_b // esz)     # unwanted elements worth carrying

        def flush(run):
            if len(run) < 2:
                return
            start = run[0][0]
            count = run[-1][0] - start + 1
            key = _slc_block_key(prefix, file_num, start, count)
            for element, name in run:
                plan[name] = (key, (element - start) * esz)

        run = [items[0]]
        for element, name in items[1:]:
            # Split when the block would outgrow one request, or when the hole
            # before this address costs more bytes than an extra request saves.
            if (element - run[0][0] + 1 > max_elems
                    or element - run[-1][0] - 1 > max_gap):
                flush(run)
                run = [(element, name)]
            else:
                run.append((element, name))
        flush(run)
    return plan


def _slc_io_word(name: str):
    """(prefix, file_num, slot, word) for an I/O word address like I:3.0, else None."""
    parsed = _slc_parse_addr(name)
    if not parsed:
        return None
    prefix, file_num, slot, bit, sub = parsed
    if prefix not in ('I', 'O') or bit is not None:
        return None
    if not (isinstance(sub, str) and sub.startswith('W')):
        return None
    return prefix, file_num, slot, int(sub[1:])


def _plan_slc_io_blocks(names, io_slots, skip_files=()):
    """Group SLC I/O words into block reads, given the rack's word layout.

    The input and the output image are packed slot after slot, each slot taking
    the words the I/O configuration gives it — `io_slots` = {slot: (input_words,
    output_words)}, the SCAN_IN / SCAN_OUT columns of a .SLC export. So I:6.0
    follows I:3.0 directly when slots 4 and 5 hold output cards, and a run of
    I/O words is one request, like a run of N7. Without the layout the word after
    I:3.0 cannot be named, which is why _plan_slc_blocks leaves I/O alone.

    Returns {phys_name: (block_key, byte_offset)} like _plan_slc_blocks. A block
    runs from the first to the last wanted word of a file — the holes are other
    cards' words, a few bytes against a request saved — and is split only past
    the payload cap or across a hole wider than _SLC_IO_GAP_WORDS. Words the
    layout does not place (unknown slot, a word past its card) keep their own
    request; the controller answers those exactly as before.
    """
    by_file = {}
    for name in names:
        io = _slc_io_word(name)
        if io is None:
            continue
        prefix, file_num, slot, word = io
        if (prefix, file_num) in skip_files:
            continue
        side = 0 if prefix == 'I' else 1
        if word >= io_slots.get(slot, (0, 0))[side]:
            continue
        by_file.setdefault((prefix, file_num), []).append((slot, word, name))

    plan = {}
    max_words = _SLC_BLOCK_BYTES // 2
    for (prefix, file_num), items in by_file.items():
        if len(items) < 2:
            continue                      # one word is already one request
        side = 0 if prefix == 'I' else 1
        first, at = {}, 0                 # image offset of each slot's word 0
        for s in sorted(io_slots):
            first[s] = at
            at += io_slots[s][side]

        def pos(item):
            return first[item[0]] + item[1]

        def flush(run):
            if len(run) < 2:
                return
            base = pos(run[0])
            key = (f"{prefix}:{run[0][0]}.{run[0][1]}"
                   f"{_SLC_BLOCK_SENTINEL}{pos(run[-1]) - base + 1}")
            for item in run:
                plan[item[2]] = (key, (pos(item) - base) * 2)

        items.sort(key=pos)
        run = [items[0]]
        for item in items[1:]:
            if (pos(item) - pos(run[0]) + 1 > max_words
                    or pos(item) - pos(run[-1]) - 1 > _SLC_IO_GAP_WORDS):
                flush(run)
                run = [item]
            else:
                run.append(item)
        flush(run)
    return plan


def _slc_read_value(tag_handle: int, name: str, base_off: int = 0):
    """Extract typed value from a libplctag tag handle.

    Timer/Counter/Control layout (elem_size=6, elem_count=1):
      bytes 0-1  word 0  status bits  T: EN/TT/DN   C: CU/CD/DN/OV/UN
                                      R: EN/EU/DN/EM/ER/UL/IN/FD
      bytes 2-3  word 1  PRE (timer/counter preset)  | LEN (control length)
      bytes 4-5  word 2  ACC (timer/counter accum)   | POS (control position)

    `base_off` is the byte offset of this element inside the handle's buffer. It
    is 0 for a one-element handle and the element's position when the value
    comes out of a block read (a whole run of N7 / B3 / T4 fetched in a single
    PCCC request) — every offset below is relative to the element, so shifting
    the base is all that block support needs here.

    Returns int, float, bool, or a dict for a bare structure element:
    {CTL, PRE, ACC} for T/C, {CTL, LEN, POS} for R (CONTROL).
    """
    parsed = _slc_parse_addr(name)
    if not parsed:
        return None
    prefix, file_num, element, bit, sub = parsed

    if prefix in ('T', 'C', 'R'):
        bits_map = _STRUCT_BITS[prefix]

        if sub and sub in bits_map:
            # Named status bit: EN, TT, DN, CU, CD, OV, UN (T/C)
            #                   EN, EU, DN, EM, ER, UL, IN, FD (R)
            ctl_word = _libplctag.plc_tag_get_uint16(tag_handle, base_off)
            return bool((ctl_word >> bits_map[sub]) & 1)

        elif sub in _TC_BYTE_OFF:
            # PRE/ACC (timer, counter) or LEN/POS (control) — byte offset per member
            return _libplctag.plc_tag_get_int16(tag_handle, base_off + _TC_BYTE_OFF[sub])

        elif sub is None and bit is not None:
            # Numeric bit within control word: T4:0/13 (same as DN), R6:0/12 (EM)
            ctl_word = _libplctag.plc_tag_get_uint16(tag_handle, base_off)
            return bool((ctl_word >> bit) & 1)

        else:
            # Bare element → structured dict, named for the element type
            ctl = _libplctag.plc_tag_get_uint16(tag_handle, base_off)
            w1 = _libplctag.plc_tag_get_int16(tag_handle, base_off + 2)
            w2 = _libplctag.plc_tag_get_int16(tag_handle, base_off + 4)
            if prefix == 'R':
                return {'CTL': ctl, 'LEN': w1, 'POS': w2}
            return {'CTL': ctl, 'PRE': w1, 'ACC': w2}

    elif prefix == 'F':
        return round(_libplctag.plc_tag_get_float32(tag_handle, base_off), 6)

    elif prefix in ('I', 'O') and (
            (isinstance(sub, str) and sub.startswith('W')) or bit is not None):
        # I/O word request: whole 16-bit word if no bit, else extracted bit.
        # Covers both I:slot.word (word-only) and I:slot/bit (bit fallback).
        word_val = _libplctag.plc_tag_get_uint16(tag_handle, base_off)
        if bit is None:
            return word_val
        return bool((word_val >> bit) & 1)

    elif bit is not None:
        # Bit-level access on a 16-bit word (e.g. B3:0/5, N7:0/3)
        word = _libplctag.plc_tag_get_uint16(tag_handle, base_off)
        return bool((word >> bit) & 1)

    else:
        return _libplctag.plc_tag_get_int16(tag_handle, base_off)


# ─────────────────────────────────────────────────────────────────────────────
_LIBPLCTAG_TIMEOUT = 3000  # ms


class LibplctagConnection:
    """Unified libplctag connection for SLC / MicroLogix / ControlLogix / CompactLogix.

    All reads use non-blocking pattern (plc_tag_read with timeout=0, then poll
    plc_tag_status). For Logix this lets libplctag pack multiple pending reads
    into a single CIP Multi-Service Packet (allow_packing=1), matching or
    beating pylogix batch throughput for large watch lists.
    """

    def __init__(self, cfg: Config, logger: logging.Logger | None = None):
        self.cfg = cfg
        # Session logger: with several controllers polled at once every line has
        # to say which one it came from.
        self.log = logger or log
        self.connected = False
        self.auto_reconnect = True
        self.controller_info = {}
        self.error = ""
        self._lock = asyncio.Lock()
        self._closing = False                     # set for good by cancel_reads()
        self._handles: dict[str, int] = {}        # addr → tag handle
        self._handle_meta: dict[str, dict] = {}   # addr → meta from _build_tag_path
        self._type_hints: dict[str, str] = {}     # addr → L5X dataType (from tracer)
        self._err_seen: dict[str, str] = {}       # addr → last logged read error
        # SLC data files whose block read failed — they fall back to one request
        # per element. A block read is the single biggest win on PCCC, but it is
        # the controller that decides how large a request it will answer, so a
        # refusal must degrade instead of breaking the file.
        self._slc_no_block: set = set()
        # Per-file payload cap. A controller that refuses a large multi-element
        # read gets a smaller one next cycle rather than losing block reads
        # altogether — the request count matters far too much to surrender it
        # over a size the SLC simply would not quote.
        self._slc_block_cap: dict = {}
        # Refusal bookkeeping for those two: consecutive refused cycles per
        # block key, when each file last changed size, how long it waits before
        # trying a bigger size again, and the files reading at a size that was
        # just given back (see _slc_shrink / _slc_regrow).
        self._slc_refused: dict = {}
        self._slc_shrunk_at: dict = {}
        self._slc_regrow_wait: dict = {}
        self._slc_probing: set = set()
        self._refused_all_logged = False          # "refused every request" said once
        self._slc_probe_next = False              # add _SLC_PROBE to the next read
        # I/O image block reads (Config.io_block): the rack layout the tracer
        # sends with its watch list — {slot: (input words, output words)} — and
        # the checks that compare a block with word-by-word reads before it is
        # trusted (see _io_plan / _io_settle). The layout arrives on the event
        # loop while a read runs in the executor, hence the lock.
        self._io_mu = threading.RLock()
        self._io_slots = None
        self._io_reset()
        # When the request now in flight on each handle was sent. A reply that
        # never comes used to keep its tag "in flight" for the life of the
        # connection, and every cycle then waited the full deadline for it.
        self._inflight: dict[str, float] = {}
        self._lost_streak: dict[str, int] = {}   # consecutive lost replies per handle
        self.lost_replies = 0                    # cancelled on this connection
        # Measured pace of the link: seconds per request in the last cycle and
        # a running average — what the batch deadline is sized from.
        self._req_s_last = 0.0
        self._req_s_avg = 0.0
        self.last_req_ms = 0.0
        # Physical requests issued by the last read — the number the poll cycle
        # is actually made of, after bits, structures and SLC blocks collapse.
        self.last_phys_reads = 0
        # Payload bytes those requests carry. On a serial-gateway link this is
        # what the cycle time is made of, so it is the number to tune against.
        self.last_phys_bytes = 0
        # ── DEBUG_SAVE counters: prove handle cache reuse (no duplicate reads) ──
        self._dbg_handle_hits = 0      # _get_or_create served from cache
        self._dbg_handle_creates = 0   # _get_or_create created a new libplctag handle

    # ── parallel connections ------------------------------------------------
    def _effective_groups(self) -> int:
        """How many CIP connections to spread the watch list over.

        Auto (0) picks 4 for Logix — a CompactLogix/ControlLogix has connection
        slots to spare and answers packed requests in parallel — and 2 for
        SLC/MicroLogix, whose PCCC stack is far more modest about concurrent
        connections. Anything above 8 buys little: the controller's own scan
        becomes the limit, and every group costs it a connection slot.
        """
        n = int(getattr(self.cfg, 'conn_groups', 0) or 0)
        if n <= 0:
            n = 4 if self.cfg.controller_type == 'logix' else 2
        return max(1, min(16, n))

    def _warn_if_slc_overparallel(self):
        """PCCC gains almost nothing from extra connections — and loses a lot.

        An SLC/MicroLogix answers one request at a time no matter how many
        connections are open, so beyond two the extra requests only queue up and
        run out of time. Measured on a live SLC 5/05: 2 connections shaved the
        cycle from 2500 to 2000 ms, 4 produced a wall of timeouts.
        """
        if self.cfg.controller_type != 'logix' and self._effective_groups() > 2:
            self.log.warning(
                f"{self._effective_groups()} connections on a PCCC controller: "
                f"an SLC/MicroLogix serves requests one at a time, so more than 2 "
                f"usually just adds timeouts. Use block reads (on by default) to "
                f"shorten the cycle instead.")

    def _group_for(self, name: str):
        """Stable shard for a tag name; None when running on one connection."""
        n = self._effective_groups()
        if n <= 1:
            return None
        # crc32 (not hash()) — deterministic across restarts, so a tag keeps its
        # connection and the handle cache survives a reconnect unchanged.
        base = name[:-len(_LGX_STRUCT_SENTINEL)] if name.endswith(_LGX_STRUCT_SENTINEL) else name
        return zlib.crc32(base.encode('utf-8')) % n

    async def update_conn_groups(self, n: int):
        async with self._lock:
            await asyncio.get_event_loop().run_in_executor(
                None, self._update_conn_groups_sync, n)

    def _update_conn_groups_sync(self, n: int):
        """Change the connection count; every handle must be rebuilt.

        The group id is baked into the connection string at creation time, so
        live handles keep their old session until they are destroyed.
        """
        n = max(0, min(16, int(n)))
        if n == int(getattr(self.cfg, 'conn_groups', 0) or 0):
            return
        self.cfg.conn_groups = n
        cnt = len(self._handles)
        self._destroy_all()
        self.log.info(f"CIP connections → {self._effective_groups()}"
                 + (" (auto)" if n == 0 else "")
                 + (f"; {cnt} handle(s) dropped for rebuild" if cnt else ""))
        self._warn_if_slc_overparallel()

    # ── type hints ---------------------------------------------------------
    def set_tag_types(self, type_map: dict):
        """Tracer pushes {tag_name: dataType} from its L5X symbol table."""
        if not type_map:
            return
        changed = 0
        for name, dt in type_map.items():
            if not name or not dt:
                continue
            prev = self._type_hints.get(name)
            if prev != dt:
                self._type_hints[name] = dt
                changed += 1
                # Invalidate existing handle — size may have changed
                if name in self._handles:
                    self._drop_handle(name)
                # Also drop the shared atomic-structure handle if one exists,
                # so a changed TIMER/COUNTER hint rebuilds with the right layout.
                struct_key = name + _LGX_STRUCT_SENTINEL
                if struct_key in self._handles:
                    self._drop_handle(struct_key)
        if changed:
            self.log.info(f"Received {changed} tag type hint(s) from tracer")

    # ── poll interval / read cache -----------------------------------------
    async def update_poll_interval(self, interval: float):
        async with self._lock:
            await asyncio.get_event_loop().run_in_executor(
                None, self._update_poll_interval_sync, interval)

    def _update_poll_interval_sync(self, interval: float):
        """Re-target read_cache_ms on every cached handle.

        The cache window (80% of the poll interval) is baked into each handle's
        connection string at creation time, so a runtime set_interval would
        otherwise leave existing handles serving values from the previous —
        possibly much longer — cache window (e.g. 1 s → 0.2 s still cached
        800 ms). plc_tag_set_int_attribute also expires the current cache;
        handles that refuse the update are dropped and rebuilt with the right
        cache on their next read.
        """
        self.cfg.poll_interval = interval
        cache_ms = int(max(0, interval * 1000 * 0.8))
        if _LIBPLCTAG_HAS_SET_ATTR:
            failed = []
            for name, h in self._handles.items():
                try:
                    rc = _libplctag.plc_tag_set_int_attribute(h, b"read_cache_ms", cache_ms)
                    if rc != PLCTAG_STATUS_OK:
                        failed.append(name)
                except Exception:
                    failed.append(name)
            for name in failed:
                self._drop_handle(name)
            self.log.info(f"poll_interval={interval}s → read_cache_ms={cache_ms} "
                     f"applied to {len(self._handles)} handle(s)"
                     + (f", {len(failed)} dropped for rebuild" if failed else ""))
        else:
            n = len(self._handles)
            self._destroy_all()
            self.log.info(f"poll_interval={interval}s → DLL lacks set_int_attribute; "
                     f"dropped {n} handle(s) to rebuild with read_cache_ms={cache_ms}")

    # ── lifecycle ---------------------------------------------------------
    def cancel_reads(self):
        """The session is being stopped: a read or a connect probe running on
        the executor gives up at its next poll pass, and none starts again.

        One way only — a stopped session never uses its connection again
        (Reconnect builds a new one). Without it, stopping waited out the whole
        batch deadline of a read on a slow line, up to 10 s, and closing the
        window did not wait that long: the process went down with every
        handle still open.
        """
        self._closing = True

    async def _run_locked(self, fn, *args):
        """Run fn on the executor under the connection lock, and keep the lock
        until the thread has really finished.

        Cancelling the task that awaits it cannot stop a thread already inside
        libplctag. A plain `async with` released the lock right then, so stop()
        destroyed the handles while the read was still polling them; the read
        dropped failed handles out of the very dict the destroy was walking,
        and whatever the destroy had not reached yet stayed open.
        """
        async with self._lock:
            fut = asyncio.get_event_loop().run_in_executor(None, fn, *args)
            try:
                return await asyncio.shield(fut)
            except asyncio.CancelledError:
                await asyncio.wait({fut})    # quick after cancel_reads()
                raise

    async def connect(self) -> bool:
        return await self._run_locked(self._connect_sync)

    def _probe_wait(self, tag: int, rc: int):
        """Wait for a non-blocking create or read of the probe tag to finish.

        Returns its final status, PLCTAG_ERR_TIMEOUT after _LIBPLCTAG_TIMEOUT,
        or None as soon as the session is being stopped. A blocking call could
        not be cut short: closing the window while an unreachable controller
        was being probed sat out up to two full timeouts.
        """
        deadline = time.monotonic() + _LIBPLCTAG_TIMEOUT / 1000.0
        while rc == PLCTAG_STATUS_PENDING:
            if self._closing:
                return None
            if time.monotonic() >= deadline:
                return PLCTAG_ERR_TIMEOUT
            time.sleep(0.01)
            rc = _libplctag.plc_tag_status(tag)
        return rc

    def _connect_sync(self) -> bool:
        if self._closing:
            return False
        try:
            self._destroy_all()
            if self.cfg.controller_type == 'logix':
                probe_path = _logix_tag_path(self.cfg, "@tags", 4, 0)
                probe_label = "Logix @tags"
            else:
                probe_path = _slc_tag_path(self.cfg.ip, self.cfg.slot, "S2:0")
                probe_label = "SLC S2:0"
            if not probe_path:
                self.error = "Cannot build probe tag path"
                return False
            # Non-blocking create and read, waited out in _probe_wait so that a
            # session being stopped does not have to sit through the timeouts.
            tag = _libplctag.plc_tag_create(probe_path.encode('utf-8'), 0)
            rc = tag if tag < 0 else self._probe_wait(tag, _libplctag.plc_tag_status(tag))
            if rc is not None and rc != PLCTAG_STATUS_OK:
                if tag >= 0:
                    _libplctag.plc_tag_destroy(tag)
                err = _libplctag.plc_tag_decode_error(rc)
                self.error = f"libplctag create failed: {err.decode() if err else rc}"
                self.connected = False
                self.log.error(f"libplctag connect error ({probe_label}): {self.error}")
                return False
            if rc is not None:
                rc = self._probe_wait(tag, _libplctag.plc_tag_read(tag, 0))
            _libplctag.plc_tag_destroy(tag)
            if rc is None:
                self.connected = False           # stopped mid-probe, nothing to say
                return False
            if rc != PLCTAG_STATUS_OK:
                err = _libplctag.plc_tag_decode_error(rc)
                self.error = f"libplctag probe read failed: {err.decode() if err else rc}"
                self.connected = False
                self.log.error(f"libplctag connect probe error ({probe_label}): {self.error}")
                return False
            self.connected = True
            self.error = ""
            backend_label = "libplctag/CIP" if self.cfg.controller_type == 'logix' else "libplctag/PCCC"
            self.controller_info = {
                "name": f"{self.cfg.processor_type} @ {self.cfg.ip} ({backend_label})",
                "ip": self.cfg.ip,
                "slot": self.cfg.slot,
                "type": self.cfg.controller_type,
                "backend": "libplctag",
            }
            self.log.info(f"✓ Connected: {self.controller_info['name']} "
                     f"· {self._effective_groups()} CIP connection(s)"
                     + ("" if self.cfg.controller_type == 'logix'
                        else f" · block reads "
                             f"{'on' if getattr(self.cfg, 'slc_blocks', True) else 'off'}"))
            self._warn_if_slc_overparallel()
            return True
        except Exception as e:
            self.connected = False
            self.error = str(e)
            self.log.error(f"libplctag connect error: {e}")
            return False

    def _destroy_all(self):
        for h in self._handles.values():
            try:
                _libplctag.plc_tag_destroy(h)
            except Exception:
                pass
        self._handles.clear()
        self._handle_meta.clear()
        self._inflight.clear()

    async def disconnect(self):
        async with self._lock:
            await asyncio.get_event_loop().run_in_executor(None, self._destroy_all)
            self.connected = False

    # ── reads -------------------------------------------------------------
    async def read_tags(self, tag_names: list) -> dict:
        if not self.connected:
            return {}
        return await self._run_locked(self._read_sync, tag_names)

    def _read_sync(self, tag_names: list) -> dict:
        """Non-blocking batch read: start all pending, poll until done, collect.

        This pattern lets libplctag (with allow_packing=1) combine multiple
        concurrent CIP reads into one Multi-Service Packet on the wire, which
        is what makes Logix reads of 100+ tags competitive with pylogix batch.

        ── Word-read optimisation ────────────────────────────────────────────
        Bit addresses that belong to the same atomic integer word
        (Logix `Tag.N` / `Local:1:I.Data.N`, SLC `B3:0/N`, `I:1.0/N`, …) are
        grouped and the *parent word* is read ONCE per cycle; every requested
        bit is then extracted from that single value.  Benefits:
          • Synchronicity — all bits of a word come from the same scan instant
            (no torn reads where bit 0 is from scan N and bit 7 from scan N+1).
          • Fewer CIP / PCCC round-trips (8 bits of a word → 1 read, not 8).
        The result map still uses the original per-bit keys, so the tracer,
        recorder and `.ndrec` format see no change whatsoever.

        Extraction is byte-for-byte identical to the legacy per-bit path:
          • Logix → read parent (elem_size 4) + plc_tag_get_bit(handle, bit)
          • SLC   → read parent word (uint16) + (word >> bit) & 1
        Structure bits (TIMER/COUNTER/CONTROL: .DN/.EN/T4:0/DN/T4:0/13/…) are
        never promoted — they fall through to a direct read, unchanged.
        """
        if self._closing:
            return {}                  # the session is stopping (cancel_reads)
        results: dict[str, dict] = {}
        is_logix = (self.cfg.controller_type == 'logix')

        # ── Plan: classify each requested tag ──────────────────────────────
        bit_map: dict[str, tuple[str, int]] = {}  # orig_name -> (word_name, bit)
        # orig_name -> (struct_key, member, struct_type) for atomic TIMER/COUNTER
        struct_map: dict[str, tuple[str, str, str]] = {}
        struct_base: dict[str, str] = {}           # struct_key -> base tag name
        direct: list[str] = []                     # names read & returned as-is
        phys_set: set[str] = set()                 # physical tags to actually read
        for name in tag_names:
            # 1) TIMER/COUNTER/CONTROL member → read parent structure atomically
            promo_s = self._promote_struct_member(name, is_logix)
            if promo_s is not None:
                base, member, stype = promo_s
                struct_key = base + _LGX_STRUCT_SENTINEL
                struct_map[name] = (struct_key, member, stype)
                struct_base[struct_key] = base
                phys_set.add(struct_key)
                continue
            # 2) Bit of an atomic integer word → read parent word once
            promo = self._promote_bit(name, is_logix)
            if promo is None:
                direct.append(name)
                phys_set.add(name)
            else:
                word_name, bit = promo
                bit_map[name] = (word_name, bit)
                phys_set.add(word_name)

        phys_ok: dict[str, int] = {}      # phys_name -> handle (read complete)
        phys_err: dict[str, dict] = {}    # phys_name -> error dict

        # ── SLC: fold the physical set into block reads ────────────────────
        # One PCCC request per address is what makes a big SLC watch list slow;
        # contiguous runs of a data file collapse into a single request here.
        block_of: dict[str, tuple[str, int]] = {}   # phys_name -> (block_key, byte_off)
        # I/O words read as a block AND one by one this cycle (see _io_plan)
        io_check: dict[str, tuple[str, int]] = {}
        io_slots = self._io_slots
        if (not is_logix) and getattr(self.cfg, 'slc_blocks', True):
            if self._slc_block_cap or self._slc_no_block:
                self._slc_regrow()
            block_of = _plan_slc_blocks(
                phys_set, self._slc_no_block,
                lambda k: self._slc_block_cap.get(k, _SLC_BLOCK_BYTES),
                getattr(self.cfg, 'slc_gap_bytes', _SLC_GAP_BYTES))
            # The I/O image as well, on a controller that has it switched on
            if getattr(self.cfg, 'io_block', False):
                io_blocks, io_check = self._io_plan(phys_set, io_slots)
                block_of.update(io_blocks)
        if block_of or io_check:
            covered = set(block_of)
            phys_names = sorted(phys_set - covered)
            phys_names += sorted({k for k, _ in block_of.values()}
                                 | {k for k, _ in io_check.values()})
        else:
            phys_names = list(phys_set)
        # Last cycle the controller refused every block with nothing else to
        # tell a size limit from a PLC that refuses everything — ask it one
        # plain word too (see "Refusals" below).
        if self._slc_probe_next and not is_logix and _SLC_PROBE not in phys_names:
            phys_names.append(_SLC_PROBE)

        def _hit(phys_name):
            """Handle + byte offset holding this address, or None if not read."""
            b = block_of.get(phys_name)
            if b is None:
                h = phys_ok.get(phys_name)
                return (h, 0) if h is not None else None
            h = phys_ok.get(b[0])
            return (h, b[1]) if h is not None else None

        def _err_of(phys_name):
            """Error recorded for this address, via its block when it has one."""
            b = block_of.get(phys_name)
            return phys_err.get(b[0]) if b else phys_err.get(phys_name)

        if DEBUG_SAVE:
            # Request de-duplication summary — proves the bridge never issues
            # duplicate reads even when the tracer sends a word AND its bits, or
            # the same address twice. `requested` is the raw count from the
            # tracer; `dup_in_request` is how many collapsed via the phys_set.
            n_req = len(tag_names)
            n_uniq_req = len(set(tag_names))
            n_words = len(set(w for w, _ in bit_map.values()))
            n_structs = len(struct_base)
            self.log.info(
                f"[DBG-SAVE] read plan: requested={n_req} "
                f"(unique={n_uniq_req}, dup_in_request={n_req - n_uniq_req}) → "
                f"physical_reads={len(phys_names)} "
                f"[bits={len(bit_map)} grouped into {n_words} words, "
                f"struct_members={len(struct_map)} grouped into {n_structs} structs, "
                f"direct={len(direct)}, "
                f"slc_block_reads={len({k for k, _ in block_of.values()})} covering "
                f"{len(block_of)} addresses]")
        self.last_phys_reads = len(phys_names)

        def _phys_bytes(n):
            blk = _slc_parse_block_key(n)
            if blk:
                return blk[1] * blk[2]
            meta = self._handle_meta.get(n) or {}
            if meta.get('elem_size'):
                return int(meta['elem_size'])
            if is_logix:
                return 4                      # not yet created: DINT-sized guess
            parsed = _slc_parse_addr(n)
            return _slc_block_elem_size(parsed[0]) if parsed else 2

        self.last_phys_bytes = sum(_phys_bytes(n) for n in phys_names)
        # Snapshot handle-cache counters + start time so the post-read summary
        # can report reuse (no duplicate handle creation) and wall-clock cost.
        _dbg_hits0 = self._dbg_handle_hits
        _dbg_creates0 = self._dbg_handle_creates
        _dbg_t0 = time.monotonic()

        # ── Non-blocking batch read of the physical tag set ────────────────
        pending:  list[tuple[str, int]] = []   # read issued, waiting for data
        creating: list[tuple[str, int]] = []   # handle still being set up
        lost_now: list[tuple[str, float]] = [] # (name, seconds waited) given up on
        # Replies that came over the wire this cycle, and when the last one
        # did — the measured pace of the link (see _note_req_time).
        n_replies = 0
        t_last_reply = _dbg_t0

        # A file whose block size was already stepped down this cycle. Several
        # blocks of one file fail together, and halving once per failure would
        # skip straight past the sizes the controller does accept.
        shrunk: set = set()
        # Blocks the controller refused this cycle — weighed once the whole
        # cycle is in (see "Refusals" below the poll loop).
        refused_now: list[tuple[str, str]] = []

        def _fail(name: str, msg: str, drop: bool, rc=None):
            phys_err[name] = {"value": None, "type": "?", "error": msg,
                              "ts": time.time()}
            if drop:
                self._drop_handle(name)
            # A block read the controller refused (too many elements, file
            # shorter than the run) must not keep failing every cycle: its file
            # steps down to smaller blocks, at the floor to one request per
            # address. Only a refusal counts — the PLC answered and said no —
            # and only once it repeats. A dropped connection, a cancelled
            # request or one bad moment on a busy line used to cut the blocks
            # for good, and the file then cost more requests until Reconnect.
            # Timeouts never land here: they keep the handle and say nothing.
            if rc in _REFUSAL_ERRS and _slc_block_file(name) is not None:
                refused_now.append((name, msg))

        def _count_refusal(name: str, msg: str):
            key = _slc_block_file(name)
            # A file already stepped down this cycle has new block keys from the
            # next one on; a count kept under this key would outlive it.
            if key is None or key in shrunk:
                return
            refused = self._slc_refused.get(name, 0) + 1
            self._slc_refused[name] = refused
            if key[0] in ('I', 'O'):
                # An I/O image block has no smaller size worth trying: the
                # controller either serves a multi-word I/O read or it does not.
                if refused >= _SLC_REFUSALS_TO_SHRINK:
                    self._io_disable(key, f"the controller refused the block read "
                                          f"({msg}, {refused} cycles in a row)")
                return
            # a size just given back is settled by its first refusal
            probing = key in self._slc_probing
            if refused < _SLC_REFUSALS_TO_SHRINK and not probing:
                return
            shrunk.add(key)
            blk = _slc_parse_block_key(name)
            self._slc_shrink(key, msg, refused, probing,
                             blk[1] * blk[2] if blk else _SLC_BLOCK_BYTES)

        def _decode(rc: int) -> str:
            err = _libplctag.plc_tag_decode_error(rc)
            return err.decode() if err else str(rc)

        def _classify(name: str, handle: int, rc: int):
            """Sort a plc_tag_read() return code into ok / pending / error."""
            if rc == PLCTAG_STATUS_OK:
                phys_ok[name] = handle
                self._inflight.pop(name, None)
            elif rc == PLCTAG_STATUS_PENDING:
                # A fresh request went out — its clock starts now.
                self._inflight[name] = time.monotonic()
                pending.append((name, handle))
            elif rc in _SOFT_READ_ERRS:
                # BUSY: a read started in an earlier cycle has not landed yet.
                # Its result goes into this same tag buffer, so there is nothing
                # to re-issue — just keep waiting on the handle we already have,
                # on the clock of the request that is actually out.
                self._inflight.setdefault(name, time.monotonic())
                pending.append((name, handle))
            else:
                _fail(name, _decode(rc), drop=True, rc=rc)

        # Kick off creation AND reads for the whole set, all non-blocking.
        # Handle creation is async too (plc_tag_create with timeout=0): a batch
        # that pulls in hundreds of first-seen tags — what opening a cross
        # reference on a big project does — used to serialise one blocking
        # create per tag and stall the cycle for seconds before the read
        # deadline even started. libplctag now sets them all up in parallel.
        for name in phys_names:
            try:
                handle, is_new = self._get_or_create(name)
                if handle is None:
                    _fail(name, f"bad address: {name}", drop=False)
                    continue
                if is_new:
                    creating.append((name, handle))  # read follows once setup ends
                    continue
                _classify(name, handle, _libplctag.plc_tag_read(handle, 0))
            except Exception as e:
                _fail(name, str(e), drop=True)

        # Poll until every read completes or the batch deadline elapses. The
        # deadline covers setup + read — a first-seen tag needs both — and
        # follows the measured pace of the link (see _read_budget).
        budget = self._read_budget(len(phys_names))
        # A request still unanswered after several whole deadlines is not slow,
        # it is lost: no later cycle would ever see its reply.
        lost_after = max(_LOST_REPLY_MIN_S, _LOST_REPLY_DEADLINES * budget)
        deadline = time.monotonic() + budget
        while (pending or creating) and time.monotonic() < deadline:
            if self._closing:
                # Stopping: nobody is left to receive this cycle. What is still
                # in flight goes with its handle in the disconnect that follows.
                return {}
            now = time.monotonic()
            # 1) handles still being set up → issue their read once ready
            if creating:
                still_c: list[tuple[str, int]] = []
                for name, handle in creating:
                    try:
                        st = _libplctag.plc_tag_status(handle)
                    except Exception as e:
                        _fail(name, str(e), drop=True)
                        continue
                    if st == PLCTAG_STATUS_PENDING:
                        still_c.append((name, handle))
                    elif st == PLCTAG_STATUS_OK:
                        try:
                            _classify(name, handle, _libplctag.plc_tag_read(handle, 0))
                        except Exception as e:
                            _fail(name, str(e), drop=True)
                    else:
                        # Setup itself failed (unknown tag name, bad path) — the
                        # handle is useless, drop it so it can be rebuilt later.
                        _fail(name, _decode(st), drop=True, rc=st)
                creating = still_c
            # 2) reads in flight
            if pending:
                still: list[tuple[str, int]] = []
                for name, handle in pending:
                    try:
                        st = _libplctag.plc_tag_status(handle)
                    except Exception as e:
                        _fail(name, str(e), drop=True)
                        continue
                    if st == PLCTAG_STATUS_OK:
                        phys_ok[name] = handle
                        self._inflight.pop(name, None)
                        n_replies += 1
                        t_last_reply = now
                    elif st == PLCTAG_STATUS_PENDING:
                        sent = self._inflight.get(name)
                        if sent is not None and now - sent > lost_after:
                            # Cancelled now rather than at the deadline: waiting
                            # for it is what kept every cycle at full length.
                            self._cancel_request(name, handle)
                            lost_now.append((name, now - sent))
                            _fail(name, _LOST_REPLY, drop=False)
                        else:
                            still.append((name, handle))
                    else:
                        self._inflight.pop(name, None)
                        _fail(name, _decode(st), drop=st not in _SOFT_READ_ERRS, rc=st)
                pending = still
            if pending or creating:
                # Every pass costs one library call — and one tag mutex — per
                # outstanding tag, so spinning at a flat 2 ms makes a big batch
                # fight the very I/O threads it is waiting on. Scale the pause
                # with the outstanding set: small batches stay snappy, large
                # ones back off to 10 ms (a tail cost far below their own RTT).
                outstanding = len(pending) + len(creating)
                time.sleep(min(0.010, max(0.002, outstanding / 100000.0)))

        # Still unfinished at the deadline → report a timeout for THIS cycle but
        # KEEP the handle. The read is in flight and normally lands a few ms
        # later, so the next cycle collects it with no re-creation at all — that
        # is what stops one slow cycle from cascading into a rebuild storm.
        # The request keeps its clock (_inflight): if it never lands, the
        # lost-reply check above gives up on it a few deadlines later.
        timed_out = bool(pending or creating)
        for name, _h in pending:
            _fail(name, "read timeout", drop=False)
        for name, _h in creating:
            _fail(name, "tag setup timeout", drop=False)

        # ── Refusals ────────────────────────────────────────────────────────
        # A refused block counts against its size only while the controller
        # answers other requests of the cycle. One that refuses EVERYTHING — the
        # PLC behind a live gateway gone, a program download under way — made
        # every block "refused" at once: three such cycles cut every file down
        # to single requests and switched the I/O blocks off, and once the PLC
        # was back the Silo trace needed 224 requests instead of 21, ran into
        # the deadline and kept most of its tags in error for the twenty
        # minutes the sizes took to grow back. When the refusals of a cycle
        # have nothing to compare with — a watch list that is all blocks — the
        # next cycle adds one single-word read (_SLC_PROBE): the controller
        # answering that one is what makes the refusals count.
        if refused_now:
            if phys_ok:
                for name, msg in refused_now:
                    _count_refusal(name, msg)
            elif not self._refused_all_logged:
                self._refused_all_logged = True
                self.log.warning(
                    f"none of the {len(phys_names)} request(s) of this cycle got a normal "
                    f"answer ({len(refused_now)} block(s) refused: {refused_now[0][1]}) — block "
                    f"sizes stay as they are until the controller answers again (PLC "
                    f"behind the gateway unreachable, or a program download under way?)")
        if phys_ok:
            self._refused_all_logged = False
        self._slc_probe_next = bool(refused_now) and not any(n != _SLC_PROBE for n in phys_ok)

        # ── How fast the link answers ───────────────────────────────────────
        # Time to the last reply over the replies that came: what one request
        # costs on this link right now. It stays true when the deadline cut the
        # cycle short — 23 replies in 3.36 s are 146 ms each — which is exactly
        # when the length of the cycle stops saying anything.
        if n_replies:
            self._note_req_time((t_last_reply - _dbg_t0) / n_replies)
        elif timed_out:
            # Not one reply inside the whole budget: a request takes at least
            # that long. Shown as such, but not taught to the deadline — a lost
            # reply must not stretch every cycle that follows.
            self.last_req_ms = budget * 1000.0
        if lost_now:
            self._log_lost(lost_now)
        if io_check:
            self._io_settle(io_check, phys_ok, io_slots)

        # ── Assemble results under the ORIGINAL requested keys ─────────────
        for name in direct:
            hit = _hit(name)
            if hit:
                results[name] = self._extract_result(hit[0], name, hit[1])
            else:
                results[name] = _err_of(name) or {
                    "value": None, "type": "?", "error": "read failed", "ts": time.time()}

        for name, (word_name, bit) in bit_map.items():
            hit = _hit(word_name)
            if hit:
                results[name] = self._extract_bit(hit[0], bit, is_logix, hit[1])
            else:
                e = _err_of(word_name)
                results[name] = dict(e) if e else {
                    "value": None, "type": "?", "error": "read failed", "ts": time.time()}

        for name, (struct_key, member, stype) in struct_map.items():
            if struct_key in phys_ok:
                results[name] = self._extract_struct_member(
                    phys_ok[struct_key], member, stype)
            else:
                e = phys_err.get(struct_key)
                results[name] = dict(e) if e else {
                    "value": None, "type": "?", "error": "read failed", "ts": time.time()}

        # ── Name the failures ───────────────────────────────────────────────
        # A bad address used to be invisible in the log ("ok=12 err=4") while the
        # tracer just showed an empty cell. Log each failing tag once; stay quiet
        # until its error text changes or it starts reading again.
        if phys_err:
            fresh = []
            for n, e in phys_err.items():
                msg = str(e.get('error'))
                # a lost reply has its own line (_log_lost)
                if self._err_seen.get(n) != msg and msg != _LOST_REPLY:
                    fresh.append(f"{_phys_label(n)} → {msg}")   # N7:0 ×28
                self._err_seen[n] = msg
            if fresh:
                more = f" (+{len(fresh) - 8} more)" if len(fresh) > 8 else ""
                self.log.warning(f"read failed for {len(fresh)} tag(s): "
                            f"{'; '.join(fresh[:8])}{more}")
        for n in phys_ok:
            self._err_seen.pop(n, None)
            self._lost_streak.pop(n, None)
            self._slc_refused.pop(n, None)
        if self._slc_probing:
            self._slc_settle_probes(phys_ok)

        if DEBUG_SAVE:
            # Post-read summary: timing + handle-cache reuse for this batch.
            # hits  = handles served from cache (reused, no new CIP/PCCC tag)
            # creates = brand-new handles built this batch (first-seen addresses)
            dt_ms = (time.monotonic() - _dbg_t0) * 1000.0
            hits = self._dbg_handle_hits - _dbg_hits0
            creates = self._dbg_handle_creates - _dbg_creates0
            self.log.info(
                f"[DBG-SAVE] read done: {len(phys_names)} physical reads in "
                f"{dt_ms:.0f} ms | handle_cache reuse={hits} new={creates} "
                f"(total cached={len(self._handles)}) | "
                f"ok={len(phys_ok)} err={len(phys_err)}")

        return results

    # ── pace of the link ---------------------------------------------------
    def _read_budget(self, n_req: int) -> float:
        """Batch read deadline: twice what the link needs at its measured pace.

        A fixed 120 ms per request cut every cycle short the moment the link got
        slower than that — another master on the gateway was enough — and the
        tail of the queue was reported as timeouts while its replies were on the
        way. Sized from the measured pace, a slow link makes a longer cycle
        instead: every value arrives, only less often. The last cycle makes it
        react to a slowdown at once, the running average lets it relax
        gradually. Until the first measurement the old per-request guess holds.
        """
        s = max(self._req_s_last, self._req_s_avg)
        per_req = _READ_BUDGET_MARGIN * s if s > 0 else _READ_BUDGET_PER_REQ
        return max(_LIBPLCTAG_TIMEOUT / 1000.0,
                   min(_READ_BUDGET_MAX, per_req * n_req))

    def _note_req_time(self, s: float):
        """Record one cycle's seconds per request (see _read_budget)."""
        self._req_s_last = s
        self._req_s_avg = (s if self._req_s_avg <= 0
                           else self._req_s_avg + _REQ_AVG_ALPHA * (s - self._req_s_avg))
        self.last_req_ms = s * 1000.0

    # ── lost replies -------------------------------------------------------
    def _cancel_request(self, name: str, handle: int):
        """Give up on a request whose reply is not coming.

        plc_tag_abort keeps the handle, so the next cycle simply asks again. A
        second loss in a row on the same tag — or a DLL without abort — rebuilds
        the handle instead: whatever wedged it does not survive a fresh tag.
        """
        self._inflight.pop(name, None)
        again = self._lost_streak.get(name, 0)
        self._lost_streak[name] = again + 1
        if not again and _LIBPLCTAG_HAS_ABORT:
            try:
                if _libplctag.plc_tag_abort(handle) == PLCTAG_STATUS_OK:
                    return
            except Exception:
                pass
        self._drop_handle(name)

    def _log_lost(self, lost: list):
        self.lost_replies += len(lost)
        shown = "; ".join(f"{_phys_label(n)} after {age:.0f} s" for n, age in lost[:8])
        more = f" (+{len(lost) - 8} more)" if len(lost) > 8 else ""
        self.log.warning(
            f"reply lost for {len(lost)} request(s): {shown}{more} — cancelled, "
            f"asked again next cycle ({self.lost_replies} lost on this connection; "
            f"a count that keeps growing points at the link itself)")

    # ── SLC block size ------------------------------------------------------
    def _slc_size_text(self, key) -> str:
        if key in self._slc_no_block:
            return "one request per address"
        return f"{self._slc_block_cap.get(key, _SLC_BLOCK_BYTES)}-byte blocks"

    def _slc_drop_refusals(self, key):
        """Forget the refusal counts of one file — its block keys just changed."""
        for k in [k for k in self._slc_refused if _slc_block_file(k) == key]:
            del self._slc_refused[k]

    def _slc_shrink(self, key, msg: str, refused: int, probing: bool,
                    blk_bytes: int = _SLC_BLOCK_BYTES):
        """One step down for a file whose blocks the controller keeps refusing.

        The step is half of the refused block, not half of the file's limit: a
        short run that crosses the end of the data file (an address the PLC
        does not have) fits under every smaller limit, so halving the limit
        took five rounds of refusals — all the while failing the valid
        addresses inside that run — before reaching one request per address.
        """
        name = f"{key[0]}{key[1]}"
        tried = self._slc_size_text(key)
        cap = min(self._slc_block_cap.get(key, _SLC_BLOCK_BYTES), blk_bytes) // 2
        if cap >= _SLC_BLOCK_BYTES_MIN:
            self._slc_block_cap[key] = cap
        else:
            self._slc_no_block.add(key)
        self._slc_shrunk_at[key] = time.monotonic()
        self._slc_drop_refusals(key)
        if probing:
            # The size just given back is refused again: back down at once, and
            # wait twice as long before the next try.
            self._slc_probing.discard(key)
            wait = min(_SLC_REGROW_MAX_S, 2 * self._slc_regrow_wait.get(key, _SLC_REGROW_S))
            self._slc_regrow_wait[key] = wait
            self.log.warning(f"SLC {name} still refuses {tried} ({msg}) — back to "
                             f"{self._slc_size_text(key)}, next try in {wait / 60:.0f} min")
        elif cap >= _SLC_BLOCK_BYTES_MIN:
            self.log.warning(f"SLC block read refused on {name} ({msg}, {refused} "
                             f"cycles in a row) — retrying with {cap}-byte blocks")
        else:
            self.log.warning(f"SLC block read failed on {name} ({msg}, {refused} "
                             f"cycles in a row) — falling back to one request per "
                             f"address for this file")

    def _slc_regrow(self):
        """Give a cut-down file its previous block size back after a quiet spell.

        A refusal on a busy line can be a passing thing, while a size lost for
        good is paid for in requests every cycle until Reconnect. So every
        _SLC_REGROW_S the file tries one size up; the first refusal of that try
        sends it straight back and doubles the wait — a controller that really
        will not serve big blocks costs one refused read now and then.
        """
        now = time.monotonic()
        for key in set(self._slc_block_cap) | self._slc_no_block:
            if key in self._slc_probing:
                continue
            wait = self._slc_regrow_wait.get(key, _SLC_REGROW_S)
            if now - self._slc_shrunk_at.setdefault(key, now) < wait:
                continue
            if key in self._slc_no_block:
                self._slc_no_block.discard(key)
            else:
                cap = self._slc_block_cap[key] * 2
                if cap >= _SLC_BLOCK_BYTES:
                    del self._slc_block_cap[key]
                else:
                    self._slc_block_cap[key] = cap
            self._slc_shrunk_at[key] = now
            self._slc_probing.add(key)
            self._slc_drop_refusals(key)
            self.log.info(f"SLC {key[0]}{key[1]}: no refusals for {wait / 60:.0f} min "
                          f"— trying {self._slc_size_text(key)} again")

    def _slc_settle_probes(self, phys_ok: dict):
        """A size given back by _slc_regrow stays after one clean block read."""
        read_ok = {_slc_block_file(n) for n in phys_ok}
        for key in list(self._slc_probing):
            if key not in read_ok:
                continue                      # not read this cycle — still open
            self._slc_probing.discard(key)
            self._slc_regrow_wait.pop(key, None)   # next step waits the normal time
            if key not in self._slc_block_cap and key not in self._slc_no_block:
                self._slc_shrunk_at.pop(key, None)  # full size again
            self.log.info(f"SLC {key[0]}{key[1]}: {self._slc_size_text(key)} accepted")

    # ── SLC I/O image in block reads -------------------------------------
    def _io_reset(self):
        """Forget every I/O block check — new layout, mode switched, new link."""
        self._io_off: dict = {}         # (prefix, file) → why blocks are off
        self._io_ok: dict = {}          # (prefix, file) → words a check confirmed
        self._io_checked_at: dict = {}  # (prefix, file) → monotonic time of it
        self._io_tries: dict = {}       # (prefix, file) → checks without a pass
        self._io_miss: dict = {}        # word → checks in a row it disagreed
        # Bits seen changing on their own between two check cycles, per I/O
        # word, and the last value each read gave — see _io_settle for why a
        # check must not count them. Both live for one run of checks of a file.
        self._io_active: dict = {}      # word → bit mask
        self._io_last: dict = {}        # (word, 'b' | 's') → (value, monotonic time)
        self._io_seq: set = set()       # files in a run of check cycles
        self._io_hint_logged = False

    def _io_layout_text(self) -> str:
        s = self._io_slots
        if not s:
            return ("I/O block read on — no slot layout yet: I/O is read word by "
                    "word until the tracer sends one with its watch list")
        return (f"I/O block read on — layout of {len(s)} slot(s): "
                f"{sum(v[0] for v in s.values())} input / "
                f"{sum(v[1] for v in s.values())} output word(s)")

    def set_io_slots(self, slots):
        """Rack layout the tracer read from its .SLC project.

        `slots` = {slot: [input_words, output_words]} (SCAN_IN / SCAN_OUT of the
        export); None or {} = no layout, I/O is read word by word. It comes with
        every watch_only, so a project loaded in the tracer brings its own — and
        a layout other than the one in use starts the checks over.
        """
        norm = {}
        if isinstance(slots, dict):
            for k, v in slots.items():
                try:
                    s, i, o = int(k), int(v[0]), int(v[1])
                except (TypeError, ValueError, IndexError, KeyError):
                    continue
                if 0 <= s <= 255 and 0 <= i <= 255 and 0 <= o <= 255:
                    norm[s] = (i, o)
        norm = norm or None
        with self._io_mu:
            if norm == self._io_slots:
                return
            self._io_slots = norm
            self._io_reset()
        if getattr(self.cfg, 'io_block', False):
            self.log.info(self._io_layout_text())

    async def update_io_block(self, on: bool):
        async with self._lock:
            await asyncio.get_event_loop().run_in_executor(
                None, self._update_io_block_sync, on)

    def _update_io_block_sync(self, on: bool):
        """Switch I/O block reads on or off on the live connection."""
        on = bool(on)
        if on == bool(getattr(self.cfg, 'io_block', False)):
            return
        self.cfg.io_block = on
        with self._io_mu:
            self._io_reset()
        if on:
            self.log.info(self._io_layout_text())
            return
        blocks = [n for n in self._handles
                  if (_slc_block_file(n) or ('',))[0] in ('I', 'O')]
        for n in blocks:
            self._drop_handle(n)
        self.log.info("I/O block read off — I/O is read word by word"
                      + (f" ({len(blocks)} block handle(s) closed)" if blocks else ""))

    def _io_disable(self, key, why: str):
        """Stop reading one I/O file as a block on this connection."""
        with self._io_mu:
            if key in self._io_off:
                return
            self._io_off[key] = why
            self._io_ok.pop(key, None)
            self._io_tries.pop(key, None)
            self._io_seq.discard(key)
            for w in [w for w in self._io_miss if (_slc_io_word(w) or ())[:2] == key]:
                del self._io_miss[w]
        self._slc_drop_refusals(key)
        self.log.warning(f"I/O block read off for {key[0]}{key[1]}: {why} — its words "
                         f"are read one by one (Reconnect, or switching the mode off "
                         f"and on again, tries the block once more)")

    def _io_plan(self, phys_set, io_slots):
        """This cycle's I/O blocks, and the words to read both ways.

        Returns (blocks, check). `blocks` joins block_of. `check` holds the words
        whose block is not confirmed yet: they are ALSO read on their own this
        cycle, their values come from those single reads, and _io_settle then
        compares the two. Until a block has matched, it never feeds a value.
        """
        if not io_slots:
            if not self._io_hint_logged and any(_slc_io_word(n) for n in phys_set):
                self._io_hint_logged = True
                self.log.info("I/O block read is on, but no slot layout came with the "
                              "watch list (older tracer, or a project without the .SLC "
                              "I/O configuration) — reading I/O word by word")
            return {}, {}
        with self._io_mu:
            plan = _plan_slc_io_blocks(phys_set, io_slots, set(self._io_off))
            words_of = {}
            for w, (bkey, _off) in plan.items():
                words_of.setdefault(_slc_block_file(bkey), set()).add(w)
            now = time.monotonic()
            blocks, check = {}, {}
            for key, words in words_of.items():
                due = (not words <= self._io_ok.get(key, set())
                       or now - self._io_checked_at.get(key, 0.0) >= _IO_RECHECK_S)
                if due and key not in self._io_seq:
                    # A new run of checks learns its moving bits afresh: what
                    # moved a minute ago — a machine starting up, say — says
                    # nothing about the two reads about to be compared.
                    self._io_seq.add(key)
                    for w in words:
                        self._io_active.pop(w, None)
                        self._io_last.pop((w, 'b'), None)
                        self._io_last.pop((w, 's'), None)
                target = check if due else blocks
                for w in words:
                    target[w] = plan[w]
        return blocks, check

    def _io_note(self, word: str, src: str, value: int) -> int:
        """Remember one read of an I/O word ('b' block, 's' its own read) and
        return the bits it was seen changing on since the previous check cycle
        (call under _io_mu). Only reads close together count — after a pause
        the old value says nothing about what moves now."""
        now = time.monotonic()
        prev = self._io_last.get((word, src))
        self._io_last[(word, src)] = (value, now)
        moving = self._io_active.get(word, 0)
        if prev is not None and prev[0] != value and now - prev[1] <= _IO_MOVE_WINDOW_S:
            moving |= prev[0] ^ value
            self._io_active[word] = moving
        return moving

    def _io_settle(self, check, phys_ok, io_slots):
        """Compare this cycle's I/O blocks with the same words read one by one.

        The layout comes from the project file loaded in the tracer. A stale
        .SLC shifts every word after the first card it gets wrong — silently,
        the values would just belong to another card. So a block is trusted only
        once all its words agree with their own reads in one cycle, and a word
        disagreeing _IO_CHECK_MISMATCHES checks in a row turns block reads off
        for its file (one disagreement can be an input that changed between the
        two reads). A confirmed block is compared again every _IO_RECHECK_S and
        whenever new words join it.

        The two reads of a word are a whole cycle apart at worst — on a serial
        line about a second — and a running plant changes inputs that fast: a
        speed switch, a flasher, an analog card. Compared as whole words, such a
        word disagreed check after check and a correct layout was switched off
        as "not matching this controller". So a bit counts against the layout
        only while BOTH reads have held it since the previous check cycle: one
        that changed in between, in either read, was simply moving. A layout
        that is off by a card keeps showing — the word read in the block belongs
        to another card, and the two disagree on bits that hold still.

        A check counts as failed only on evidence against the block: a word that
        disagrees, or one the controller answered on its own while the block
        stayed unanswered. A cycle in which none of the file's words came back
        says nothing about the block — the link or the PLC is down — and every
        such cycle used to count: an outage still going on _IO_CHECK_MAX_TRIES
        cycles after a re-check fell due (about a minute and a half in all)
        switched the file to word-by-word reads until the next Reconnect.
        """
        with self._io_mu:
            if io_slots is not self._io_slots:
                return                    # a new layout arrived while this ran
            now = time.monotonic()
            files = {}
            for w, (bkey, off) in check.items():
                files.setdefault(_slc_block_file(bkey), []).append((w, bkey, off))
            for key, items in files.items():
                if key in self._io_off:
                    continue              # the controller refused it this cycle
                agreed, worst, against = 0, None, False
                for w, bkey, off in items:
                    hb, hs = phys_ok.get(bkey), phys_ok.get(w)
                    if hs is None:
                        continue          # the word's own read failed — no verdict
                    if hb is None:
                        against = True    # the controller answered the word, not the block
                        continue
                    try:
                        vb = _libplctag.plc_tag_get_uint16(hb, off)
                        vs = _libplctag.plc_tag_get_uint16(hs, 0)
                    except Exception:
                        continue
                    self._io_note(w, 'b', vb)
                    moving = self._io_note(w, 's', vs)
                    if not (vb ^ vs) & ~moving & 0xFFFF:
                        agreed += 1
                        self._io_miss.pop(w, None)
                        continue
                    against = True
                    n = self._io_miss.get(w, 0) + 1
                    self._io_miss[w] = n
                    if worst is None or n > worst[3]:
                        worst = (w, vb, vs, n)
                if agreed == len(items):
                    if key not in self._io_ok:
                        n_req = len({b for _w, b, _o in items})
                        self.log.info(f"I/O block read {key[0]}{key[1]}: {len(items)} "
                                      f"word(s) in {n_req} request(s) — matches "
                                      f"word-by-word reads")
                    self._io_ok[key] = {w for w, _b, _o in items}
                    self._io_checked_at[key] = now
                    self._io_tries.pop(key, None)
                    self._io_seq.discard(key)     # this run of checks is over
                    continue
                if not against:
                    continue              # nothing came back to judge the block by
                tries = self._io_tries.get(key, 0) + 1
                self._io_tries[key] = tries
                if worst and worst[3] >= _IO_CHECK_MISMATCHES:
                    self._io_disable(key, (
                        f"{worst[0]} reads {worst[1]:#06x} inside the block but "
                        f"{worst[2]:#06x} on its own, {worst[3]} checks in a row — "
                        f"the slot layout of the project loaded in the tracer does "
                        f"not match this controller"))
                elif tries >= _IO_CHECK_MAX_TRIES:
                    self._io_disable(key, f"the block could not be confirmed in "
                                          f"{tries} cycles")

    @staticmethod
    def _promote_bit(name: str, is_logix: bool):
        """Decide whether `name` is a bit of an atomic integer word.

        Returns (word_name, bit_index) when the bit can be read by fetching its
        parent word, or None when the address must be read directly (single
        tags, whole words, and structure bits like .DN/.EN/T4:0/DN/T4:0/13).

        The returned word_name resolves to the exact same libplctag connection
        string the legacy per-bit path would have used for that bit, so bit
        extraction is identical — only the physical read is now shared.
        """
        if is_logix:
            # Logix bit selector: "Tag.N" / "Local:1:I.Data.N" / "Arr[5].N".
            # Logix arrays use [] indexing, so a trailing ".N" (0..31) is always
            # a bit — matching _infer_logix_type's existing dint_bit handling.
            m = _LGX_BIT_SEL_RE.match(name)
            if not m:
                return None
            bit = int(m.group('bit'))
            if not (0 <= bit <= 31):
                return None
            return (m.group('base'), bit)

        # SLC / MicroLogix
        parsed = _slc_parse_addr(name)
        if not parsed:
            return None
        prefix, file_num, element, bit, sub = parsed
        if bit is None:
            return None                       # /DN, .ACC, bare word — not a numeric bit
        if prefix in ('T', 'C', 'R'):
            return None                       # timer/counter/control structures
        if prefix in ('I', 'O'):
            # I/O module word: I:<slot>.<word> (sub carries the word as 'W<n>')
            word_idx = int(sub[1:]) if (isinstance(sub, str) and sub.startswith('W')) else 0
            word_name = f"{prefix}:{element}.{word_idx}"
        else:
            # File word: B3:0, N7:0, S2:1, …  (canonical "<prefix><file>:<elem>")
            word_name = f"{prefix}{file_num}:{element}"
        return (word_name, bit)

    def _promote_struct_member(self, name: str, is_logix: bool):
        """Decide whether `name` is a member of an atomic TIMER/COUNTER/CONTROL.

        Returns (base, member, struct_type) when `name` is `Base.MEMBER` and the
        L5X type hint for `Base` is TIMER / COUNTER / CONTROL and MEMBER is a
        valid member of that structure. Otherwise None (the tag is read directly).

        Promoting timer members lets the bridge read the whole 12-byte structure
        ONCE per cache window and extract every member (.PRE/.ACC/.EN/.TT/.DN …)
        from that single snapshot. This guarantees the members are mutually
        consistent — the historic "Save Online Project" bug, where a running
        timer's .DN disagreed with its .ACC because the members were split across
        two read batches read milliseconds apart, can no longer occur.
        """
        if not is_logix or '.' not in name:
            return None
        base, member = name.rsplit('.', 1)
        member = member.upper()
        # Need an explicit TIMER/COUNTER/CONTROL hint for the base tag — without
        # it we cannot assume the 12-byte layout (a UDT could also have a .DN).
        hint = self._type_hints.get(base)
        if hint is None and base.startswith('Program:'):
            parts = base.split('.', 1)
            if len(parts) == 2:
                hint = self._type_hints.get(parts[1])
        if not hint:
            return None
        stype = _re.sub(r'\[.*$', '', hint.strip().upper())
        if stype not in _LGX_STRUCT_BITS:
            return None
        # Member must be a real DINT word or a status bit of this structure type.
        if member in _LGX_STRUCT_DINT_OFF or member in _LGX_STRUCT_BITS[stype]:
            return (base, member, stype)
        return None

    @staticmethod
    def _extract_struct_member(handle: int, member: str, stype: str) -> dict:
        """Extract one member from an already-read 12-byte TIMER/COUNTER handle.

        DINT members (.PRE/.ACC/.LEN/.POS) come from their byte offset; status
        bits (.EN/.TT/.DN/.CU/…) from the control DINT at offset 0. Because all
        members are pulled from the SAME physical read, they are guaranteed to
        describe one consistent instant on the PLC.
        """
        member = member.upper()
        try:
            if member in _LGX_STRUCT_DINT_OFF:
                off = _LGX_STRUCT_DINT_OFF[member]
                v = int(_libplctag.plc_tag_get_int32(handle, off))
                return {"value": v, "type": "int", "error": None, "ts": time.time()}
            bit = _LGX_STRUCT_BITS.get(stype, {}).get(member)
            if bit is None:
                return {"value": None, "type": "?",
                        "error": f"unknown member .{member}", "ts": time.time()}
            v = bool(_libplctag.plc_tag_get_bit(handle, bit))
            return {"value": v, "type": "bool", "error": None, "ts": time.time()}
        except Exception as e:
            return {"value": None, "type": "?", "error": str(e), "ts": time.time()}

    @staticmethod
    def _extract_bit(handle: int, bit: int, is_logix: bool, base_off: int = 0) -> dict:
        """Extract a single bit from an already-read parent-word handle.

        Mirrors the legacy per-bit extractors exactly:
          • Logix → plc_tag_get_bit (respects the tag's real buffer size)
          • SLC   → (uint16 word >> bit) & 1
        """
        try:
            if is_logix:
                rc = int(_libplctag.plc_tag_get_bit(handle, bit))
                if rc < 0:
                    # Bit past the end of the parent element — report it instead
                    # of letting bool(<negative error code>) read as True.
                    err = _libplctag.plc_tag_decode_error(rc)
                    return {"value": None, "type": "?",
                            "error": f"bit {bit} out of range: "
                                     f"{err.decode() if err else rc}",
                            "ts": time.time()}
                v = bool(rc)
            else:
                word = _libplctag.plc_tag_get_uint16(handle, base_off)
                v = bool((word >> bit) & 1)
            return {"value": v, "type": "bool", "error": None, "ts": time.time()}
        except Exception as e:
            return {"value": None, "type": "?", "error": str(e), "ts": time.time()}

    def _extract_result(self, handle: int, name: str, base_off: int = 0) -> dict:
        try:
            meta = self._handle_meta.get(name, {})
            val = _read_tag_value(handle, name, meta, base_off)
            vtype = type(val).__name__ if val is not None else "?"
            return {"value": val, "type": vtype, "error": None, "ts": time.time()}
        except Exception as e:
            return {"value": None, "type": "?", "error": str(e), "ts": time.time()}

    # ── handle cache ------------------------------------------------------
    def _get_or_create(self, name: str) -> tuple:
        """Return (handle, is_new); (None, False) when the address is unusable.

        `is_new` means the handle was just created and libplctag may still be
        setting it up — the caller must wait for plc_tag_status() to leave
        PENDING before issuing a read.

        Creation is NON-BLOCKING (timeout=0). A blocking create cost one full
        round-trip per first-seen tag, serially, so a watch list that suddenly
        grew by a few hundred tags (opening a cross reference on a big project)
        froze the whole poll cycle before a single read went out. Now libplctag
        sets every new tag up concurrently on its own I/O thread.
        """
        if name in self._handles:
            self._dbg_handle_hits += 1
            return self._handles[name], False
        # SLC block handle: one request covering a run of a data file.
        blk = _slc_parse_block_key(name)
        if blk is not None:
            first, esz, count = blk
            path = _slc_tag_path(self.cfg.ip, self.cfg.slot, first)
            if not path:
                return None, False
            # _slc_tag_path always emits elem_count=1 — widen it, keeping the
            # element size it chose for this file type.
            path = path.replace("&elem_count=1", f"&elem_count={count}", 1)
            path = _with_conn_group(path, self._group_for(first))
            tag = _libplctag.plc_tag_create(path.encode('utf-8'), 0)
            if tag < 0:
                return None, False
            self._handles[name] = tag
            self._handle_meta[name] = {'kind': 'slc', 'block': True,
                                       'elem_size': esz, 'elem_count': count}
            self._dbg_handle_creates += 1
            return tag, True
        # Atomic TIMER/COUNTER/CONTROL structure handle (sentinel-keyed):
        # read the whole 12-byte element so every member shares one snapshot.
        if name.endswith(_LGX_STRUCT_SENTINEL):
            base = name[:-len(_LGX_STRUCT_SENTINEL)]
            cache_ms = int(max(0, self.cfg.poll_interval * 1000 * 0.8))
            path = _with_conn_group(
                _logix_tag_path(self.cfg, base, _LGX_STRUCT_SIZE, cache_ms),
                self._group_for(name))
            tag = _libplctag.plc_tag_create(path.encode('utf-8'), 0)
            if tag < 0:
                return None, False
            self._handles[name] = tag
            self._handle_meta[name] = {'kind': 'lgx', 'reader': 'struct',
                                       'elem_size': _LGX_STRUCT_SIZE}
            self._dbg_handle_creates += 1
            return tag, True
        type_hint = self._type_hints.get(name)
        # Also try base name without "Program:Prog." prefix for hint lookup
        if type_hint is None and name.startswith('Program:'):
            parts = name.split('.', 1)
            if len(parts) == 2:
                type_hint = self._type_hints.get(parts[1])
        # 80% of poll_interval, bounded — lets libplctag dedupe rapid re-reads
        cache_ms = int(max(0, self.cfg.poll_interval * 1000 * 0.8))
        path, meta = _build_tag_path(self.cfg, name, type_hint, cache_ms,
                                     group=self._group_for(name))
        if not path:
            return None, False
        tag = _libplctag.plc_tag_create(path.encode('utf-8'), 0)
        if tag < 0:
            return None, False
        self._handles[name] = tag
        self._handle_meta[name] = meta or {}
        self._dbg_handle_creates += 1
        return tag, True

    def _drop_handle(self, name: str):
        h = self._handles.pop(name, None)
        self._handle_meta.pop(name, None)
        self._inflight.pop(name, None)      # destroying a tag cancels its request
        if h is not None:
            try:
                _libplctag.plc_tag_destroy(h)
            except Exception:
                pass

    # ── tag list browse --------------------------------------------------
    async def get_tag_list(self) -> list:
        if self.cfg.controller_type == 'logix':
            return await asyncio.get_event_loop().run_in_executor(
                None, self._logix_tag_list_sync)
        # SLC: no tag browse, return standard hint list
        return [
            {"name": "S:0", "type": "INT",  "desc": "Status File — major fault word"},
            {"name": "I:0", "type": "BOOL", "desc": "Input file slot 0"},
            {"name": "O:0", "type": "BOOL", "desc": "Output file slot 0"},
            {"name": "B3:0","type": "BOOL", "desc": "Bit file 3"},
            {"name": "N7:0","type": "INT",  "desc": "Integer file 7"},
            {"name": "T4:0","type": "TIMER","desc": "Timer file 4"},
            {"name": "C5:0","type": "CTR",  "desc": "Counter file 5"},
            {"name": "F8:0","type": "FLOAT","desc": "Float file 8"},
        ]

    def _logix_tag_list_sync(self) -> list:
        """Best-effort Logix tag browse via `name=@tags` special tag.

        libplctag returns a raw byte buffer; full parsing (tag name + type
        encoding per CIP Vol1 Appendix C) is non-trivial. v1 returns a hint
        based on known tag-type hints collected from the tracer — enough for
        the UI browse pane; real tag-info parsing is a v2 item.
        """
        hints: list = []
        for name, dt in sorted(self._type_hints.items()):
            hints.append({"name": name, "type": dt, "desc": ""})
        if hints:
            return hints
        return [
            {"name": "(no tag hints received yet)", "type": "-", "desc":
             "Tracer will push tagTypes when a project is loaded."}
        ]


# ─────────────────────────────────────────────────────────────────────────────
def create_connection(cfg: Config, logger: logging.Logger | None = None):
    """Factory: single libplctag backend for all supported controller families."""
    if not LIBPLCTAG_OK:
        raise RuntimeError("libplctag is not available — place plctag.dll in "
                           "Bridge/libplctag_2.6.16_windows_x64/")
    lg = logger or log
    if cfg.controller_type == 'logix':
        lg.info("Backend: libplctag (CIP / ControlLogix)")
    else:
        lg.info("Backend: libplctag (PCCC / SLC / MicroLogix)")
    return LibplctagConnection(cfg, lg)


# ─────────────────────────────────────────────────────────────────────────────
# What a request whose reply never came reports once it is cancelled.
_LOST_REPLY = "reply lost"

# Read results that mean "this cycle ran out of time", not "this tag is bad".
_TIMEOUT_ERRS = ("read timeout", "tag setup timeout", _LOST_REPLY)

# When to complain about the cycle length. Not "longer than the poll interval":
# a controller reached over a serial gateway cannot serve a large watch list in
# 200 ms and never will, so that comparison would fire every single cycle and
# bury the log. This is the level where something is actually wrong.
_CYCLE_WARN_MS = 3000.0

# Batch read deadline: at least _LIBPLCTAG_TIMEOUT, at most _READ_BUDGET_MAX,
# in between _READ_BUDGET_MARGIN times the measured time per request (see
# LibplctagConnection._read_budget). Before the first measurement it assumes
# _READ_BUDGET_PER_REQ — sized for the slowest link the bridge is used on, a PLC
# serial port behind a COM-to-Ethernet converter.
_READ_BUDGET_PER_REQ = 0.12   # seconds per request, until measured
_READ_BUDGET_MARGIN = 2.0
_READ_BUDGET_MAX = 10.0
_REQ_AVG_ALPHA = 0.3          # weight of the newest cycle in the running average

# A request unanswered for this many deadlines, and at least this long, is
# taken for lost and cancelled.
_LOST_REPLY_DEADLINES = 3
_LOST_REPLY_MIN_S = 10.0

# Nobody to show the values to → nothing to read them for. A tab that closes
# leaves its watch list behind, and a forgotten bridge used to go on polling it
# for good, sharing a slow serial line with the bridge that IS being watched.
_IDLE_PAUSE_S = 10.0      # no client this long → pause (outlasts a page reload)
_HTTP_CLIENT_S = 30.0     # an HTTP reader counts as a client this long after a call
_RESUME_SETTLE_S = 0.5    # a returning tab sends its watch list before the first read


class TagPoller:
    def __init__(self, conn: LibplctagConnection, cfg: Config,
                 logger: logging.Logger | None = None):
        self.conn = conn
        self.cfg  = cfg
        self.log  = logger or log
        self.watched = set(cfg.watched_tags)
        self.values  = {}
        self.subscribers = set()
        self._running = False
        # ── Cycle telemetry (read by the GUI) ─────────────────────────────
        # How long one full read of the watch set actually takes. When it grows
        # past the poll interval the bridge is saturated: the tracer is asking
        # for more tags than the controller can serve at that rate, which is
        # what a wall of "read timeout" errors really means. The interval is
        # NEVER changed automatically — this only reports the fact.
        self.last_cycle_ms   = 0.0     # duration of the most recent read
        self.avg_cycle_ms    = 0.0     # rolling average over the last 10 reads
        self.last_timeouts   = 0       # tags that ran out of time last cycle
        self.last_tag_count  = 0       # watch-list size read last cycle
        self._cycle_hist     = collections.deque(maxlen=10)
        self._overrun_logged = 0.0     # monotonic ts of the last overrun warning
        # Why the poll loop stopped; "" while it is healthy. status() surfaces
        # it so a dead poller cannot keep passing for a live link.
        self.crashed         = ""
        # Polling stops while nobody is attached (see _IDLE_PAUSE_S): a tab on
        # the WebSocket, or an HTTP reader of /values or /watch lately.
        self.paused          = False
        self._client_seen    = time.monotonic()
        self._http_seen      = float("-inf")

    def subscribe(self, ws): self.subscribers.add(ws)
    def unsubscribe(self, ws): self.subscribers.discard(ws)
    def watch(self, tags): self.watched.update(tags)
    def unwatch(self, tags): self.watched -= set(tags)

    def touch_http(self):
        """An HTTP reader asked for values — it counts as a client for a while."""
        self._http_seen = time.monotonic()

    def _has_client(self, now: float) -> bool:
        return bool(self.subscribers) or now - self._http_seen < _HTTP_CLIENT_S

    async def run(self):
        """Guarded entry point for the poll loop.

        The task this runs in is created and never awaited, so an exception
        escaping the loop used to end it in silence: `connected` stayed True,
        the GUI and /sessions kept printing CONNECTED, the browser tab kept its
        socket open, and the only symptom was values that never changed again.
        Anything that escapes is now logged with its traceback, marks the
        session down and is pushed to every client, so a stopped poll loop
        reads as a dead link everywhere instead of as fresh data that happens
        to be frozen.
        """
        self._running = True
        self.crashed  = ""
        self.log.info("Poller started")
        try:
            await self._poll_loop()
        except asyncio.CancelledError:
            raise                      # ordinary shutdown via session.stop()
        except BaseException as e:
            # Reported, not re-raised: the task object is never awaited, so a
            # re-raise would only reach the interpreter at garbage-collection
            # time, long after the operator needed to know.
            await self._on_crash(e)
        finally:
            self._running = False

    async def _on_crash(self, exc):
        """Say it in the log, mark the session down, tell every client."""
        self.crashed = f"{type(exc).__name__}: {exc}"
        self.log.error(f"poller stopped: {self.crashed} — no further reads "
                       f"until this session is restarted", exc_info=exc)
        try:
            self.conn.connected = False
            self.conn.error = f"poller stopped ({self.crashed})"
        except Exception:
            pass
        # A tab is still holding an open socket onto values that will never
        # change again. Push the loss of the link so it shows DISCONNECTED
        # instead of stale readings. Iterate a copy of the subscriber set:
        # this path exists to report a failure and must not repeat one.
        try:
            payload = json.dumps({
                "type": "status", "connected": False,
                "error": getattr(self.conn, "error", "poller stopped"),
                "controller": getattr(self.conn, "controller_info", {}),
            })
        except Exception:
            return
        for ws in list(self.subscribers):
            try:
                await ws.send(payload)
            except Exception:
                pass

    async def _poll_loop(self):
        while self._running:
            if not self.conn.connected:
                if not self.conn.auto_reconnect:
                    await asyncio.sleep(1)
                    continue
                self.log.info(f"Reconnecting in {RECONNECT}s…")
                await asyncio.sleep(RECONNECT)
                await self.conn.connect()
                continue
            # Nobody attached for a while → stop reading, keep the watch list.
            # The first client back resumes at once — after a short pause that
            # lets a returning tab replace the list before anything is read.
            now = time.monotonic()
            if self._has_client(now):
                self._client_seen = now
                if self.paused:
                    self.paused = False
                    self.log.info(f"client connected — polling resumed "
                                  f"({len(self.watched)} tag(s))")
                    await asyncio.sleep(_RESUME_SETTLE_S)
                    continue
            elif self.watched and now - self._client_seen >= _IDLE_PAUSE_S:
                if not self.paused:
                    self.paused = True
                    # what is cached would be stale by the time anyone looks
                    self.values.clear()
                    self.last_cycle_ms = 0.0
                    self.last_timeouts = 0
                    self.log.info(f"no clients for {_IDLE_PAUSE_S:.0f} s — polling "
                                  f"paused ({len(self.watched)} tag(s) kept)")
                await asyncio.sleep(0.25)
                continue
            # Drop cached values for tags that are no longer watched,
            # so the bridge UI and reconnecting WS clients don't see stale entries.
            # Note: this also drops one-shot `read_now` results on the next tick —
            # that's acceptable since read_now is not meant to populate the cache.
            if self.values:
                stale = [t for t in self.values if t not in self.watched]
                for t in stale:
                    self.values.pop(t, None)
            if self.watched:
                tags = list(self.watched)
                t0 = time.monotonic()
                new_vals = await self.conn.read_tags(tags)
                self._note_cycle(time.monotonic() - t0, len(tags), new_vals)
                changed = {t:d for t,d in new_vals.items()
                           if self.values.get(t,{}).get("value") != d.get("value")
                           or self.values.get(t,{}).get("error")  != d.get("error")}
                self.values.update(new_vals)
                if changed:
                    await self._broadcast({"type":"values","data":changed})
            else:
                self.last_cycle_ms = 0.0
                self.last_timeouts = 0
                self.last_tag_count = 0
            await asyncio.sleep(self.cfg.poll_interval)

    def _note_cycle(self, dt: float, n_tags: int, vals: dict):
        """Record how long this read took and warn when it overruns.

        The interval stays exactly where the user set it — an automatic
        slow-down would hide the overload instead of showing it. The warning is
        rate-limited to one line per 30 s so a saturated bridge does not bury
        its own log.
        """
        self.last_cycle_ms  = dt * 1000.0
        self.last_tag_count = n_tags
        self.last_timeouts  = sum(1 for d in vals.values()
                                  if d.get("error") in _TIMEOUT_ERRS)
        self._cycle_hist.append(self.last_cycle_ms)
        self.avg_cycle_ms = sum(self._cycle_hist) / len(self._cycle_hist)
        if self.last_cycle_ms > _CYCLE_WARN_MS:
            now = time.monotonic()
            if now - self._overrun_logged >= 30.0:
                self._overrun_logged = now
                reads = getattr(self.conn, 'last_phys_reads', 0)
                per_req = getattr(self.conn, 'last_req_ms', 0.0)
                self.log.warning(
                    f"poll cycle {self.last_cycle_ms:.0f} ms for {n_tags} tag(s) "
                    f"in {reads} request(s)"
                    + (f", {per_req:.0f} ms per request" if per_req else "")
                    + (f", {self.last_timeouts} timed out" if self.last_timeouts else "")
                    + " — check the link, the watch list or the block settings")

    async def _broadcast(self, msg):
        payload = json.dumps(msg)
        dead = set()
        for ws in self.subscribers:
            try: await ws.send(payload)
            except: dead.add(ws)
        self.subscribers -= dead

    def stop(self): self._running = False


# ─────────────────────────────────────────────────────────────────────────────
# One PLC = one session = one WebSocket port
# ─────────────────────────────────────────────────────────────────────────────
def _port_free(port: int) -> bool:
    """True when nothing on this machine currently holds the port."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sk:
            sk.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False


class PlcSession:
    """One controller end to end: config, connection, poller, recorder, port.

    Sessions are fully independent — their own libplctag handles, watch set,
    recording and WebSocket endpoint — so a browser tab talks to exactly one
    controller and a PLC that goes down cannot disturb the others.
    """

    def __init__(self, name: str, cfg: Config):
        self.name = (name or cfg.ip or "plc").strip()
        self.slug = _slug(self.name) or "plc"
        self.cfg  = cfg
        self.log  = session_logger(self.slug)
        self.recorder = Recorder(self.slug, self.log)
        self.conn    = None
        self.poller  = None
        self.running = False
        # Set by SessionManager.start() — lets a WS client ask one session for
        # the list of every controller this bridge process serves.
        self.manager = None
        self.started_at = 0.0
        self._ws_srv = None
        self._ptask  = None

    @property
    def ws_url(self) -> str:
        return f"ws://localhost:{self.cfg.port_ws}"

    async def start(self):
        """Bring the session up. Raises if the WS port cannot be taken."""
        if self.running:
            return False
        self.conn   = create_connection(self.cfg, self.log)
        self.poller = TagPoller(self.conn, self.cfg, self.log)
        if WS_OK and websockets:
            # Bind the port BEFORE touching the PLC: a port clash has to fail
            # fast and leave nothing running, while a controller that is merely
            # unreachable is normal — the poller keeps retrying in background.
            self._ws_srv = await websockets.serve(
                lambda ws: ws_handler(ws, self), "0.0.0.0", self.cfg.port_ws)
            self.log.info(f"WebSocket → {self.ws_url}")
        self.running    = True
        self.started_at = time.time()
        await self.conn.connect()
        if not self.running:
            return False        # stopped while connecting — no poller to start
        if self.cfg.slc_path:
            try:
                tags = load_tags_from_slc(self.cfg.slc_path)
                self.poller.watch(tags)
                self.log.info(f"Loaded {len(tags)} addresses from SLC file")
            except Exception as e:
                self.log.warning(f"SLC file load failed: {e}")
        self._ptask = asyncio.create_task(self.poller.run())
        self._ptask.add_done_callback(self._on_poller_done)
        return True

    def _on_poller_done(self, task):
        """Catch a poll task that ended while the session was still up.

        TagPoller.run() reports its own crashes; this covers what it cannot —
        its handler failing, or the loop returning cleanly — so the task can
        never disappear without a word.
        """
        if task.cancelled() or not self.running:
            return                      # stop() / reconnect — expected
        exc = task.exception()
        if exc is not None:
            self.log.error(f"poll task ended unexpectedly: "
                           f"{type(exc).__name__}: {exc}", exc_info=exc)
        elif not (self.poller and self.poller.crashed):
            self.log.error("poll task ended unexpectedly with no error — "
                           "polling has stopped, restart the session")
        if self.poller and not self.poller.crashed:
            self.poller.crashed = (f"{type(exc).__name__}: {exc}" if exc
                                   else "poll task ended unexpectedly")
        if self.conn:
            self.conn.connected = False
            self.conn.error = f"poller stopped ({self.poller.crashed})"                               if self.poller else "poller stopped"

    async def stop(self):
        self.running = False
        if self.conn and hasattr(self.conn, "cancel_reads"):
            # First, or a read in flight runs to its deadline — seconds on a
            # slow line — and holds up the poller, a tab waiting for read_now,
            # and with them every step below.
            self.conn.cancel_reads()
        if self.poller:
            self.poller.stop()
        if self._ptask:
            self._ptask.cancel()
            try:
                await self._ptask
            except (asyncio.CancelledError, Exception):
                pass
            self._ptask = None
        if self._ws_srv:
            self._ws_srv.close()
            try:
                await self._ws_srv.wait_closed()
            except Exception:
                pass
            self._ws_srv = None
        # A recording belongs to the session, not to the bridge: closing the
        # session must close its .ndrec, and only its own.
        self.recorder.stop()
        if self.conn:
            await self.conn.disconnect()
        self.log.info("session stopped")

    async def reconnect(self) -> bool:
        if not self.conn:
            return False
        await self.conn.disconnect()
        old = self.conn
        # Recreate the connection object — the controller family may have
        # changed (SLC <-> Logix) and handles carry their type from creation.
        self.conn = create_connection(self.cfg, self.log)
        # The rack layout came with the tracer's last watch list, and the tab
        # sends the next one only when its view changes — carry it over, or the
        # I/O would be read word by word until then. Its checks start afresh.
        slots = getattr(old, "_io_slots", None)
        if slots and hasattr(self.conn, "set_io_slots"):
            self.conn.set_io_slots({s: list(v) for s, v in slots.items()})
        if self.poller:
            self.poller.conn = self.conn
        return await self.conn.connect()

    def status(self) -> dict:
        """Everything the GUI and /sessions need, in one snapshot."""
        c, p = self.conn, self.poller
        dev = saved_device_for(self.cfg, self.name)
        return {
            "name":       self.name,
            "slug":       self.slug,
            # Preset this session matches in plc_bridge_devices.json ("" when
            # it is an ad-hoc connection). `label` is what a UI prints: the
            # saved name, or the WebSocket port when there is no saved name.
            "device":     (dev["name"] if dev else ""),
            "saved":      bool(dev),
            "label":      (dev["name"] if dev else str(self.cfg.port_ws)),
            "ip":         self.cfg.ip,
            "slot":       self.cfg.slot,
            "type":       self.cfg.controller_type,
            "ws_port":    self.cfg.port_ws,
            "ws_url":     self.ws_url,
            "running":    self.running,
            "connected":  bool(c and c.connected),
            "error":      (c.error if c else ""),
            "controller": (c.controller_info if c else {}),
            "watching":   (len(p.watched) if p else 0),
            # Non-empty once the poll loop has stopped: the port is still open
            # and clients are still attached, so "not connected" alone would
            # read like a link that is merely coming back up.
            "crashed":    (p.crashed if p else ""),
            "clients":    (len(p.subscribers) if p else 0),
            "paused":     bool(p and p.paused),
            "cycle_ms":   (p.last_cycle_ms if p else 0.0),
            "avg_ms":     (p.avg_cycle_ms if p else 0.0),
            "ms_per_req": round(getattr(c, "last_req_ms", 0.0) if c else 0.0, 1),
            "timeouts":   (p.last_timeouts if p else 0),
            "lost":       (getattr(c, "lost_replies", 0) if c else 0),
            "interval":   self.cfg.poll_interval,
            "groups":     (c._effective_groups() if c else 0),
            "reads":      (getattr(c, "last_phys_reads", 0) if c else 0),
            "bytes":      (getattr(c, "last_phys_bytes", 0) if c else 0),
            "rslinx":     bool(self.cfg.via_rslinx),
            "io_block":   bool(getattr(self.cfg, "io_block", False)),
            "recording":  self.recorder.active,
            "rec_file":   self.recorder.file_name,
            "started_at": self.started_at,
        }

    def stats(self) -> dict:
        """Poll telemetry for the browser tab on this session's port: the cycle
        part of status(), same keys, without the bulk — cheap enough to be asked
        for every couple of seconds. Every tab asks over its own socket, so with
        several PLCs each window gets its own controller's numbers."""
        c, p = self.conn, self.poller
        return {
            "plc":       self.name,
            "connected": bool(c and c.connected),
            "watching":  (len(p.watched) if p else 0),
            "paused":    bool(p and p.paused),
            "cycle_ms":  (p.last_cycle_ms if p else 0.0),
            "avg_ms":    (p.avg_cycle_ms if p else 0.0),
            # average time per request in the last cycle — what the header shows
            "ms_per_req": round(getattr(c, "last_req_ms", 0.0) if c else 0.0, 1),
            "timeouts":  (p.last_timeouts if p else 0),
            "lost":      (getattr(c, "lost_replies", 0) if c else 0),
            "interval":  self.cfg.poll_interval,
            "reads":     (getattr(c, "last_phys_reads", 0) if c else 0),
            "bytes":     (getattr(c, "last_phys_bytes", 0) if c else 0),
        }


class SessionManager:
    """Every PLC session running in this process, keyed by name."""

    def __init__(self, base_port: int = DEFAULT_WS, reserved=(DEFAULT_HTTP,)):
        self.sessions: dict = {}          # name → PlcSession (insertion ordered)
        self.base_port = base_port
        # Ports the bridge itself needs; never handed to a PLC session.
        self.reserved = set(reserved)

    # ── lookup ────────────────────────────────────────────────────────────────
    def __len__(self):
        return len(self.sessions)

    def get(self, name: str):
        if not name:
            return None
        s = self.sessions.get(name)
        if s:
            return s
        n = name.strip().lower()
        return next((v for k, v in self.sessions.items() if k.lower() == n), None)

    def default(self):
        """The session an unqualified request means: the only one running."""
        return next(iter(self.sessions.values()), None)

    def names(self) -> list:
        return list(self.sessions.keys())

    def status_all(self) -> list:
        return [s.status() for s in self.sessions.values()]

    def brief_all(self) -> list:
        """Small per-session summary for the browser's connect dialog.

        Deliberately not status(): the dialog only needs identity, address and
        state, and this is sent on every `hello` — one dict per PLC, no poller
        counters, no controller blob.
        """
        out = []
        for s in self.sessions.values():
            c = s.conn
            dev = saved_device_for(s.cfg, s.name)
            out.append({
                "name":      s.name,
                "device":    (dev["name"] if dev else ""),
                "saved":     bool(dev),
                "label":     (dev["name"] if dev else str(s.cfg.port_ws)),
                "ip":        s.cfg.ip,
                "slot":      s.cfg.slot,
                "type":      s.cfg.controller_type,
                "ws_port":   s.cfg.port_ws,
                "ws_url":    s.ws_url,
                "running":   s.running,
                "connected": bool(c and c.connected),
                "error":     (c.error if c else ""),
                "clients":   (len(s.poller.subscribers) if s.poller else 0),
                "watching":  (len(s.poller.watched) if s.poller else 0),
                "recording": s.recorder.active,
            })
        return out

    # ── ports ─────────────────────────────────────────────────────────────────
    def used_ports(self) -> set:
        return {s.cfg.port_ws for s in self.sessions.values()}

    def alloc_port(self, preferred: int = 0) -> int:
        """Preferred port when it is genuinely free, otherwise the next one.

        Falling back instead of failing is deliberate: the point of the preset
        list is that starting three saved controllers must just work, even when
        two of them were saved with the same (default) port.
        """
        used = self.used_ports()
        if preferred and preferred not in used and _port_free(preferred):
            return preferred
        port = self.base_port
        while port < self.base_port + 200:
            if port not in used and port not in self.reserved and _port_free(port):
                return port
            port += 1
        raise RuntimeError("no free WebSocket port in "
                           f"{self.base_port}..{self.base_port + 200}")

    # ── lifecycle ─────────────────────────────────────────────────────────────
    async def start(self, name: str, cfg: Config) -> PlcSession:
        name = (name or cfg.ip or "plc").strip()
        if self.get(name):
            raise ValueError(f"session “{name}” is already running")
        want = int(getattr(cfg, "port_ws", 0) or 0)
        port = self.alloc_port(want)
        if want and port != want:
            log.warning(f"[{name}] WS port {want} is taken — using {port}")
        cfg.port_ws = port
        sess = PlcSession(name, cfg)
        sess.manager = self
        self.sessions[sess.name] = sess
        try:
            await sess.start()
        except Exception:
            self.sessions.pop(sess.name, None)
            raise
        return sess

    async def stop(self, name: str) -> bool:
        sess = self.get(name)
        if not sess:
            return False
        self.sessions.pop(sess.name, None)
        await sess.stop()
        return True

    async def stop_all(self):
        for sess in list(self.sessions.values()):
            try:
                await sess.stop()
            except Exception as e:
                log.warning(f"[{sess.name}] stop failed: {e}")
        self.sessions.clear()


# ─────────────────────────────────────────────────────────────────────────────
def _session_list(session: PlcSession) -> list:
    """Sibling sessions of this one — every PLC served by this process.

    Falls back to the session itself when it was created outside a manager
    (unit tests, an embedded single session).
    """
    mgr = getattr(session, "manager", None)
    if mgr is not None:
        try:
            return mgr.brief_all()
        except Exception as e:
            log.warning(f"session list failed: {e}")
    dev = saved_device_for(session.cfg, session.name)
    return [{
        "name":      session.name,
        "device":    (dev["name"] if dev else ""),
        "saved":     bool(dev),
        "label":     (dev["name"] if dev else str(session.cfg.port_ws)),
        "ip":        session.cfg.ip,
        "slot":      session.cfg.slot,
        "type":      session.cfg.controller_type,
        "ws_port":   session.cfg.port_ws,
        "ws_url":    session.ws_url,
        "running":   session.running,
        "connected": bool(session.conn and session.conn.connected),
        "error":     (session.conn.error if session.conn else ""),
    }]


async def ws_handler(websocket, session: PlcSession):
    """Serve one browser tab.

    Every command is scoped to `session`, so tabs pointed at different ports
    drive different controllers: separate watch sets, separate values, separate
    recording. `session.conn` is read on each use rather than captured once,
    because a reconnect swaps the connection object under a live socket.
    """
    poller = session.poller
    cfg    = session.cfg
    rec    = session.recorder
    slog   = session.log
    slog.info(f"WS client: {websocket.remote_address}")
    poller.subscribe(websocket)
    try:
        await websocket.send(json.dumps({
            "type": "hello",
            # Which controller this port serves — lets a tab show what it is
            # actually looking at when several bridges are up.
            "plc": session.name,
            "wsPort": cfg.port_ws,
            # Every controller this bridge process serves, so the connect
            # dialog can list them before the user commits to a port.
            "sessions": _session_list(session),
            "controller": session.conn.controller_info,
            "connected": session.conn.connected,
            "error": session.conn.error,
            "watching": list(poller.watched),
            "values": poller.values,
        }))
        async for raw in websocket:
            cmd = None
            try:
                msg = json.loads(raw)
                cmd = msg.get("cmd")
                if cmd == "watch":
                    poller.watch(msg.get("tags",[]))
                    await websocket.send(json.dumps({"type":"ack","cmd":"watch","watching":list(poller.watched)}))
                elif cmd == "watch_only":
                    # Replace watched set entirely — only poll tags visible in current graph
                    new_tags = set(msg.get("tags", []))
                    removed = poller.watched - new_tags
                    added   = new_tags - poller.watched
                    poller.watched = new_tags
                    # Accept optional L5X type hints for Logix tag sizing
                    type_map = msg.get("tagTypes") or {}
                    if type_map and hasattr(session.conn, "set_tag_types"):
                        try:
                            session.conn.set_tag_types(type_map)
                        except Exception as e:
                            slog.warning(f"set_tag_types failed: {e}")
                    # SLC rack layout for I/O block reads. Absent = this tab's
                    # project has none (L5X, older tracer) — a layout left over
                    # from another project must not stay in use.
                    if hasattr(session.conn, "set_io_slots"):
                        try:
                            session.conn.set_io_slots(msg.get("ioSlots"))
                        except Exception as e:
                            slog.warning(f"set_io_slots failed: {e}")
                    if removed or added:
                        slog.info(f"watch_only: +{len(added)} -{len(removed)} tags, total={len(new_tags)}")
                    if DEBUG_SAVE:
                        sample_add = list(added)[:5]
                        sample_rem = list(removed)[:5]
                        slog.info(
                            f"[DBG-SAVE] watch_only: payload={len(new_tags)} tagTypes={len(type_map)} "
                            f"added_sample={sample_add} removed_sample={sample_rem}")
                    await websocket.send(json.dumps({"type":"ack","cmd":"watch_only","watching":list(poller.watched)}))
                    # Push current known values for newly watched tags so graph updates immediately
                    try:
                        current_vals = {t: poller.values[t] for t in new_tags if t in poller.values}
                        if current_vals:
                            if DEBUG_SAVE:
                                slog.info(
                                    f"[DBG-SAVE] watch_only → pushing cached current_vals: "
                                    f"{len(current_vals)} keys "
                                    f"sample={list(current_vals.items())[:3]}")
                            await websocket.send(json.dumps({"type":"values","data":current_vals}))
                    except Exception:
                        pass
                elif cmd == "unwatch":
                    poller.unwatch(msg.get("tags",[]))
                elif cmd == "read_now":
                    req_tags = msg.get("tags", [])
                    if DEBUG_SAVE:
                        prog_tags = [t for t in req_tags if t.startswith("Program:")]
                        # Overlap with the live watch set — the background poller
                        # may read these same tags in parallel. This is NOT a
                        # duplicate-request bug: read_tags() serialises on the
                        # connection lock and shares the handle cache, so the two
                        # paths can never issue concurrent reads on one handle.
                        overlap = sum(1 for t in req_tags if t in poller.watched)
                        slog.info(
                            f"[DBG-SAVE] read_now request: count={len(req_tags)} "
                            f"unique={len(set(req_tags))} "
                            f"program_scope={len(prog_tags)} "
                            f"watch_overlap={overlap}/{len(poller.watched)} "
                            f"sample_in={req_tags[:5]} "
                            f"sample_prog={prog_tags[:5]} "
                            f"sample_tail={req_tags[-5:]}")
                    vals = await session.conn.read_tags(req_tags)
                    if DEBUG_SAVE:
                        missing = [t for t in req_tags if t not in vals]
                        errors  = [(k, v.get("error")) for k, v in vals.items() if v.get("error")]
                        sample_ok = [(k, vals[k].get("value"), vals[k].get("type"))
                                     for k in list(vals.keys())[:5]]
                        prog_vals = [(k, vals[k].get("value"), vals[k].get("type"), vals[k].get("error"))
                                     for k in vals if k.startswith("Program:")]
                        slog.info(
                            f"[DBG-SAVE] read_now result: got={len(vals)}/{len(req_tags)} "
                            f"missing={len(missing)} errors={len(errors)}")
                        if sample_ok:
                            slog.info(f"[DBG-SAVE] read_now sample_ok={sample_ok}")
                        if prog_vals:
                            slog.info(f"[DBG-SAVE] read_now program_scope_vals={prog_vals}")
                        if errors:
                            slog.info(f"[DBG-SAVE] read_now errors_sample={errors[:5]}")
                        if missing:
                            slog.info(f"[DBG-SAVE] read_now missing_sample={missing[:5]}")
                    poller.values.update(vals)
                    await websocket.send(json.dumps({"type":"values","data":vals}))
                elif cmd == "set_interval":
                    iv = min(60.0, poll_interval_from(msg.get("interval")))
                    cfg.poll_interval = iv
                    await session.conn.update_poll_interval(iv)
                elif cmd == "ping":
                    await websocket.send(json.dumps({"type":"pong","ts":time.time()}))
                elif cmd == "list_sessions":
                    await websocket.send(json.dumps({
                        "type": "sessions", "sessions": _session_list(session)}))
                elif cmd == "get_status":
                    await websocket.send(json.dumps({
                        "type":"status","connected":session.conn.connected,
                        "error":session.conn.error,"controller":session.conn.controller_info,
                        "plc":session.name,
                        "watching":list(poller.watched),
                    }))
                elif cmd == "get_stats":
                    # The tracer's header line: tag count + read cycle of THIS session
                    await websocket.send(json.dumps({"type": "stats", **session.stats()}))
                elif cmd == "reconnect":
                    ok = await session.reconnect()
                    await websocket.send(json.dumps({"type":"ack","cmd":"reconnect","ok":ok}))
                elif cmd == "rec_start":
                    rec.start(
                        msg.get("stamp", ""),
                        msg.get("graphSnap", {}),
                        msg.get("tagIndex", []),
                        Path(cfg.records_dir),
                        websocket,
                    )
                    await websocket.send(json.dumps({"type":"ack","cmd":"rec_start","ok":True}))
                elif cmd == "rec_chunk":
                    rec.chunk(msg.get("tagIndex", []), msg.get("frames", []),
                              msg.get("graphSnap"))
                    await websocket.send(json.dumps({"type":"ack","cmd":"rec_chunk","ok":True}))
                elif cmd == "rec_stop":
                    rec.stop()
                    await websocket.send(json.dumps({"type":"ack","cmd":"rec_stop","ok":True}))
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"type":"error","msg":"invalid JSON"}))
            except _WS_CLOSED:
                raise                                  # the tab is gone
            except Exception as e:
                # One command failing — a records folder that cannot be written,
                # a file held open by another program, a malformed field — used
                # to end the whole connection: the tab went OFFLINE and stayed
                # there, since the tracer does not reconnect by itself. Report it
                # and keep serving. A send that fails here because the client is
                # gone raises again and ends the loop below, as it should.
                slog.warning(f"command {cmd!r} failed: {type(e).__name__}: {e}")
                await websocket.send(json.dumps({"type": "error", "cmd": cmd, "msg": str(e)}))
    except Exception as e:
        slog.info(f"WS disconnected: {e}")
    finally:
        poller.unsubscribe(websocket)
        rec.stop_if_owner(websocket)


# ─────────────────────────────────────────────────────────────────────────────
def build_http_app(manager: SessionManager, log_buf):
    """REST API over every running PLC session.

    Each route takes an optional ?plc=<name> selector. Without it the request
    goes to the first running session, so a client written against the
    single-PLC API keeps working unchanged.
    """
    app = web.Application()
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(allow_credentials=True, expose_headers="*", allow_headers="*", allow_methods="*")
    })

    def pick(req):
        name = req.rel_url.query.get("plc") or req.rel_url.query.get("session") or ""
        s = manager.get(name) if name else manager.default()
        if s is None:
            raise web.HTTPNotFound(
                text=json.dumps({"error": f"no such PLC session: {name or '(none running)'}",
                                 "sessions": manager.names()}),
                content_type="application/json")
        return s

    async def h_sessions(req):
        return web.json_response({"sessions": manager.status_all()})
    async def h_status(req):
        s = pick(req)
        return web.json_response({"plc":s.name,"connected":s.conn.connected,"error":s.conn.error,
                                  "controller":s.conn.controller_info,"watching":list(s.poller.watched),
                                  "poll_interval":s.cfg.poll_interval,"ws_port":s.cfg.port_ws,
                                  "auto_reconnect":s.conn.auto_reconnect})
    async def h_read(req):
        s = pick(req)
        names=[t.strip() for t in req.rel_url.query.get("tags","").split(",") if t.strip()]
        if not names: return web.json_response({"error":"no tags"},status=400)
        vals = await s.conn.read_tags(names); s.poller.values.update(vals)
        return web.json_response(vals)
    async def h_watch(req):
        s = pick(req)
        data=await req.json(); s.poller.watch(data.get("tags",[])); s.poller.touch_http()
        return web.json_response({"watching":list(s.poller.watched)})
    async def h_values(req):
        s = pick(req); s.poller.touch_http()      # an HTTP reader keeps polling alive
        return web.json_response(s.poller.values)
    async def h_reconnect(req):
        s = pick(req)
        s.conn.auto_reconnect=True; ok=await s.reconnect()
        return web.json_response({"ok":ok,"error":s.conn.error})
    async def h_disconnect(req):
        s = pick(req)
        s.conn.auto_reconnect=False; await s.conn.disconnect()
        s.log.info("Disconnected by UI request"); return web.json_response({"ok":True})
    async def h_tags(req):
        tags=await pick(req).conn.get_tag_list(); return web.json_response({"tags":tags})
    async def h_config_get(req):
        s = pick(req); cfg = s.cfg
        return web.json_response({"plc":s.name,"ip":cfg.ip,"slot":cfg.slot,"controller_type":cfg.controller_type,"processor_type":cfg.processor_type,"poll_interval":cfg.poll_interval,"via_rslinx":cfg.via_rslinx,"port_ws":cfg.port_ws})
    async def h_config_post(req):
        s = pick(req); cfg = s.cfg
        data=await req.json(); need_reconnect=False
        for key,cast in (("ip",str),("slot",int),("via_rslinx",bool)):
            if key in data:
                val=cast(data[key])
                if val!=getattr(cfg,key): setattr(cfg,key,val); need_reconnect=True
        if "controller_type" in data and data["controller_type"]!=cfg.controller_type:
            cfg.controller_type=data["controller_type"]
            cfg.processor_type="Logix5000" if cfg.controller_type=="logix" else cfg.processor_type
            need_reconnect=True
        if "poll_interval" in data:
            cfg.poll_interval=min(60.0,poll_interval_from(data["poll_interval"]))
            await s.conn.update_poll_interval(cfg.poll_interval)
        if need_reconnect:
            s.conn.auto_reconnect=True; ok=await s.reconnect()
            return web.json_response({"ok":ok,"reconnected":True,"error":s.conn.error})
        return web.json_response({"ok":True,"reconnected":False})
    async def h_logs(req):
        since=int(req.rel_url.query.get("since",0)); return web.json_response(log_buf.since(since))
    for r in [
        app.router.add_get("/sessions",h_sessions),
        app.router.add_get("/status",h_status),
        app.router.add_get("/read",h_read),
        app.router.add_post("/watch",h_watch),
        app.router.add_get("/values",h_values),
        app.router.add_post("/reconnect",h_reconnect),
        app.router.add_post("/disconnect",h_disconnect),
        app.router.add_get("/tags",h_tags),
        app.router.add_get("/config",h_config_get),
        app.router.add_post("/config",h_config_post),
        app.router.add_get("/logs",h_logs),
    ]: cors.add(r)
    return app


# ─────────────────────────────────────────────────────────────────────────────
def interactive_setup(cfg):
    print("\n"+"="*55)
    print("  PLC Tracer -- Live Bridge")
    print("="*55)
    print("\nТип контроллера:")
    print("  1. MicroLogix (1100 / 1200 / 1400 / 1500) — EtherNet/IP")
    print("  2. SLC 5/05 — EtherNet/IP напрямую")
    print("  3. SLC 5/03, 5/04 — через RSLinx (DH+ / DH-485 routing)")
    print("  4. ControlLogix / CompactLogix (Logix 5000) — EtherNet/IP")
    print()
    ch=input("Выбор [1/2/3/4]: ").strip() or "1"
    if ch=="3":
        cfg.via_rslinx=True
        print("\nRSLinx будет использоваться как CIP gateway.")
    proc={"1":"MicroLogix","2":"SLC","3":"SLC","4":"Logix5000"}.get(ch,"SLC")
    cfg.processor_type=proc
    cfg.controller_type="logix" if ch=="4" else "slc"
    ip=input(f"IP адрес [{cfg.ip}]: ").strip()
    if ip: cfg.ip=ip
    if ch in ("2","3"):
        sl=input(f"Backplane slot [{cfg.slot}]: ").strip()
        if sl: cfg.slot=int(sl)
    if ch=="4":
        sl=input(f"CPU backplane slot [{cfg.slot}]: ").strip()
        if sl: cfg.slot=int(sl)
    iv=input(f"Интервал опроса сек [{cfg.poll_interval}]: ").strip()
    if iv:
        try: cfg.poll_interval=poll_interval_from(iv)
        except: pass
    if input("Сохранить config? [y/N]: ").strip().lower()=="y":
        cfg.save()
    print()

def load_tags_from_slc(path:str)->list:
    """Extract address list from .SLC or .APS text file."""
    import re
    tags=set()
    addr_re=re.compile(r'\b([IOBNFTCRSAb][IiOoBbNnFfTtCcRrSsAa]?\d*[:/][0-9A-Za-z./]+)',re.I)
    with open(path,encoding='utf-8',errors='ignore') as f:
        for line in f:
            for m in addr_re.finditer(line):
                a=m.group(1)
                if len(a)<3: continue
                tags.add(a)
    return list(tags)[:500]


# ─────────────────────────────────────────────────────────────────────────────
async def main(specs, port_http: int = DEFAULT_HTTP):
    """Run every requested PLC session side by side.

    `specs` is a list of (name, Config). Each session takes its own WebSocket
    port, which is the address a browser tab connects to, so several tabs can
    watch several controllers at the same time. The REST API is shared and
    selects the session with ?plc=<name>.
    """
    log_buf = LogBuffer()
    logging.getLogger().addHandler(MemoryLogHandler(log_buf))
    mgr = SessionManager()
    for name, cfg in specs:
        try:
            await mgr.start(name, cfg)
        except Exception as e:
            log.error(f"[{name}] not started: {e}")
    if not len(mgr):
        log.error("No PLC session started — nothing to do.")
        return
    http_runner=None
    if AIOHTTP_OK:
        app=build_http_app(mgr,log_buf)
        runner=web.AppRunner(app)
        await runner.setup()
        # Best effort, as in the GUI: the port is taken when another bridge
        # (the GUI one, say) already runs. That used to end this one with a
        # traceback right after its sessions had started — the WebSocket
        # sessions do not need REST at all.
        try:
            await web.TCPSite(runner,"0.0.0.0",port_http).start()
            http_runner=runner
            log.info(f"HTTP REST  → http://localhost:{port_http}")
        except OSError as e:
            await runner.cleanup()
            log.warning(f"HTTP REST not started on {port_http}: {e} — "
                        f"WebSocket sessions are unaffected")
    print(f"\n{'-'*55}")
    print(f"  Bridge started — {len(mgr)} PLC session(s).")
    for s in mgr.sessions.values():
        print(f"    {s.name:<18} {s.cfg.ip:<15} {s.cfg.controller_type:<6} → {s.ws_url}")
    print("  Point each browser tab at the URL of its controller.")
    print(f"{'-'*55}\n  Ctrl+C to stop\n")
    try:
        await asyncio.Future()
    except (KeyboardInterrupt,asyncio.CancelledError):
        pass
    finally:
        if http_runner: await http_runner.cleanup()
        await mgr.stop_all()


def parse_args():
    p=argparse.ArgumentParser(description="PLC Tracer 500 Bridge — SLC 500 / MicroLogix")
    p.add_argument("--ip",default=None)
    p.add_argument("--slot",type=int,default=0)
    p.add_argument("--rslinx",action="store_true")
    p.add_argument("--type",default=None,choices=["slc","logix"],help="Controller family: slc (default) | logix (ControlLogix/CompactLogix)")
    p.add_argument("--interval",type=float,default=POLL_IV,
                   help=f"Pause between poll cycles, s (default {POLL_IV}, minimum {POLL_IV_MIN})")
    p.add_argument("--slc-gap-bytes",type=int,default=None,dest="slc_gap_bytes",
                   help="SLC block reads: unwanted bytes worth carrying inside a "
                        "block to save one request. Low for a serial-gateway link "
                        "(bytes cost wire time), high for direct Ethernet "
                        "(requests cost scan time). Default 16.")
    p.add_argument("--conn-groups",type=int,default=None,dest="conn_groups",
                   help="Parallel CIP connections the watch list is spread over "
                        "(0 = auto: 4 for logix, 2 for slc). More connections = "
                        "overlapping round-trips = shorter poll cycle.")
    p.add_argument("--plc",action="append",default=[],metavar="NAME",
                   help="Start a saved device by name (repeatable). Devices come "
                        "from plc_bridge_devices.json — the same list the GUI "
                        "writes. Each one gets its own WebSocket port, so a "
                        "browser tab per controller can run at the same time.")
    p.add_argument("--all-plc",action="store_true",dest="all_plc",
                   help="Start every saved device")
    p.add_argument("--devices",default=None,
                   help="Path to the saved-device list (default: "
                        "plc_bridge_devices.json next to this script)")
    p.add_argument("--config",default=None)
    p.add_argument("--slc",default=None,help="SLC/APS file to load address list from")
    p.add_argument("--ws-port",type=int,default=DEFAULT_WS)
    p.add_argument("--http-port",type=int,default=DEFAULT_HTTP)
    p.add_argument("--no-interactive",action="store_true")
    return p.parse_args()


if __name__=="__main__":
    if not LIBPLCTAG_OK:
        print("\n[ERROR] libplctag недоступен.")
        print("Положите plctag.dll в папку Bridge/libplctag_2.6.16_windows_x64/")
        print("Установка зависимостей: pip install websockets aiohttp aiohttp-cors\n")
        sys.exit(1)
    args=parse_args()
    specs=[]
    if args.plc or args.all_plc:
        # Named presets from the list the GUI writes. This is the multi-PLC
        # path: every device becomes its own session on its own WS port, and a
        # browser tab connects to whichever controller it wants to watch.
        store=DeviceStore(Path(args.devices) if args.devices else None)
        if args.all_plc:
            wanted=list(store.devices)
        else:
            wanted=[]
            for n in args.plc:
                d=store.find(n)
                if d: wanted.append(d)
                else: print(f"[WARN] no saved device named {n!r} in {store.path.name}")
        if not wanted:
            known=", ".join(d["name"] for d in store.devices) or "(list is empty)"
            print(f"\n[ERROR] nothing to start. Saved devices: {known}\n")
            sys.exit(1)
        for d in wanted:
            cfg=device_to_config(d)
            if args.interval and args.interval!=POLL_IV: cfg.poll_interval=poll_interval_from(args.interval)
            if args.slc_gap_bytes is not None: cfg.slc_gap_bytes=args.slc_gap_bytes
            if args.slc: cfg.slc_path=args.slc
            specs.append((d["name"],cfg))
    else:
        cfg=Config()
        if args.config: cfg.load(args.config)
        if args.ip:           cfg.ip=args.ip
        if args.slot:         cfg.slot=args.slot
        if args.rslinx:       cfg.via_rslinx=True
        if args.type:
            cfg.controller_type=args.type
            cfg.processor_type="Logix5000" if args.type=="logix" else cfg.processor_type
        if args.interval:     cfg.poll_interval=poll_interval_from(args.interval)
        if args.conn_groups is not None: cfg.conn_groups=args.conn_groups
        if args.slc_gap_bytes is not None: cfg.slc_gap_bytes=args.slc_gap_bytes
        if args.slc:          cfg.slc_path=args.slc
        if args.ws_port:      cfg.port_ws=args.ws_port
        if args.http_port:    cfg.port_http=args.http_port
        if not args.ip and not args.config and not args.no_interactive:
            interactive_setup(cfg)
        specs.append((args.ip or cfg.ip, cfg))
    try:
        asyncio.run(main(specs, args.http_port or DEFAULT_HTTP))
    except KeyboardInterrupt:
        print("\nBye.")
