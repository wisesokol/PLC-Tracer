#!/usr/bin/env python3
"""
PLC Bridge V2 — remote module: HMI projects for phones and tablets.

The bridge host keeps the HMI projects. A phone opens  http://<host>:8766/m
in its browser and gets the very same PLC-tracer-HMI.html the host uses, with
one flag set (window.PLC_REMOTE) that switches the page to its remote mode:
view only, projects picked from the list this module serves.

What lives here:
  • the "remote" section of plc_bridge_devices.json (password, project list,
    which saved PLC each project belongs to)
  • build_remote_project(): a light copy of an HMI project — the background
    image scaled down and stored as a separate file, the layout without it,
    the PLC source files next to them
  • RemoteHub: login tokens and the HTTP routes under /m

Nothing in here talks to a PLC. Sessions are started through the callables the
bridge hands over, so this module imports nothing from plc_bridge_500.
"""

import asyncio, base64, io, json, logging, os, re, secrets, shutil, socket, time
from pathlib import Path

log = logging.getLogger("bridge500.remote")

PAGE_NAME      = "PLC-tracer-HMI.html"
LAYOUT_NAME    = "layout.hmi.json"
MANIFEST_NAME  = "remote.json"
DEFAULT_MAX_IMAGE = 2560          # longest side of the phone background, px
_PNG_KEEP_BYTES   = 1_500_000     # a PNG drawing bigger than this goes out as JPEG
_LOGIN_FAIL_DELAY = 1.0           # seconds a wrong password costs


# ─────────────────────────────────────────────────────────────────────────────
# Settings — the "remote" section of plc_bridge_devices.json
# ─────────────────────────────────────────────────────────────────────────────
def slug(text: str) -> str:
    """Project name reduced to something safe for a folder name and a URL."""
    out = re.sub(r"[^0-9A-Za-z_\-]+", "_", str(text or "")).strip("_")
    return out[:40] or "project"


def norm_remote(raw) -> dict:
    """Whatever the file holds → a complete, well-typed settings dict."""
    raw = raw if isinstance(raw, dict) else {}
    projects, seen = [], set()
    for p in raw.get("projects", []) if isinstance(raw.get("projects"), list) else []:
        if not isinstance(p, dict):
            continue
        name = str(p.get("name", "")).strip()
        src  = str(p.get("source", "")).strip()
        if not name or not src:
            continue
        pid = slug(name)
        if pid.lower() in seen:
            continue                      # two names with one folder — first wins
        seen.add(pid.lower())
        projects.append({"name": name, "source": src,
                         "device": str(p.get("device", "")).strip()})
    try:
        max_image = int(raw.get("max_image", DEFAULT_MAX_IMAGE) or DEFAULT_MAX_IMAGE)
    except (TypeError, ValueError):
        max_image = DEFAULT_MAX_IMAGE
    return {
        "enabled":   bool(raw.get("enabled", False)),
        "password":  str(raw.get("password", "") or ""),
        "max_image": max(640, min(8192, max_image)),
        # Optional explicit path to PLC-tracer-HMI.html; empty = look for it
        # next to the bridge and one folder up.
        "page":      str(raw.get("page", "") or "").strip(),
        "projects":  projects,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Light project builder
# ─────────────────────────────────────────────────────────────────────────────
def find_layout(source: Path) -> Path:
    """The .hmi.json a project source means: the file itself, or the newest one
    in the folder (the same rule LOAD HMI uses in the page)."""
    source = Path(source)
    if source.is_file():
        return source
    if not source.is_dir():
        raise FileNotFoundError(f"project source not found: {source}")
    found = [p for p in source.iterdir()
             if p.is_file() and p.name.lower().endswith(".hmi.json")]
    if not found:
        raise FileNotFoundError(f"no .hmi.json in {source}")
    return max(found, key=lambda p: p.stat().st_mtime)


def _light_image(data_url: str, max_side: int):
    """dataURL of the background → (file name, bytes, width, height).

    The layout keeps its coordinates in pixels of the ORIGINAL image, so only
    the bitmap shrinks: the page draws it stretched over the original size.
    """
    try:
        from PIL import Image
    except ImportError as e:
        raise RuntimeError("Pillow is not installed — run: pip install pillow") from e
    m = re.match(r"data:image/([A-Za-z0-9.+\-]+);base64,", data_url[:64])
    if not m:
        raise ValueError("background image is not a base64 data URL")
    raw = base64.b64decode(data_url[m.end():])
    Image.MAX_IMAGE_PIXELS = 250_000_000      # plant drawings are large on purpose
    img = Image.open(io.BytesIO(raw))
    img.load()
    w, h = img.size
    k = max_side / float(max(w, h))
    if k < 1.0:
        img = img.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)
    has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
    is_png = m.group(1).lower() == "png"
    if is_png:
        # A line drawing: PNG stays sharp and small. Photos saved as PNG do not.
        buf = io.BytesIO()
        (img if img.mode in ("RGBA", "RGB", "L", "LA", "P") else img.convert("RGBA")
         ).save(buf, "PNG", optimize=True)
        if has_alpha or buf.tell() <= _PNG_KEEP_BYTES:
            return "bg.png", buf.getvalue(), img.size[0], img.size[1]
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=82, optimize=True, progressive=True)
    return "bg.jpg", buf.getvalue(), img.size[0], img.size[1]


def build_remote_project(name: str, source, out_root, device: str = "",
                         max_image: int = DEFAULT_MAX_IMAGE) -> dict:
    """Write the light copy of one HMI project into out_root/<slug>/.

    Built in a side folder and swapped in at the end, so a phone that is
    loading the project during an update gets the old one or the new one,
    never half of each. Returns the manifest.
    """
    layout_path = find_layout(Path(source))
    src_dir = layout_path.parent
    obj = json.loads(layout_path.read_text(encoding="utf-8"))
    if not isinstance(obj, dict) or obj.get("app") != "PLC-tracer-HMI":
        raise ValueError(f"{layout_path.name} is not a PLC-tracer-HMI layout")

    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    pid   = slug(name)
    final = out_root / pid
    work  = out_root / (pid + ".new")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir()
    try:
        files, image_file, image_px = [], "", [0, 0]
        data_url = obj.pop("image", None)
        if isinstance(data_url, str) and data_url:
            image_file, blob, iw, ih = _light_image(data_url, max_image)
            (work / image_file).write_bytes(blob)
            image_px = [iw, ih]
            obj["imageRemote"] = image_file      # the page loads it by URL
            files.append(image_file)
        # The PDF manual stays on the host: its path is relative to the page
        # there and means nothing on a phone.
        if isinstance(obj.get("docs"), dict):
            obj["docs"] = dict(obj["docs"], enabled=False)
        # PLC source files the layout refers to, matched like the page does:
        # by file name, case-insensitive, in the folder of the .hmi.json.
        by_name = {p.name.lower(): p for p in src_dir.iterdir() if p.is_file()}
        plc, missing = {}, []
        for key in ("project", "prn", "desc"):
            ref = (obj.get("plcFiles") or {}).get(key)
            if not ref:
                continue
            p = by_name.get(str(ref).lower())
            if p is None:
                missing.append(ref)
                continue
            shutil.copyfile(p, work / p.name)
            plc[key] = p.name
            files.append(p.name)
        if "project" not in plc:
            raise FileNotFoundError(
                "PLC project file not found next to the layout: "
                + (", ".join(missing) or "(the layout names none)"))
        (work / LAYOUT_NAME).write_text(
            json.dumps(obj, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        files.append(LAYOUT_NAME)
        manifest = {
            "version":  1,
            "id":       pid,
            "name":     name,
            "device":   device,
            "built":    time.time(),
            "source":   str(layout_path),
            "source_mtime": layout_path.stat().st_mtime,
            "layout":   LAYOUT_NAME,
            "image":    image_file,
            "image_px": image_px,
            "image_orig_px": [int(obj.get("imageW") or 0), int(obj.get("imageH") or 0)],
            "plc":      plc,
            "missing":  missing,
            "files":    files,
            "bytes":    {f: (work / f).stat().st_size for f in files},
        }
        (work / MANIFEST_NAME).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        if final.exists():
            shutil.rmtree(final)
        os.replace(work, final)
        return manifest
    except Exception:
        shutil.rmtree(work, ignore_errors=True)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# The page itself
# ─────────────────────────────────────────────────────────────────────────────
# Nothing of the page is built into the bridge. The folder of remote projects is
# everything a phone is served from: the light projects AND a copy of
# PLC-tracer-HMI.html. Moving the bridge to another PC = the .exe, the settings
# file and that folder.
def find_page_source(explicit: str = "", dirs=()):
    """The working PLC-tracer-HMI.html: the `page` setting, else next to the
    bridge, else one folder up (the repository layout). None when this machine
    has no such file."""
    cands = [Path(explicit)] if explicit else []
    for d in dirs:
        d = Path(d)
        cands += [d / PAGE_NAME, d.parent / PAGE_NAME]
    return next((p for p in cands if p.is_file()), None)


def refresh_page_copy(out_root, explicit: str = "", dirs=()) -> str:
    """Put the current page into the remote projects folder. Called by every
    UPDATE REMOTE PROJECT. Returns a line for the user: "" when the copy was
    already current, otherwise what happened."""
    out_root = Path(out_root)
    dst = out_root / PAGE_NAME
    src = find_page_source(explicit, dirs)
    if src is None:
        return "" if dst.is_file() else (
            f"{PAGE_NAME} not found - put it into the folder {out_root.name} "
            f"(or next to the bridge) for phones to get a page")
    try:
        if dst.is_file() and dst.read_bytes() == src.read_bytes():
            return ""
        out_root.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_suffix(".new")
        shutil.copyfile(src, tmp)
        os.replace(tmp, dst)
        return f"page updated ({dst.stat().st_size // 1024} KB)"
    except OSError as e:
        return f"page NOT updated: {e}"


def read_manifest(out_root, name: str):
    """Manifest of a built project, or None when it was never built."""
    try:
        return json.loads((Path(out_root) / slug(name) / MANIFEST_NAME)
                          .read_text(encoding="utf-8"))
    except Exception:
        return None


def project_state(out_root, proj: dict) -> dict:
    """Is the light copy there, and is it older than its source?"""
    man = read_manifest(out_root, proj["name"])
    state = {"built": 0.0, "ready": False, "stale": False, "error": ""}
    if not man:
        return state
    state["built"] = float(man.get("built") or 0)
    state["ready"] = True
    try:
        src = find_layout(Path(proj["source"]))
        state["stale"] = (str(src) != man.get("source")
                          or src.stat().st_mtime > float(man.get("source_mtime") or 0) + 1e-6)
    except Exception as e:
        state["error"] = str(e)
    return state


# ─────────────────────────────────────────────────────────────────────────────
# Who is local
# ─────────────────────────────────────────────────────────────────────────────
_LOCAL_IPS = {"ts": 0.0, "ips": frozenset()}

def local_addresses() -> frozenset:
    """Every address this machine answers on (refreshed once a minute — a
    Wi-Fi adapter may get its address after the bridge has started)."""
    now = time.monotonic()
    if _LOCAL_IPS["ips"] and now - _LOCAL_IPS["ts"] < 60.0:
        return _LOCAL_IPS["ips"]
    ips = {"127.0.0.1", "::1", "localhost"}
    try:
        host = socket.gethostname()
        ips.update(socket.gethostbyname_ex(host)[2])
        for info in socket.getaddrinfo(host, None):
            ips.add(info[4][0].split("%")[0])
    except Exception:
        pass
    _LOCAL_IPS.update(ts=now, ips=frozenset(ips))
    return _LOCAL_IPS["ips"]


def is_local(addr) -> bool:
    """True for a client on the bridge host itself (any of its own addresses)."""
    ip = addr[0] if isinstance(addr, (tuple, list)) and addr else str(addr or "")
    ip = ip.split("%")[0]
    if ip.startswith("::ffff:"):
        ip = ip[7:]
    return ip.startswith("127.") or ip in local_addresses()


def lan_urls(port: int) -> list:
    """Addresses to type into the phone."""
    out = []
    for ip in sorted(local_addresses()):
        if ":" in ip or ip.startswith("127.") or ip == "localhost":
            continue
        out.append(f"http://{ip}:{port}/m")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Hub — tokens + HTTP routes
# ─────────────────────────────────────────────────────────────────────────────
class RemoteHub:
    """Remote access for one bridge process.

    manager       SessionManager
    dev_file      path of plc_bridge_devices.json (settings are re-read when
                  the file changes, so the GUI's edits apply at once)
    out_root      folder of the built light projects
    find_device   name → saved preset dict | None
    to_config     preset dict → Config
    """

    def __init__(self, manager, dev_file, out_root, find_device, to_config,
                 page_dirs=()):
        self.manager     = manager
        self.dev_file    = Path(dev_file)
        self.out_root    = Path(out_root)
        self.find_device = find_device
        self.to_config   = to_config
        self.page_dirs   = [Path(p) for p in page_dirs]
        self._cfg        = norm_remote(None)
        self._cfg_mtime  = -1.0
        self._tokens     = {}       # token → password it was issued for
        self._start_lock = asyncio.Lock()

    # ── settings ──────────────────────────────────────────────────────────────
    def cfg(self) -> dict:
        try:
            mtime = self.dev_file.stat().st_mtime
        except OSError:
            mtime = 0.0
        if mtime != self._cfg_mtime:
            self._cfg_mtime = mtime
            try:
                raw = json.loads(self.dev_file.read_text(encoding="utf-8")).get("remote")
            except Exception:
                raw = None
            self._cfg = norm_remote(raw)
        return self._cfg

    def active(self) -> bool:
        """Remote access is on only with the box ticked AND a password set."""
        c = self.cfg()
        return bool(c["enabled"] and c["password"])

    # ── tokens ────────────────────────────────────────────────────────────────
    def login(self, password: str):
        c = self.cfg()
        if not self.active() or not password \
           or not secrets.compare_digest(str(password).encode(), c["password"].encode()):
            return None
        tok = secrets.token_urlsafe(24)
        self._tokens[tok] = c["password"]
        if len(self._tokens) > 64:                       # a phone logs in rarely
            for old in list(self._tokens)[:-64]:
                self._tokens.pop(old, None)
        return tok

    def token_ok(self, token: str) -> bool:
        """A token dies with the password it was issued for."""
        return bool(token) and self.active() and \
               self._tokens.get(token) == self.cfg()["password"]

    def ws_allowed(self, remote_address, path: str) -> bool:
        """May this WebSocket client talk to a PLC session?

        The host itself always may. Anyone else needs a token once a remote
        password is set; with no password the bridge behaves as it always did.
        """
        if is_local(remote_address):
            return True
        c = self.cfg()
        if not c["password"]:
            return True
        if not c["enabled"]:
            return False
        m = re.search(r"[?&]k=([^&#]+)", path or "")
        return bool(m) and self.token_ok(m.group(1))

    # ── projects ──────────────────────────────────────────────────────────────
    def projects(self) -> list:
        out = []
        for p in self.cfg()["projects"]:
            st   = project_state(self.out_root, p)
            sess = self.manager.get(p["device"]) if p["device"] else None
            out.append({
                "id":        slug(p["name"]),
                "name":      p["name"],
                "device":    p["device"],
                "ready":     st["ready"],
                "stale":     st["stale"],
                "built":     st["built"],
                "running":   bool(sess and sess.running),
                "connected": bool(sess and sess.conn and sess.conn.connected),
                "recording": bool(sess and sess.recorder.active),
            })
        return out

    def _project(self, pid: str):
        return next((p for p in self.cfg()["projects"]
                     if slug(p["name"]).lower() == str(pid or "").lower()), None)

    async def open_project(self, pid: str) -> dict:
        """Make sure the PLC session of a project is up; say where it listens."""
        proj = self._project(pid)
        if not proj:
            raise KeyError(f"no such project: {pid}")
        man = read_manifest(self.out_root, proj["name"])
        if not man:
            raise RuntimeError("the project was never prepared — press "
                               "UPDATE REMOTE PROJECT in the bridge")
        dev = self.find_device(proj["device"]) if proj["device"] else None
        if not dev:
            raise RuntimeError(f"project “{proj['name']}” is not linked to a saved PLC")
        async with self._start_lock:             # two taps must not start it twice
            sess = self.manager.get(dev["name"])
            if not sess:
                log.info(f"remote: starting PLC session “{dev['name']}” "
                         f"for project “{proj['name']}”")
                sess = await self.manager.start(dev["name"], self.to_config(dev))
        return {
            "id":        slug(proj["name"]),
            "name":      proj["name"],
            "plc":       sess.name,
            "ws_port":   sess.cfg.port_ws,
            "connected": bool(sess.conn and sess.conn.connected),
            "recording": bool(sess.recorder.active),
            "manifest":  {k: man.get(k) for k in
                          ("layout", "image", "plc", "files", "bytes", "built")},
        }

    # ── page ──────────────────────────────────────────────────────────────────
    def page_path(self):
        """The page a phone gets: the working file when this machine has it
        (the development PC), otherwise the copy kept with the remote projects."""
        return (find_page_source(self.cfg()["page"], self.page_dirs)
                or next((p for p in [self.out_root / PAGE_NAME] if p.is_file()), None))

    # ── HTTP ──────────────────────────────────────────────────────────────────
    def add_routes(self, app, web):
        hub = self

        def token_of(req) -> str:
            return req.headers.get("X-Remote-Token") or req.rel_url.query.get("k") or ""

        def guard(req):
            if not hub.active():
                raise web.HTTPForbidden(text="remote access is switched off in the bridge")
            if not hub.token_ok(token_of(req)):
                raise web.HTTPUnauthorized(text="login required")

        async def h_page(req):
            if not hub.active():
                return web.Response(status=403, content_type="text/plain", text=(
                    "Remote access is switched off.\n"
                    "Bridge window → REMOTE: tick “enabled” and set a password."))
            p = hub.page_path()
            if p is None:
                return web.Response(status=500, content_type="text/plain", text=(
                    f"{PAGE_NAME} is missing. "
                    f"Put it into the folder {hub.out_root.name} next to the bridge."))
            html = p.read_text(encoding="utf-8")
            flag = '<script>window.PLC_REMOTE={v:1};</script>'
            i = html.find("<head>")
            html = (html[:i + 6] + flag + html[i + 6:]) if i >= 0 else flag + html
            resp = web.Response(text=html, content_type="text/html", charset="utf-8",
                                headers={"Cache-Control": "no-store"})
            resp.enable_compression()
            return resp

        async def h_login(req):
            try:
                data = await req.json()
            except Exception:
                data = {}
            tok = hub.login(str(data.get("password", "")))
            if not tok:
                await asyncio.sleep(_LOGIN_FAIL_DELAY)
                log.warning(f"remote: wrong password from {req.remote}")
                return web.json_response({"ok": False, "error": "wrong password"}, status=403)
            log.info(f"remote: login from {req.remote}")
            return web.json_response({"ok": True, "token": tok})

        async def h_projects(req):
            guard(req)
            return web.json_response({"projects": hub.projects()},
                                     headers={"Cache-Control": "no-store"})

        async def h_open(req):
            guard(req)
            try:
                data = await req.json()
            except Exception:
                data = {}
            try:
                return web.json_response(dict(await hub.open_project(data.get("id", "")), ok=True))
            except Exception as e:
                log.warning(f"remote: open {data.get('id')!r} failed: {e}")
                return web.json_response({"ok": False, "error": str(e)}, status=409)

        async def h_file(req):
            guard(req)
            proj = hub._project(req.match_info["pid"])
            man  = read_manifest(hub.out_root, proj["name"]) if proj else None
            name = req.match_info["name"]
            # Only what the manifest lists — the URL never becomes a path.
            if not man or name not in (man.get("files") or []):
                raise web.HTTPNotFound()
            path = hub.out_root / slug(proj["name"]) / name
            if not path.is_file():
                raise web.HTTPNotFound()
            return web.FileResponse(path, headers={"Cache-Control": "no-store"})

        app.router.add_get("/m", h_page)
        app.router.add_get("/m/", h_page)
        app.router.add_post("/m/login", h_login)
        app.router.add_get("/m/projects", h_projects)
        app.router.add_post("/m/open", h_open)
        app.router.add_get("/m/p/{pid}/{name}", h_file)


# This file is a module of the bridge, not a program: the bridge window
# (plc_bridge_ui.py) loads it. Started by itself it opens that window, so a
# double click here does the expected thing instead of nothing.
if __name__ == "__main__":
    import runpy
    runpy.run_path(str(Path(__file__).resolve().parent / "plc_bridge_ui.py"),
                   run_name="__main__")
