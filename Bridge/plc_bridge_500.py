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

Requirements:
    pip install websockets aiohttp aiohttp-cors
    plctag.dll in Bridge/libplctag_2.6.16_windows_x64/
"""

import asyncio, collections, json, logging, argparse, os, sys, time, zlib
from pathlib import Path

# ── libplctag (SLC 5/05, MicroLogix via PCCC + ControlLogix/CompactLogix via CIP) ─────────────────────
try:
    import ctypes
    _dll_dir = Path(__file__).parent / "libplctag_2.6.16_windows_x64"
    _dll_path = str(_dll_dir / "plctag.dll")
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
    print(f"[WARN] libplctag not loaded: {_e}")

# Transient read conditions: the handle is still perfectly good, only this
# cycle's read did not finish in time. Destroying the handle here used to start
# a rebuild storm — every timed-out tag was re-created on the next cycle, which
# made that cycle slower still, so the timeouts fed themselves until the user
# shrank the scene. Keep the handle: the in-flight read lands a few ms later.
_SOFT_READ_ERRS = frozenset({PLCTAG_ERR_TIMEOUT, PLCTAG_ERR_BUSY})

try:
    import aiohttp
    from aiohttp import web
    import aiohttp_cors
    AIOHTTP_OK = True
except ImportError:
    AIOHTTP_OK = False

try:
    import websockets
    WS_OK = True
except ImportError:
    WS_OK = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("bridge500")

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

    def add(self, level: str, msg: str):
        self._seq += 1
        self._buf.append({"seq": self._seq, "ts": time.time(), "level": level, "msg": msg})

    def since(self, seq: int) -> list:
        return [r for r in self._buf if r["seq"] > seq]

class MemoryLogHandler(logging.Handler):
    def __init__(self, buf: LogBuffer):
        super().__init__()
        self.buf = buf
        self.setFormatter(logging.Formatter("%(message)s"))

    def emit(self, record):
        try: self.buf.add(record.levelname, self.format(record))
        except: pass

DEFAULT_WS   = 8765
DEFAULT_HTTP = 8766
POLL_IV      = 0.2
MAX_BATCH    = 40
RECONNECT    = 5

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
        self.watched_tags  = []
        self.slc_path      = ""        # loaded .SLC / .APS file for tag list
        self.port_ws       = DEFAULT_WS
        self.port_http     = DEFAULT_HTTP
        self.records_dir   = str(Path(__file__).parent / "RECORDS")

    def build_ip_path(self):
        return self.ip   # libplctag builds gateway= from plain IP

    def save(self, path="config500.json"):
        with open(path,"w") as f: json.dump(self.__dict__,f,indent=2)
        log.info(f"Config saved → {path}")

    def load(self, path="config500.json"):
        with open(path) as f: self.__dict__.update(json.load(f))


# ─────────────────────────────────────────────────────────────────────────────
# Recorder state (single active session at a time)
_rec: dict = {
    'active':    False,
    'tmp_path':  None,   # Path to _tmp file (header + all chunks so far)
    'main_path': None,   # Path to final .ndrec file (mirror of tmp, rebuilt each chunk)
    'header':    {},     # current header dict (tagIndex/graphSnap updated on each chunk)
    'owner':     None,   # websocket that started it, so a dropped client closes it
}

def rec_start(stamp: str, graph_snap: dict, tag_index: list, records_dir: Path,
              owner=None):
    rec_stop()  # stop any previous session
    records_dir.mkdir(parents=True, exist_ok=True)
    _rec['tmp_path']  = records_dir / f"plc_rec_{stamp}_tmp.ndrec"
    _rec['main_path'] = records_dir / f"plc_rec_{stamp}.ndrec"
    _rec['header']    = {
        "type": "header", "version": 2,
        "createdAt": time.time(),
        "tagIndex":  tag_index,
        "graphSnap": graph_snap,
    }
    _rec['tmp_path'].write_text(
        json.dumps(_rec['header'], ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _rec['active'] = True
    _rec['owner']  = owner
    log.info(f"[REC] started → {_rec['main_path'].name}")

def rec_chunk(tag_index: list, frames: list, graph_snap: dict | None = None):
    if not _rec['active']:
        return
    # 1. Update tagIndex in header
    _rec['header']['tagIndex'] = tag_index
    # The tracer re-snapshots the scene when it captures its FIRST frame: REC is
    # normally pressed before a tag is picked, so the snapshot taken at rec_start
    # describes an empty canvas. Take the later one when it arrives.
    if graph_snap:
        _rec['header']['graphSnap'] = graph_snap
    # 2. Read existing tmp lines (header + previous chunks)
    existing = _rec['tmp_path'].read_text(encoding="utf-8").splitlines(keepends=True)
    # 3. Replace header line and append new chunk
    lines = [json.dumps(_rec['header'], ensure_ascii=False) + "\n"]
    lines += existing[1:]  # previous chunks (skip old header)
    if frames:
        lines.append(json.dumps({"type": "chunk", "frames": frames}, ensure_ascii=False) + "\n")
    content = "".join(lines)
    # 4. Atomic write: tmp → staging file → rename to main, then update tmp
    staging = _rec['main_path'].with_suffix('.ndrec._writing')
    staging.write_text(content, encoding="utf-8")
    os.replace(str(staging), str(_rec['main_path']))  # atomic rename
    _rec['tmp_path'].write_text(content, encoding="utf-8")
    log.info(f"[REC] chunk flushed ({len(frames)} frames) → {_rec['main_path'].name}")

def rec_stop():
    if not _rec['active']:
        return
    # Remove tmp scratch file, main file is already up to date
    if _rec['tmp_path'] and _rec['tmp_path'].exists():
        _rec['tmp_path'].unlink()
    log.info(f"[REC] stopped → {_rec['main_path'].name}")
    _rec['active']    = False
    _rec['tmp_path']  = None
    _rec['main_path'] = None
    _rec['header']    = {}
    _rec['owner']     = None


def rec_stop_if_owner(websocket):
    """Close the session whose client just disappeared.

    A browser that closes mid-recording never sends rec_stop, which used to
    leave the session 'active' with its _tmp scratch file on disk forever. The
    .ndrec itself is already complete — every chunk rewrites it — so this only
    has to release the session.
    """
    if _rec['active'] and _rec['owner'] is websocket:
        log.info("[REC] client disconnected — closing the recording session")
        rec_stop()


# ─────────────────────────────────────────────────────────────────────────────
# libplctag helpers: SLC address → connection string + value extraction
# ─────────────────────────────────────────────────────────────────────────────
import re as _re

# Timer/Counter word layout (byte offsets within a 6-byte element, elem_size=6)
# Word 0 (bytes 0-1): control/status bits
# Word 1 (bytes 2-3): PRE (preset)
# Word 2 (bytes 4-5): ACC (accumulated)
_TC_BYTE_OFF = {'PRE': 2, 'LEN': 2, 'ACC': 4}

# Timer/Counter status bits in word 0 (bit positions)
_T_BITS = {'EN': 15, 'TT': 14, 'DN': 13}
_C_BITS = {'CU': 15, 'CD': 14, 'DN': 13, 'OV': 12, 'UN': 11}

# Slash-notation aliases for timer/counter status bits
_TC_SLASH_BITS = {**_T_BITS, **_C_BITS}

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

    Key insight for Timer/Counter (T, C files):
      libplctag SLC driver addresses T/C by ELEMENT number, not word offset.
      Each T/C element is 3 words (6 bytes).  We read the whole element
      (elem_size=6, elem_count=1) and extract sub-fields in _slc_read_value.

    Returns None if the address can't be parsed.
    """
    parsed = _slc_parse_addr(name)
    if not parsed:
        return None
    prefix, file_num, element, bit, sub = parsed

    if prefix in ('T', 'C'):
        # Always read the full 6-byte (3-word) timer/counter element.
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
        # N, B, I, O, S, R, A — all 16-bit words
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
_SLC_ELEM_SIZE = {'T': 6, 'C': 6, 'F': 4}

# Payload ceiling per PCCC read. An SLC 5/05 tops out around 236 bytes of data
# per request; 224 keeps a margin for the reply header while filling the packet.
# A controller that refuses this size does not lose block reads — the cap for
# that file is halved on the spot (see _slc_block_cap).
_SLC_BLOCK_BYTES = 224
_SLC_BLOCK_BYTES_MIN = 16     # 8 words — below this a block barely beats singles

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


def _slc_read_value(tag_handle: int, name: str, base_off: int = 0):
    """Extract typed value from a libplctag tag handle.

    Timer/Counter layout (elem_size=6, elem_count=1):
      bytes 0-1  word 0  control/status bits  (EN/TT/DN or CU/CD/DN/OV/UN)
      bytes 2-3  word 1  PRE (preset)
      bytes 4-5  word 2  ACC (accumulated)

    `base_off` is the byte offset of this element inside the handle's buffer. It
    is 0 for a one-element handle and the element's position when the value
    comes out of a block read (a whole run of N7 / B3 / T4 fetched in a single
    PCCC request) — every offset below is relative to the element, so shifting
    the base is all that block support needs here.

    Returns int, float, bool, or dict {CTL, PRE, ACC} for bare T/C element.
    """
    parsed = _slc_parse_addr(name)
    if not parsed:
        return None
    prefix, file_num, element, bit, sub = parsed

    if prefix in ('T', 'C'):
        bits_map = _T_BITS if prefix == 'T' else _C_BITS

        if sub and sub in bits_map:
            # Named status bit: EN, TT, DN, CU, CD, OV, UN
            ctl_word = _libplctag.plc_tag_get_uint16(tag_handle, base_off)
            return bool((ctl_word >> bits_map[sub]) & 1)

        elif sub in _TC_BYTE_OFF:
            # PRE or ACC — read from correct byte offset
            return _libplctag.plc_tag_get_int16(tag_handle, base_off + _TC_BYTE_OFF[sub])

        elif sub is None and bit is not None:
            # Numeric bit within control word: T4:0/13 (same as DN)
            ctl_word = _libplctag.plc_tag_get_uint16(tag_handle, base_off)
            return bool((ctl_word >> bit) & 1)

        else:
            # Bare T/C element → return structured dict
            ctl = _libplctag.plc_tag_get_uint16(tag_handle, base_off)
            pre = _libplctag.plc_tag_get_int16(tag_handle, base_off + 2)
            acc = _libplctag.plc_tag_get_int16(tag_handle, base_off + 4)
            return {'CTL': ctl, 'PRE': pre, 'ACC': acc}

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

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.connected = False
        self.auto_reconnect = True
        self.controller_info = {}
        self.error = ""
        self._lock = asyncio.Lock()
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
            log.warning(
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
        log.info(f"CIP connections → {self._effective_groups()}"
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
            log.info(f"Received {changed} tag type hint(s) from tracer")

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
            log.info(f"poll_interval={interval}s → read_cache_ms={cache_ms} "
                     f"applied to {len(self._handles)} handle(s)"
                     + (f", {len(failed)} dropped for rebuild" if failed else ""))
        else:
            n = len(self._handles)
            self._destroy_all()
            log.info(f"poll_interval={interval}s → DLL lacks set_int_attribute; "
                     f"dropped {n} handle(s) to rebuild with read_cache_ms={cache_ms}")

    # ── lifecycle ---------------------------------------------------------
    async def connect(self) -> bool:
        async with self._lock:
            return await asyncio.get_event_loop().run_in_executor(None, self._connect_sync)

    def _connect_sync(self) -> bool:
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
            tag = _libplctag.plc_tag_create(probe_path.encode('utf-8'), _LIBPLCTAG_TIMEOUT)
            if tag < 0:
                err = _libplctag.plc_tag_decode_error(tag)
                self.error = f"libplctag create failed: {err.decode() if err else tag}"
                self.connected = False
                log.error(f"libplctag connect error ({probe_label}): {self.error}")
                return False
            rc = _libplctag.plc_tag_read(tag, _LIBPLCTAG_TIMEOUT)
            _libplctag.plc_tag_destroy(tag)
            if rc != PLCTAG_STATUS_OK:
                err = _libplctag.plc_tag_decode_error(rc)
                self.error = f"libplctag probe read failed: {err.decode() if err else rc}"
                self.connected = False
                log.error(f"libplctag connect probe error ({probe_label}): {self.error}")
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
            log.info(f"✓ Connected: {self.controller_info['name']} "
                     f"· {self._effective_groups()} CIP connection(s)"
                     + ("" if self.cfg.controller_type == 'logix'
                        else f" · block reads "
                             f"{'on' if getattr(self.cfg, 'slc_blocks', True) else 'off'}"))
            self._warn_if_slc_overparallel()
            return True
        except Exception as e:
            self.connected = False
            self.error = str(e)
            log.error(f"libplctag connect error: {e}")
            return False

    def _destroy_all(self):
        for h in self._handles.values():
            try:
                _libplctag.plc_tag_destroy(h)
            except Exception:
                pass
        self._handles.clear()
        self._handle_meta.clear()

    async def disconnect(self):
        async with self._lock:
            await asyncio.get_event_loop().run_in_executor(None, self._destroy_all)
            self.connected = False

    # ── reads -------------------------------------------------------------
    async def read_tags(self, tag_names: list) -> dict:
        if not self.connected:
            return {}
        async with self._lock:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._read_sync, tag_names)

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
        if (not is_logix) and getattr(self.cfg, 'slc_blocks', True):
            block_of = _plan_slc_blocks(
                phys_set, self._slc_no_block,
                lambda k: self._slc_block_cap.get(k, _SLC_BLOCK_BYTES),
                getattr(self.cfg, 'slc_gap_bytes', _SLC_GAP_BYTES))
        if block_of:
            covered = set(block_of)
            phys_names = sorted(phys_set - covered)
            phys_names += sorted({k for k, _ in block_of.values()})
        else:
            phys_names = list(phys_set)

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
            log.info(
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

        # A file whose block size was already stepped down this cycle. Several
        # blocks of one file fail together, and halving once per failure would
        # skip straight past the sizes the controller does accept.
        shrunk: set = set()

        def _fail(name: str, msg: str, drop: bool):
            phys_err[name] = {"value": None, "type": "?", "error": msg,
                              "ts": time.time()}
            if drop:
                self._drop_handle(name)
                # A block read the controller refused (too many elements, file
                # shorter than the run) must not keep failing every cycle: retire
                # blocks for that data file and let its addresses be read one by
                # one from the next cycle on. Timeouts do NOT land here — those
                # are transient and keep the handle.
                blk = _slc_parse_block_key(name)
                if blk is not None:
                    parsed = _slc_parse_addr(blk[0])
                    if parsed:
                        key = (parsed[0], parsed[1])
                        if key in shrunk:
                            return
                        shrunk.add(key)
                        cap = self._slc_block_cap.get(key, _SLC_BLOCK_BYTES) // 2
                        if cap >= _SLC_BLOCK_BYTES_MIN:
                            self._slc_block_cap[key] = cap
                            log.warning(
                                f"SLC block read refused on {parsed[0]}{parsed[1]} "
                                f"({msg}) — retrying with {cap}-byte blocks")
                        elif key not in self._slc_no_block:
                            self._slc_no_block.add(key)
                            log.warning(
                                f"SLC block read failed on {parsed[0]}{parsed[1]} "
                                f"({msg}) — falling back to one request per address "
                                f"for this file")

        def _decode(rc: int) -> str:
            err = _libplctag.plc_tag_decode_error(rc)
            return err.decode() if err else str(rc)

        def _classify(name: str, handle: int, rc: int):
            """Sort a plc_tag_read() return code into ok / pending / error."""
            if rc == PLCTAG_STATUS_OK:
                phys_ok[name] = handle
            elif rc == PLCTAG_STATUS_PENDING or rc in _SOFT_READ_ERRS:
                # BUSY: a read started in an earlier cycle has not landed yet.
                # Its result goes into this same tag buffer, so there is nothing
                # to re-issue — just keep waiting on the handle we already have.
                pending.append((name, handle))
            else:
                _fail(name, _decode(rc), drop=True)

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
        # scales with the size of the batch: a controller reached over a serial
        # gateway answers ~20 requests per second, so a flat 3 s would cut a
        # perfectly healthy cycle short and report timeouts for the tail.
        budget = max(_LIBPLCTAG_TIMEOUT / 1000.0,
                     min(_READ_BUDGET_MAX, _READ_BUDGET_PER_REQ * len(phys_names)))
        deadline = time.monotonic() + budget
        while (pending or creating) and time.monotonic() < deadline:
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
                        _fail(name, _decode(st), drop=True)
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
                    elif st == PLCTAG_STATUS_PENDING:
                        still.append((name, handle))
                    else:
                        _fail(name, _decode(st), drop=st not in _SOFT_READ_ERRS)
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
        for name, _h in pending:
            _fail(name, "read timeout", drop=False)
        for name, _h in creating:
            _fail(name, "tag setup timeout", drop=False)

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
                if self._err_seen.get(n) != msg:
                    label = (n.replace(_LGX_STRUCT_SENTINEL, '(struct)')
                              .replace(_SLC_BLOCK_SENTINEL, ' ×'))   # N7:0 ×28
                    fresh.append(f"{label} → {msg}")
                self._err_seen[n] = msg
            if fresh:
                more = f" (+{len(fresh) - 8} more)" if len(fresh) > 8 else ""
                log.warning(f"read failed for {len(fresh)} tag(s): "
                            f"{'; '.join(fresh[:8])}{more}")
        for n in phys_ok:
            self._err_seen.pop(n, None)

        if DEBUG_SAVE:
            # Post-read summary: timing + handle-cache reuse for this batch.
            # hits  = handles served from cache (reused, no new CIP/PCCC tag)
            # creates = brand-new handles built this batch (first-seen addresses)
            dt_ms = (time.monotonic() - _dbg_t0) * 1000.0
            hits = self._dbg_handle_hits - _dbg_hits0
            creates = self._dbg_handle_creates - _dbg_creates0
            log.info(
                f"[DBG-SAVE] read done: {len(phys_names)} physical reads in "
                f"{dt_ms:.0f} ms | handle_cache reuse={hits} new={creates} "
                f"(total cached={len(self._handles)}) | "
                f"ok={len(phys_ok)} err={len(phys_err)}")

        return results

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
def create_connection(cfg: Config):
    """Factory: single libplctag backend for all supported controller families."""
    if not LIBPLCTAG_OK:
        raise RuntimeError("libplctag is not available — place plctag.dll in "
                           "Bridge/libplctag_2.6.16_windows_x64/")
    if cfg.controller_type == 'logix':
        log.info("Backend: libplctag (CIP / ControlLogix)")
    else:
        log.info("Backend: libplctag (PCCC / SLC / MicroLogix)")
    return LibplctagConnection(cfg)


# ─────────────────────────────────────────────────────────────────────────────
# Read results that mean "this cycle ran out of time", not "this tag is bad".
_TIMEOUT_ERRS = ("read timeout", "tag setup timeout")

# When to complain about the cycle length. Not "longer than the poll interval":
# a controller reached over a serial gateway cannot serve a large watch list in
# 200 ms and never will, so that comparison would fire every single cycle and
# bury the log. This is the level where something is actually wrong.
_CYCLE_WARN_MS = 3000.0

# Batch read deadline: at least _LIBPLCTAG_TIMEOUT, then this much per physical
# request, capped. Sized for the slowest link the bridge is used on — a PLC
# serial port behind a COM-to-Ethernet converter, where one request costs tens
# of milliseconds — so that a long-but-healthy cycle is never cut short.
_READ_BUDGET_PER_REQ = 0.12   # seconds per request
_READ_BUDGET_MAX = 10.0


class TagPoller:
    def __init__(self, conn: LibplctagConnection, cfg: Config):
        self.conn = conn
        self.cfg  = cfg
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

    def subscribe(self, ws): self.subscribers.add(ws)
    def unsubscribe(self, ws): self.subscribers.discard(ws)
    def watch(self, tags): self.watched.update(tags)
    def unwatch(self, tags): self.watched -= set(tags)

    async def run(self):
        self._running = True
        log.info("Poller started")
        while self._running:
            if not self.conn.connected:
                if not self.conn.auto_reconnect:
                    await asyncio.sleep(1)
                    continue
                log.info(f"Reconnecting in {RECONNECT}s…")
                await asyncio.sleep(RECONNECT)
                await self.conn.connect()
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
                log.warning(
                    f"poll cycle {self.last_cycle_ms:.0f} ms for {n_tags} tag(s) "
                    f"in {reads} request(s)"
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
async def ws_handler(websocket, poller, conn, cfg):
    log.info(f"WS client: {websocket.remote_address}")
    poller.subscribe(websocket)
    try:
        await websocket.send(json.dumps({
            "type": "hello",
            "controller": conn.controller_info,
            "connected": conn.connected,
            "error": conn.error,
            "watching": list(poller.watched),
            "values": poller.values,
        }))
        async for raw in websocket:
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
                    if type_map and hasattr(conn, "set_tag_types"):
                        try:
                            conn.set_tag_types(type_map)
                        except Exception as e:
                            log.warning(f"set_tag_types failed: {e}")
                    if removed or added:
                        log.info(f"watch_only: +{len(added)} -{len(removed)} tags, total={len(new_tags)}")
                    if DEBUG_SAVE:
                        sample_add = list(added)[:5]
                        sample_rem = list(removed)[:5]
                        log.info(
                            f"[DBG-SAVE] watch_only: payload={len(new_tags)} tagTypes={len(type_map)} "
                            f"added_sample={sample_add} removed_sample={sample_rem}")
                    await websocket.send(json.dumps({"type":"ack","cmd":"watch_only","watching":list(poller.watched)}))
                    # Push current known values for newly watched tags so graph updates immediately
                    try:
                        current_vals = {t: poller.values[t] for t in new_tags if t in poller.values}
                        if current_vals:
                            if DEBUG_SAVE:
                                log.info(
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
                        log.info(
                            f"[DBG-SAVE] read_now request: count={len(req_tags)} "
                            f"unique={len(set(req_tags))} "
                            f"program_scope={len(prog_tags)} "
                            f"watch_overlap={overlap}/{len(poller.watched)} "
                            f"sample_in={req_tags[:5]} "
                            f"sample_prog={prog_tags[:5]} "
                            f"sample_tail={req_tags[-5:]}")
                    vals = await conn.read_tags(req_tags)
                    if DEBUG_SAVE:
                        missing = [t for t in req_tags if t not in vals]
                        errors  = [(k, v.get("error")) for k, v in vals.items() if v.get("error")]
                        sample_ok = [(k, vals[k].get("value"), vals[k].get("type"))
                                     for k in list(vals.keys())[:5]]
                        prog_vals = [(k, vals[k].get("value"), vals[k].get("type"), vals[k].get("error"))
                                     for k in vals if k.startswith("Program:")]
                        log.info(
                            f"[DBG-SAVE] read_now result: got={len(vals)}/{len(req_tags)} "
                            f"missing={len(missing)} errors={len(errors)}")
                        if sample_ok:
                            log.info(f"[DBG-SAVE] read_now sample_ok={sample_ok}")
                        if prog_vals:
                            log.info(f"[DBG-SAVE] read_now program_scope_vals={prog_vals}")
                        if errors:
                            log.info(f"[DBG-SAVE] read_now errors_sample={errors[:5]}")
                        if missing:
                            log.info(f"[DBG-SAVE] read_now missing_sample={missing[:5]}")
                    poller.values.update(vals)
                    await websocket.send(json.dumps({"type":"values","data":vals}))
                elif cmd == "set_interval":
                    iv = max(0.2, min(60, float(msg.get("interval", 0.2))))
                    poller.cfg.poll_interval = iv
                    await conn.update_poll_interval(iv)
                elif cmd == "ping":
                    await websocket.send(json.dumps({"type":"pong","ts":time.time()}))
                elif cmd == "get_status":
                    await websocket.send(json.dumps({
                        "type":"status","connected":conn.connected,
                        "error":conn.error,"controller":conn.controller_info,
                        "watching":list(poller.watched),
                    }))
                elif cmd == "reconnect":
                    await conn.disconnect()
                    ok = await conn.connect()
                    await websocket.send(json.dumps({"type":"ack","cmd":"reconnect","ok":ok}))
                elif cmd == "rec_start":
                    rec_start(
                        msg.get("stamp", ""),
                        msg.get("graphSnap", {}),
                        msg.get("tagIndex", []),
                        Path(cfg.records_dir),
                        websocket,
                    )
                    await websocket.send(json.dumps({"type":"ack","cmd":"rec_start","ok":True}))
                elif cmd == "rec_chunk":
                    rec_chunk(msg.get("tagIndex", []), msg.get("frames", []),
                              msg.get("graphSnap"))
                    await websocket.send(json.dumps({"type":"ack","cmd":"rec_chunk","ok":True}))
                elif cmd == "rec_stop":
                    rec_stop()
                    await websocket.send(json.dumps({"type":"ack","cmd":"rec_stop","ok":True}))
            except json.JSONDecodeError:
                await websocket.send(json.dumps({"type":"error","msg":"invalid JSON"}))
    except Exception as e:
        log.info(f"WS disconnected: {e}")
    finally:
        poller.unsubscribe(websocket)
        rec_stop_if_owner(websocket)


# ─────────────────────────────────────────────────────────────────────────────
def build_http_app(conn, poller, cfg, log_buf):
    app = web.Application()
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(allow_credentials=True, expose_headers="*", allow_headers="*", allow_methods="*")
    })
    async def h_status(req):
        return web.json_response({"connected":conn.connected,"error":conn.error,"controller":conn.controller_info,"watching":list(poller.watched),"poll_interval":cfg.poll_interval,"ws_port":cfg.port_ws,"auto_reconnect":conn.auto_reconnect})
    async def h_read(req):
        names=[t.strip() for t in req.rel_url.query.get("tags","").split(",") if t.strip()]
        if not names: return web.json_response({"error":"no tags"},status=400)
        vals = await conn.read_tags(names); poller.values.update(vals)
        return web.json_response(vals)
    async def h_watch(req):
        data=await req.json(); poller.watch(data.get("tags",[]))
        return web.json_response({"watching":list(poller.watched)})
    async def h_values(req):
        return web.json_response(poller.values)
    async def h_reconnect(req):
        conn.auto_reconnect=True; await conn.disconnect(); ok=await conn.connect()
        return web.json_response({"ok":ok,"error":conn.error})
    async def h_disconnect(req):
        conn.auto_reconnect=False; await conn.disconnect()
        log.info("Disconnected by UI request"); return web.json_response({"ok":True})
    async def h_tags(req):
        tags=await conn.get_tag_list(); return web.json_response({"tags":tags})
    async def h_config_get(req):
        return web.json_response({"ip":cfg.ip,"slot":cfg.slot,"controller_type":cfg.controller_type,"processor_type":cfg.processor_type,"poll_interval":cfg.poll_interval,"via_rslinx":cfg.via_rslinx,"port_ws":cfg.port_ws,"port_http":cfg.port_http})
    async def h_config_post(req):
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
            cfg.poll_interval=max(0.2,min(60.0,float(data["poll_interval"]))); poller.cfg.poll_interval=cfg.poll_interval
            await conn.update_poll_interval(cfg.poll_interval)
        if need_reconnect:
            conn.auto_reconnect=True; await conn.disconnect(); ok=await conn.connect()
            return web.json_response({"ok":ok,"reconnected":True,"error":conn.error})
        return web.json_response({"ok":True,"reconnected":False})
    async def h_logs(req):
        since=int(req.rel_url.query.get("since",0)); return web.json_response(log_buf.since(since))
    for r in [
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
        try: cfg.poll_interval=float(iv)
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
async def main(cfg):
    log_buf = LogBuffer()
    logging.getLogger().addHandler(MemoryLogHandler(log_buf))
    conn   = create_connection(cfg)
    poller = TagPoller(conn, cfg)
    await conn.connect()
    if not conn.connected:
        log.warning("Initial connection failed — retrying in background")
    if cfg.slc_path:
        try:
            tags=load_tags_from_slc(cfg.slc_path)
            poller.watch(tags)
            log.info(f"Loaded {len(tags)} addresses from SLC file")
        except Exception as e:
            log.warning(f"SLC file load failed: {e}")
    poller_task=asyncio.create_task(poller.run())
    ws_server=None
    if WS_OK:
        ws_server=await websockets.serve(
            lambda ws:ws_handler(ws,poller,conn,cfg),"0.0.0.0",cfg.port_ws)
        log.info(f"WebSocket → ws://localhost:{cfg.port_ws}")
    http_runner=None
    if AIOHTTP_OK:
        app=build_http_app(conn,poller,cfg,log_buf)
        runner=web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner,"0.0.0.0",cfg.port_http).start()
        http_runner=runner
        log.info(f"HTTP REST  → http://localhost:{cfg.port_http}")
    print(f"\n{'-'*55}")
    print(f"  Bridge started. Open plc-tracer-500.html")
    print(f"  WebSocket: ws://localhost:{cfg.port_ws}")
    print(f"{'-'*55}\n  Ctrl+C to stop\n")
    try:
        await asyncio.Future()
    except (KeyboardInterrupt,asyncio.CancelledError):
        pass
    finally:
        poller.stop(); poller_task.cancel()
        if ws_server: ws_server.close()
        if http_runner: await http_runner.cleanup()
        await conn.disconnect()


def parse_args():
    p=argparse.ArgumentParser(description="PLC Tracer 500 Bridge — SLC 500 / MicroLogix")
    p.add_argument("--ip",default=None)
    p.add_argument("--slot",type=int,default=0)
    p.add_argument("--rslinx",action="store_true")
    p.add_argument("--type",default=None,choices=["slc","logix"],help="Controller family: slc (default) | logix (ControlLogix/CompactLogix)")
    p.add_argument("--interval",type=float,default=POLL_IV)
    p.add_argument("--slc-gap-bytes",type=int,default=None,dest="slc_gap_bytes",
                   help="SLC block reads: unwanted bytes worth carrying inside a "
                        "block to save one request. Low for a serial-gateway link "
                        "(bytes cost wire time), high for direct Ethernet "
                        "(requests cost scan time). Default 16.")
    p.add_argument("--conn-groups",type=int,default=None,dest="conn_groups",
                   help="Parallel CIP connections the watch list is spread over "
                        "(0 = auto: 4 for logix, 2 for slc). More connections = "
                        "overlapping round-trips = shorter poll cycle.")
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
    cfg=Config()
    if args.config: cfg.load(args.config)
    if args.ip:           cfg.ip=args.ip
    if args.slot:         cfg.slot=args.slot
    if args.rslinx:       cfg.via_rslinx=True
    if args.type:
        cfg.controller_type=args.type
        cfg.processor_type="Logix5000" if args.type=="logix" else cfg.processor_type
    if args.interval:     cfg.poll_interval=args.interval
    if args.conn_groups is not None: cfg.conn_groups=args.conn_groups
    if args.slc_gap_bytes is not None: cfg.slc_gap_bytes=args.slc_gap_bytes
    if args.slc:          cfg.slc_path=args.slc
    if args.ws_port:      cfg.port_ws=args.ws_port
    if args.http_port:    cfg.port_http=args.http_port
    if not args.ip and not args.config and not args.no_interactive:
        interactive_setup(cfg)
    try:
        asyncio.run(main(cfg))
    except KeyboardInterrupt:
        print("\nBye.")
