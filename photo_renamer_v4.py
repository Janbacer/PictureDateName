"""
Photo & Video Date Renamer
Rename photos and videos by the date they were taken, and fix their date metadata,
with live preview and undo.

Requirements:  pip install pillow
"""
import os
import re
import threading
import tkinter as tk
from datetime import datetime, timedelta, timezone
from tkinter import filedialog, messagebox, ttk

from PIL import ExifTags, Image

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif", ".heic", ".webp"}
VIDEO_EXTS = {".mp4", ".m4v", ".mov", ".3gp", ".mpg", ".mpeg", ".avi", ".mkv", ".wmv", ".flv",
              ".webm", ".mts", ".m2ts"}
QUICKTIME_EXTS = {".mp4", ".m4v", ".mov", ".3gp"}  # formats we can read video metadata from
ALL_EXTS = IMAGE_EXTS | VIDEO_EXTS

# Most reliable first. Modified comes before created because copying a file
# keeps its modified date but resets its created date to the copy time.
SOURCES = {
    "apple": "Camera capture date",
    "exif": "Photo date taken (EXIF)",
    "video": "Video media created",
    "filename": "Date in filename",
    "modified": "File modified",
    "created": "File created",
}
DEFAULT_ORDER = list(SOURCES)
DEFAULT_ON = {"apple": True, "exif": True, "video": True, "filename": True,
              "modified": False, "created": False}
CUTOFF = datetime(2000, 1, 1)

DATE_FORMATS = ["%Y%m%d_%H%M%S", "%Y-%m-%d_%H-%M-%S", "%Y-%m-%d %H.%M.%S", "%Y%m%d", "%Y-%m-%d"]


# ================================================================ date readers
def read_exif(path):
    """Photo 'Date taken' (EXIF DateTimeOriginal), naive local time."""
    if os.path.splitext(path)[1].lower() not in IMAGE_EXTS:
        return None
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            data = dict(exif)
            try:  # DateTimeOriginal lives in the EXIF sub-IFD
                data.update(exif.get_ifd(0x8769))
            except Exception:
                pass
        for name in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
            for tag_id, value in data.items():
                if ExifTags.TAGS.get(tag_id) == name and value:
                    return datetime.strptime(str(value).strip()[:19], "%Y:%m:%d %H:%M:%S")
    except Exception:
        pass
    return None


def _atoms(f, start, end):
    """Yield (type, data_start, data_end) for QuickTime/MP4 atoms in [start, end)."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return
        size, kind, hdr = int.from_bytes(head[:4], "big"), head[4:8], 8
        if size == 1:
            size, hdr = int.from_bytes(f.read(8), "big"), 16
        elif size == 0:
            size = end - pos
        if size < hdr:
            return
        yield kind, pos + hdr, min(pos + size, end)
        pos += size


def _find_moov(f):
    f.seek(0, 2)
    return next(((s, e) for k, s, e in _atoms(f, 0, f.tell()) if k == b"moov"), None)


def read_video(path):
    """Video 'Media created' (mvhd creation time), aware UTC. Same value Windows shows."""
    if os.path.splitext(path)[1].lower() not in QUICKTIME_EXTS:
        return None
    try:
        with open(path, "rb") as f:
            moov = _find_moov(f)
            if not moov:
                return None
            for k, s, _ in _atoms(f, *moov):
                if k == b"mvhd":
                    f.seek(s)
                    version = f.read(4)[0]
                    secs = int.from_bytes(f.read(8 if version == 1 else 4), "big")
                    if secs:
                        return datetime(1904, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=secs)
    except Exception:
        pass
    return None


def _parse_meta_date(text):
    """Parse ISO-like metadata dates such as 2026-09-18T12:54:58+0100."""
    text = text.strip().rstrip("\0")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S%z",
                "%Y-%m-%dT%H:%M:%S", "%Y:%m:%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:32], fmt)
        except ValueError:
            pass
    return None


def _capture_fields(f, moov):
    """Locate capture-date text fields in a video: list of (offset, length, text).

    Places phones use:
      - moov/meta keys  'com.apple.quicktime.creationdate'  (iPhone .mov)
      - moov/udta/date                                      (iPhone .mp4 exports)
      - moov/udta/\xa9day                                   (other cameras/apps)
    """
    fields, metas = [], []
    for k, s, e in _atoms(f, *moov):
        if k == b"meta":
            metas.append((s, e))
        elif k == b"udta":
            for k2, s2, e2 in _atoms(f, s, e):
                if k2 == b"meta":
                    metas.append((s2, e2))
                elif k2 in (b"date", b"\xa9day") and e2 - s2 <= 64:
                    f.seek(s2)
                    raw = f.read(e2 - s2)
                    if k2 == b"\xa9day" and len(raw) > 4 and not raw[:1].isdigit():
                        s2, raw = s2 + 4, raw[4:]  # QuickTime text: 2-byte length + 2-byte language
                    fields.append((s2, len(raw), raw.decode("utf-8", "ignore")))
    for s, e in metas:
        f.seek(s)
        if f.read(4) == b"\0\0\0\0":  # MP4-style meta has version/flags
            s += 4
        children = {k: (cs, ce) for k, cs, ce in _atoms(f, s, e)}
        if b"keys" not in children or b"ilst" not in children:
            continue
        f.seek(children[b"keys"][0] + 4)
        keys = {}
        for i in range(1, int.from_bytes(f.read(4), "big") + 1):
            size = int.from_bytes(f.read(4), "big")
            f.read(4)  # namespace
            keys[i] = f.read(max(size - 8, 0)).decode("utf-8", "ignore")
        for k, cs, ce in _atoms(f, *children[b"ilst"]):
            if keys.get(int.from_bytes(k, "big")) != "com.apple.quicktime.creationdate":
                continue
            for k2, ds, de in _atoms(f, cs, ce):
                if k2 == b"data" and de - ds - 8 <= 64:
                    f.seek(ds + 8)  # skip type + locale
                    fields.append((ds + 8, de - ds - 8, f.read(de - ds - 8).decode("utf-8", "ignore")))
    return fields


def read_capture(path):
    """Camera capture time stored as text in the video (incl. time zone). Earliest wins."""
    if os.path.splitext(path)[1].lower() not in QUICKTIME_EXTS:
        return None
    try:
        with open(path, "rb") as f:
            moov = _find_moov(f)
            if not moov:
                return None
            found = [dt for _, _, text in _capture_fields(f, moov) if (dt := _parse_meta_date(text))]
    except Exception:
        return None
    return min(found, key=to_local) if found else None


FILENAME_PATTERNS = [
    # 20251026_143012 / 2025-10-26 14.30.12 / 20251026-143012123
    re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})[ _T-]?(\d{2})[-_.:]?(\d{2})[-_.:]?(\d{2})(?:\d{1,3})?(?!\d)"),
    # 20251026 / 2025-10-26 (date only, e.g. WhatsApp)
    re.compile(r"(?<!\d)((?:19|20)\d{2})[-_.]?(\d{2})[-_.]?(\d{2})(?!\d)"),
]


def read_filename_full(path):
    """Return (datetime, has_time) or (None, False)."""
    name = os.path.splitext(os.path.basename(path))[0]
    for i, pat in enumerate(FILENAME_PATTERNS):
        for m in pat.finditer(name):
            try:
                return datetime(*map(int, m.groups())), i == 0
            except ValueError:
                continue
    return None, False


READERS = {
    "apple": read_capture,
    "exif": read_exif,
    "video": read_video,
    "filename": lambda p: read_filename_full(p)[0],
    "modified": lambda p: datetime.fromtimestamp(os.path.getmtime(p)),
    "created": lambda p: datetime.fromtimestamp(os.path.getctime(p)),  # creation time on Windows
}


def read_all_dates(path):
    dates = {key: fn(path) for key, fn in READERS.items()}
    dates["filename_has_time"] = read_filename_full(path)[1]
    return dates


def to_local(dt):
    """Aware datetimes -> naive local time. Naive ones are already local."""
    return dt.astimezone().replace(tzinfo=None) if dt.tzinfo else dt


# ================================================================ metadata writers
JPEG_EXTS = {".jpg", ".jpeg"}
EPOCH_1904 = datetime(1904, 1, 1, tzinfo=timezone.utc)


def _atomic_write(path, data):
    tmp = path + ".tmp_renamer"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _swap_exif_segment(data, new_segment):
    """Remove the Exif APP1 segment(s) of a JPEG and insert new_segment (bytes, may be empty).
    Returns (new_data, removed_bytes)."""
    if data[:2] != b"\xff\xd8":
        raise ValueError("not a valid JPEG")
    pos, removed, out = 2, b"", bytearray(data[:2])
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            raise ValueError("damaged JPEG header")
        marker = data[pos + 1]
        if marker == 0xFF:
            out.append(0xFF)
            pos += 1
            continue
        if marker == 0xDA or 0xD0 <= marker <= 0xD9:  # start of image data
            break
        end = pos + 2 + int.from_bytes(data[pos + 2:pos + 4], "big")
        if marker == 0xE1 and data[pos + 4:pos + 10] == b"Exif\0\0":
            removed += data[pos:end]
        else:
            out += data[pos:end]
        pos = end
    out += data[pos:]
    at = 2  # after SOI, or after a JFIF APP0 segment
    if out[2:4] == b"\xff\xe0":
        at = 4 + int.from_bytes(out[4:6], "big")
    out[at:at] = new_segment
    return bytes(out), removed


def write_jpeg_date(path, dt):
    """Set EXIF Date taken (lossless, image data untouched). Returns undo bytes."""
    text = dt.strftime("%Y:%m:%d %H:%M:%S")
    with Image.open(path) as img:
        exif = img.getexif()
        sub = exif.get_ifd(0x8769)
    exif[0x0132] = text                    # DateTime
    sub[0x9003] = text                     # DateTimeOriginal  -> "Date taken"
    sub[0x9004] = text                     # DateTimeDigitized
    payload = exif.tobytes()
    if not payload.startswith(b"Exif"):
        payload = b"Exif\0\0" + payload
    if len(payload) + 2 > 0xFFFF:
        raise ValueError("EXIF block too large")
    segment = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    with open(path, "rb") as f:
        data = f.read()
    new_data, removed = _swap_exif_segment(data, segment)
    _atomic_write(path, new_data)
    return removed


def restore_jpeg(path, original_segment):
    with open(path, "rb") as f:
        data = f.read()
    _atomic_write(path, _swap_exif_segment(data, original_segment)[0])


def _video_time_fields(f, moov):
    """(offset, size) of creation + modification times in mvhd, tkhd and mdhd."""
    fields = []

    def header(s):
        f.seek(s)
        n = 8 if f.read(1)[0] == 1 else 4
        fields.extend([(s + 4, n), (s + 4 + n, n)])

    for k, s, e in _atoms(f, *moov):
        if k == b"mvhd":
            header(s)
        elif k == b"trak":
            for k2, s2, e2 in _atoms(f, s, e):
                if k2 == b"tkhd":
                    header(s2)
                elif k2 == b"mdia":
                    for k3, s3, _ in _atoms(f, s2, e2):
                        if k3 == b"mdhd":
                            header(s3)
    return fields


def _format_like(old, dt):
    """Format dt (naive local) in the same style as an existing capture-date string."""
    old = old.rstrip("\0")
    local = dt.astimezone()
    if old.endswith("Z"):
        text = local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + "Z"
    else:
        off = local.strftime("%z")
        if re.search(r"[+-]\d{2}:\d{2}$", old):
            off = off[:3] + ":" + off[3:]
        text = local.strftime("%Y-%m-%dT%H:%M:%S") + off
    if re.search(r":\d{2}\.\d+", old):
        frac = re.search(r"\.(\d+)", old).group(1)
        text = text[:19] + "." + "0" * len(frac) + text[19:]
    return text


def write_video_date(path, dt):
    """Set Media created (and capture-date text) in place. Returns undo list."""
    utc = dt.astimezone(timezone.utc)
    secs = int((utc - EPOCH_1904).total_seconds())
    undo = []
    with open(path, "r+b") as f:
        moov = _find_moov(f)
        if not moov:
            raise ValueError("no movie header found")
        changes = [(off, secs.to_bytes(n, "big")) for off, n in _video_time_fields(f, moov)]
        for off, n, old in _capture_fields(f, moov):
            new = _format_like(old, dt).encode()
            new += b"\0" * (n - len(new)) if len(new) < n and old.endswith("\0") else b""
            if len(new) == n:  # only same-length replacements keep the file valid
                changes.append((off, new))
        for off, new in changes:
            f.seek(off)
            undo.append((off, f.read(len(new))))
            f.seek(off)
            f.write(new)
    return undo


def restore_video(path, undo):
    with open(path, "r+b") as f:
        for off, old in reversed(undo):
            f.seek(off)
            f.write(old)


def set_file_times(path, modified=None, created=None):
    """modified/created: POSIX timestamps. Created time can only be set on Windows."""
    if modified is not None:
        os.utime(path, (os.stat(path).st_atime, modified))
    if created is not None and os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        k32.CreateFileW.restype = wintypes.HANDLE
        # FILE_WRITE_ATTRIBUTES, share all, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS
        h = k32.CreateFileW(path, 0x100, 0x7, None, 3, 0x02000000, None)
        if h in (None, wintypes.HANDLE(-1).value):
            raise OSError("cannot open file to set created date")
        try:
            ft = int((created + 11644473600) * 10_000_000)
            ctime = wintypes.FILETIME(ft & 0xFFFFFFFF, ft >> 32)
            if not k32.SetFileTime(wintypes.HANDLE(h), ctypes.byref(ctime), None, None):
                raise OSError("SetFileTime failed")
        finally:
            k32.CloseHandle(wintypes.HANDLE(h))


# ================================================================ app
THEMES = {
    "dark": dict(bg="#16181d", card="#1f2229", field="#2a2e37", border="#353a45",
                 fg="#e6e8ec", muted="#8b93a1", accent="#3b82f6", accent_hover="#2563eb",
                 accent_off="#2b3f63", head="#2a2e37", select="#334155",
                 btn="#2a2e37", btn_hover="#353a45", skip="#f87171"),
    "light": dict(bg="#f5f6f8", card="#ffffff", field="#ffffff", border="#d1d5db",
                  fg="#111827", muted="#6b7280", accent="#2563eb", accent_hover="#1d4ed8",
                  accent_off="#93c5fd", head="#e5e7eb", select="#dbeafe",
                  btn="#e5e7eb", btn_hover="#d1d5db", skip="#b91c1c"),
}
FONT = "Segoe UI"


class RenamerApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Photo & Video Date Renamer")
        self.geometry("1180x660")
        self.minsize(980, 540)
        self.theme = "dark"

        self.files = []        # paths
        self.dates = {}        # path -> {source: datetime|None}
        self.plan = []         # (old_path, new_path|None, tag)
        self.undo_stack = []   # [[(new_path, old_path), ...], ...]
        self.meta_win = None

        self.order = list(DEFAULT_ORDER)
        self.enabled = {k: tk.BooleanVar(value=v) for k, v in DEFAULT_ON.items()}
        self.mode = tk.StringVar(value="earliest")
        self.img_prefix = tk.StringVar(value="IMG_")
        self.vid_prefix = tk.StringVar(value="VID_")
        self.date_fmt = tk.StringVar(value=DATE_FORMATS[0])
        self.recursive = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Add files or a folder to get started.")

        self._style()
        self._build()
        for var in (self.mode, self.img_prefix, self.vid_prefix, self.date_fmt, *self.enabled.values()):
            var.trace_add("write", lambda *_: self.refresh_preview())
        self.refresh_preview()

    # ------------------------------------------------------------ look
    def _style(self):
        t = THEMES[self.theme]
        self.configure(bg=t["bg"])
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", font=(FONT, 10), background=t["bg"], foreground=t["fg"],
                    bordercolor=t["border"], darkcolor=t["bg"], lightcolor=t["bg"],
                    troughcolor=t["bg"], focuscolor=t["bg"], selectbackground=t["select"],
                    selectforeground=t["fg"], fieldbackground=t["field"], insertcolor=t["fg"])
        s.configure("TFrame", background=t["bg"])
        s.configure("Card.TFrame", background=t["card"])
        s.configure("TLabel", background=t["bg"], foreground=t["fg"])
        s.configure("Card.TLabel", background=t["card"], foreground=t["fg"])
        s.configure("Title.TLabel", font=(FONT + " Semibold", 16))
        s.configure("Sub.TLabel", foreground=t["muted"])
        s.configure("H.TLabel", font=(FONT + " Semibold", 11), background=t["card"])
        s.configure("Hint.TLabel", foreground=t["muted"], background=t["card"], font=(FONT, 9))
        for name, bg in (("TCheckbutton", t["bg"]), ("Card.TCheckbutton", t["card"]),
                         ("Card.TRadiobutton", t["card"])):
            s.configure(name, background=bg, foreground=t["fg"], indicatorbackground=t["field"],
                        indicatorforeground=t["fg"], upperbordercolor=t["border"],
                        lowerbordercolor=t["border"])
            s.map(name, background=[("active", bg)],
                  indicatorbackground=[("selected", t["accent"]), ("pressed", t["accent"])],
                  indicatorforeground=[("selected", "white")])
        s.configure("TButton", padding=(10, 5), background=t["btn"], foreground=t["fg"],
                    bordercolor=t["border"], lightcolor=t["btn"], darkcolor=t["btn"])
        s.map("TButton", background=[("disabled", t["card"]), ("active", t["btn_hover"])],
              foreground=[("disabled", t["muted"])])
        s.configure("Arrow.TButton", padding=(2, 0), font=(FONT, 8), width=2)
        s.configure("Accent.TButton", background=t["accent"], foreground="white",
                    font=(FONT + " Semibold", 10), padding=(16, 8), borderwidth=0,
                    lightcolor=t["accent"], darkcolor=t["accent"])
        s.map("Accent.TButton", background=[("disabled", t["accent_off"]), ("active", t["accent_hover"])],
              foreground=[("disabled", t["muted"])])
        for w in ("TEntry", "TCombobox"):
            s.configure(w, fieldbackground=t["field"], foreground=t["fg"], background=t["btn"],
                        bordercolor=t["border"], lightcolor=t["field"], darkcolor=t["field"],
                        arrowcolor=t["fg"], insertcolor=t["fg"], padding=4)
            s.map(w, fieldbackground=[("readonly", t["field"])], bordercolor=[("focus", t["accent"])])
        self.option_add("*TCombobox*Listbox.background", t["field"])
        self.option_add("*TCombobox*Listbox.foreground", t["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", t["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", "white")
        s.configure("TSeparator", background=t["border"])
        s.configure("Vertical.TScrollbar", background=t["btn"], troughcolor=t["card"],
                    bordercolor=t["card"], arrowcolor=t["muted"], lightcolor=t["btn"], darkcolor=t["btn"])
        s.map("Vertical.TScrollbar", background=[("active", t["btn_hover"])])
        s.configure("Treeview", rowheight=28, background=t["card"], fieldbackground=t["card"],
                    foreground=t["fg"], borderwidth=0, lightcolor=t["card"], darkcolor=t["card"])
        s.configure("Treeview.Heading", font=(FONT + " Semibold", 10), background=t["head"],
                    foreground=t["muted"], relief="flat", bordercolor=t["head"],
                    lightcolor=t["head"], darkcolor=t["head"], padding=(6, 6))
        s.map("Treeview.Heading", background=[("active", t["head"])])
        s.configure("Status.TLabel", background=t["head"], foreground=t["muted"], padding=(18, 5))

    def _apply_tree_colors(self):
        t = THEMES[self.theme]
        self.tree.tag_configure("ok", foreground=t["fg"])
        self.tree.tag_configure("same", foreground=t["muted"])
        self.tree.tag_configure("skip", foreground=t["skip"])

    def toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        self._style()
        self._apply_tree_colors()
        self.theme_btn.configure(text="Light mode" if self.theme == "dark" else "Dark mode")
        if self.meta_win and self.meta_win.winfo_exists():
            self.meta_win.apply_theme()

    def open_metadata(self):
        if self.meta_win and self.meta_win.winfo_exists():
            self.meta_win.lift()
            return
        self.meta_win = MetadataWindow(self)

    # ------------------------------------------------------------ layout
    def _build(self):
        # header + toolbar
        top = ttk.Frame(self, padding=(18, 14, 18, 8))
        top.pack(fill="x")
        titles = ttk.Frame(top)
        titles.pack(side="left")
        ttk.Label(titles, text="Photo & Video Date Renamer", style="Title.TLabel").pack(anchor="w")
        ttk.Label(titles, text="Choose where the date comes from, check the preview, then rename.",
                  style="Sub.TLabel").pack(anchor="w")
        self.theme_btn = ttk.Button(top, text="Light mode", command=self.toggle_theme)
        self.theme_btn.pack(side="right", anchor="n")

        bar = ttk.Frame(self, padding=(18, 0, 18, 8))
        bar.pack(fill="x")
        ttk.Button(bar, text="Add files", command=self.add_files).pack(side="left")
        ttk.Button(bar, text="Add folder", command=self.add_folder).pack(side="left", padx=(6, 0))
        ttk.Checkbutton(bar, text="Include subfolders", variable=self.recursive).pack(side="left", padx=12)
        ttk.Button(bar, text="Clear list", command=self.clear).pack(side="right")
        ttk.Button(bar, text="Fix metadata...", command=self.open_metadata).pack(side="right", padx=6)

        ttk.Label(self, textvariable=self.status, style="Status.TLabel").pack(fill="x", side="bottom")

        body = ttk.Frame(self, padding=(18, 0, 18, 14))
        body.pack(fill="both", expand=True)

        # ---- sidebar
        side = ttk.Frame(body, style="Card.TFrame", padding=16)
        side.pack(side="left", fill="y")

        ttk.Label(side, text="Date source", style="H.TLabel").pack(anchor="w")
        modes = ttk.Frame(side, style="Card.TFrame")
        modes.pack(anchor="w", pady=(6, 2))
        ttk.Radiobutton(modes, text="Earliest date", value="earliest", variable=self.mode,
                        style="Card.TRadiobutton").pack(side="left")
        ttk.Radiobutton(modes, text="First found", value="priority", variable=self.mode,
                        style="Card.TRadiobutton").pack(side="left", padx=(14, 0))
        self.mode_hint = ttk.Label(side, style="Hint.TLabel", wraplength=260, justify="left")
        self.mode_hint.pack(anchor="w", pady=(0, 8))
        self.src_frame = ttk.Frame(side, style="Card.TFrame")
        self.src_frame.pack(fill="x")
        self._draw_sources()

        ttk.Separator(side).pack(fill="x", pady=14)
        ttk.Label(side, text="Name format", style="H.TLabel").pack(anchor="w", pady=(0, 6))
        grid = ttk.Frame(side, style="Card.TFrame")
        grid.pack(fill="x")
        for row, (label, widget) in enumerate((
                ("Photo prefix", ttk.Entry(grid, textvariable=self.img_prefix, width=12)),
                ("Video prefix", ttk.Entry(grid, textvariable=self.vid_prefix, width=12)),
                ("Date format", ttk.Combobox(grid, textvariable=self.date_fmt, values=DATE_FORMATS, width=18)))):
            ttk.Label(grid, text=label, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=3)
            widget.grid(row=row, column=1, sticky="w", padx=(10, 0), pady=3)
        self.example = ttk.Label(side, style="Hint.TLabel")
        self.example.pack(anchor="w", pady=(8, 0))

        ttk.Frame(side, style="Card.TFrame").pack(fill="both", expand=True)  # spacer
        self.rename_btn = ttk.Button(side, text="Rename files", style="Accent.TButton",
                                     command=self.do_rename, state="disabled")
        self.rename_btn.pack(fill="x")
        self.undo_btn = ttk.Button(side, text="Undo last rename", command=self.undo, state="disabled")
        self.undo_btn.pack(fill="x", pady=(6, 0))

        # ---- preview table
        main = ttk.Frame(body, style="Card.TFrame", padding=8)
        main.pack(side="left", fill="both", expand=True, padx=(12, 0))
        cols = (("old", "Current name", 250), ("new", "New name", 250),
                ("date", "Date", 140), ("source", "From", 160))
        self.tree = ttk.Treeview(main, columns=[c[0] for c in cols], show="headings", selectmode="none")
        for key, text, width in cols:
            self.tree.heading(key, text=text, anchor="w")
            self.tree.column(key, width=width, minwidth=80, anchor="w", stretch=key in ("old", "new"))
        sb = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self._apply_tree_colors()

    def _draw_sources(self):
        for w in self.src_frame.winfo_children():
            w.destroy()
        last = len(self.order) - 1
        for i, key in enumerate(self.order):
            ttk.Checkbutton(self.src_frame, text=SOURCES[key], variable=self.enabled[key],
                            style="Card.TCheckbutton").grid(row=i, column=0, sticky="w", pady=2)
            up = ttk.Button(self.src_frame, text="▲", style="Arrow.TButton",
                            command=lambda k=key: self.move(k, -1))
            down = ttk.Button(self.src_frame, text="▼", style="Arrow.TButton",
                              command=lambda k=key: self.move(k, 1))
            up.grid(row=i, column=1, padx=(12, 2))
            down.grid(row=i, column=2)
            if i == 0:
                up.state(["disabled"])
            if i == last:
                down.state(["disabled"])
        self.src_frame.columnconfigure(0, weight=1)

    def move(self, key, step):
        i = self.order.index(key)
        j = i + step
        if 0 <= j < len(self.order):
            self.order[i], self.order[j] = self.order[j], self.order[i]
            self._draw_sources()
            self.refresh_preview()

    # ------------------------------------------------------------ files
    def add_files(self):
        pattern = " ".join(f"*{e}" for e in sorted(ALL_EXTS))
        paths = filedialog.askopenfilenames(title="Select photos and videos",
                                            filetypes=[("Photos & videos", pattern), ("All files", "*.*")])
        self._load(paths)

    def add_folder(self):
        folder = filedialog.askdirectory(title="Select folder")
        if not folder:
            return
        if self.recursive.get():
            paths = [os.path.join(r, n) for r, _, names in os.walk(folder) for n in names]
        else:
            paths = [os.path.join(folder, n) for n in os.listdir(folder)]
        self._load(paths)

    def clear(self):
        self.files, self.dates = [], {}
        self.refresh_preview()
        self.status.set("List cleared.")

    def _load(self, paths):
        new = []
        for p in paths:
            p = os.path.normpath(p)
            if os.path.isfile(p) and os.path.splitext(p)[1].lower() in ALL_EXTS and p not in self.dates:
                new.append(p)
        if not new:
            self.status.set("No new supported files found.")
            return
        self.rename_btn.configure(state="disabled")

        def work():
            for i, p in enumerate(new, 1):
                self.dates[p] = read_all_dates(p)
                if i % 10 == 0 or i == len(new):
                    self.after(0, self.status.set, f"Reading dates... {i}/{len(new)}")
            self.files.extend(new)
            self.after(0, self.refresh_preview)

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------ date choice
    def pick_date(self, path, mode=None):
        info = self.dates.get(path, {})
        found = [(to_local(info[k]), k) for k in self.order
                 if self.enabled[k].get() and isinstance(info.get(k), datetime)]

        if (mode or self.mode.get()) == "priority":
            return found[0] if found else (None, None)

        # earliest between 2000-01-01 and now (drops 1970/1980 reset dates and future dates)
        now = datetime.now()
        found = [c for c in found if CUTOFF <= c[0] <= now]
        if not found:
            return None, None
        dt, key = min(found, key=lambda c: c[0])
        # a date-only filename (e.g. WhatsApp) reads as 00:00:00 and would always win
        # that day; prefer another source with the same day and a real time
        if key == "filename" and not info.get("filename_has_time"):
            same_day = [c for c in found if c[1] != "filename" and c[0].date() == dt.date()]
            if same_day:
                dt, key = min(same_day, key=lambda c: c[0])
        return dt, key

    # ------------------------------------------------------------ preview
    def refresh_preview(self):
        try:
            sample = datetime(2025, 10, 26, 14, 30, 12).strftime(self.date_fmt.get())
            self.example.configure(text=f"Example:  {self.img_prefix.get()}{sample}.jpg")
        except Exception:
            self.example.configure(text="Example:  invalid date format")
        self.mode_hint.configure(
            text="Uses the earliest of the checked dates between 2000 and today."
            if self.mode.get() == "earliest" else
            "Uses the first checked source (top to bottom) that has a date.")

        self.tree.delete(*self.tree.get_children())
        self.plan, taken = [], set()
        counts = {"ok": 0, "same": 0, "skip": 0}

        for path in sorted(self.files, key=lambda p: os.path.basename(p).lower()):
            folder, old = os.path.split(path)
            ext = os.path.splitext(old)[1].lower()
            dt, src = self.pick_date(path)
            if not dt:
                self.plan.append((path, None, "skip"))
                self.tree.insert("", "end", values=(old, "(no date found)", "", ""), tags=("skip",))
                counts["skip"] += 1
                continue
            prefix = self.img_prefix.get() if ext in IMAGE_EXTS else self.vid_prefix.get()
            try:
                stem = prefix + dt.strftime(self.date_fmt.get())
            except Exception:
                stem = prefix + dt.strftime(DATE_FORMATS[0])
            stem = re.sub(r'[\\/:*?"<>|]', "-", stem)
            candidate, n = stem + ext, 1
            while True:
                full = os.path.join(folder, candidate)
                clash = full.lower() in taken or (
                    os.path.exists(full) and os.path.normcase(full) != os.path.normcase(path))
                if not clash:
                    break
                candidate, n = f"{stem}_{n}{ext}", n + 1
            taken.add(os.path.join(folder, candidate).lower())
            tag = "same" if candidate == old else "ok"
            counts[tag] += 1
            self.plan.append((path, os.path.join(folder, candidate), tag))
            self.tree.insert("", "end", tags=(tag,), values=(
                old, "(unchanged)" if tag == "same" else candidate,
                dt.strftime("%d/%m/%Y %H:%M:%S"), SOURCES[src]))

        if self.files:
            self.status.set(f"{len(self.files)} files    {counts['ok']} to rename    "
                            f"{counts['same']} unchanged    {counts['skip']} without date")
        self.rename_btn.configure(state="normal" if counts["ok"] else "disabled")
        if self.meta_win and self.meta_win.winfo_exists():
            self.meta_win.refresh()

    # ------------------------------------------------------------ actions
    def do_rename(self):
        todo = [(o, n) for o, n, tag in self.plan if tag == "ok"]
        if not todo or not messagebox.askyesno("Rename", f"Rename {len(todo)} files?"):
            return
        done, errors = [], []
        for old, new in todo:
            try:
                if os.path.exists(new):
                    raise FileExistsError(os.path.basename(new))
                os.rename(old, new)
                done.append((new, old))
            except Exception as e:
                errors.append(f"{os.path.basename(old)}: {e}")
        if done:
            self.undo_stack.append(done)
            self.undo_btn.configure(state="normal")
        self._swap_paths({old: new for new, old in done})
        msg = f"Renamed {len(done)} files."
        if errors:
            messagebox.showwarning("Some files failed", "\n".join(errors[:20]))
            msg += f" {len(errors)} failed."
        self.status.set(msg)

    def undo(self):
        if not self.undo_stack:
            return
        restored = {}
        for new, old in reversed(self.undo_stack.pop()):
            try:
                if not os.path.exists(old):
                    os.rename(new, old)
                    restored[new] = old
            except Exception:
                pass
        self._swap_paths(restored)
        self.undo_btn.configure(state="normal" if self.undo_stack else "disabled")
        self.status.set(f"Undo: restored {len(restored)} files.")

    def _swap_paths(self, mapping):
        """Update internal state after files were renamed on disk."""
        self.files = [mapping.get(p, p) for p in self.files]
        self.dates = {mapping.get(p, p): d for p, d in self.dates.items()}
        for p in mapping.values():  # the filename date changes with the name
            if p in self.dates:
                self.dates[p]["filename"], self.dates[p]["filename_has_time"] = read_filename_full(p)
        self.refresh_preview()


class MetadataWindow(tk.Toplevel):
    """Writes the chosen date into the file so Windows and galleries show it."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.title("Fix date metadata")
        self.geometry("1220x600")
        self.minsize(1000, 480)
        self.plan = []        # (path, new_dt, what_to_write, tag)
        self.undo_stack = []  # [[record, ...], ...]

        self.source = tk.StringVar(value="earliest")
        self.w_content = tk.BooleanVar(value=True)
        self.w_modified = tk.BooleanVar(value=True)
        self.w_created = tk.BooleanVar(value=os.name == "nt")
        self.status = tk.StringVar()

        self._build()
        self.apply_theme()
        for var in (self.source, self.w_content, self.w_modified, self.w_created):
            var.trace_add("write", lambda *_: self.refresh())
        self.refresh()

    def apply_theme(self):
        t = THEMES[self.app.theme]
        self.configure(bg=t["bg"])
        self.tree.tag_configure("ok", foreground=t["fg"])
        self.tree.tag_configure("same", foreground=t["muted"])
        self.tree.tag_configure("skip", foreground=t["skip"])

    def _build(self):
        top = ttk.Frame(self, padding=(18, 14, 18, 8))
        top.pack(fill="x")
        ttk.Label(top, text="Fix date metadata", style="Title.TLabel").pack(anchor="w")
        ttk.Label(top, text="Writes the date into the files in the main list, so Windows and "
                            "photo apps show the right date.", style="Sub.TLabel").pack(anchor="w")
        ttk.Label(self, textvariable=self.status, style="Status.TLabel").pack(fill="x", side="bottom")

        body = ttk.Frame(self, padding=(18, 0, 18, 14))
        body.pack(fill="both", expand=True)
        side = ttk.Frame(body, style="Card.TFrame", padding=16)
        side.pack(side="left", fill="y")

        ttk.Label(side, text="Date to write", style="H.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Radiobutton(side, text="Earliest date found", value="earliest", variable=self.source,
                        style="Card.TRadiobutton").pack(anchor="w")
        ttk.Label(side, text="From the sources ticked in the main window,\n2000 to today.",
                  style="Hint.TLabel").pack(anchor="w", padx=(22, 0), pady=(0, 6))
        ttk.Radiobutton(side, text="Date in filename", value="name", variable=self.source,
                        style="Card.TRadiobutton").pack(anchor="w")
        ttk.Label(side, text="If the name has no time (WhatsApp), the time\nis taken from "
                             "the metadata of the same day.", style="Hint.TLabel").pack(
            anchor="w", padx=(22, 0))

        ttk.Separator(side).pack(fill="x", pady=14)
        ttk.Label(side, text="Write to", style="H.TLabel").pack(anchor="w", pady=(0, 6))
        ttk.Checkbutton(side, text="Date taken / Media created", variable=self.w_content,
                        style="Card.TCheckbutton").pack(anchor="w")
        ttk.Label(side, text="JPEG: EXIF date taken (image not re-encoded)\n"
                             "MP4/MOV: media created + capture date", style="Hint.TLabel").pack(
            anchor="w", padx=(22, 0), pady=(0, 4))
        ttk.Checkbutton(side, text="File modified date", variable=self.w_modified,
                        style="Card.TCheckbutton").pack(anchor="w", pady=2)
        cb = ttk.Checkbutton(side, text="File created date", variable=self.w_created,
                             style="Card.TCheckbutton")
        cb.pack(anchor="w", pady=2)
        if os.name != "nt":
            cb.state(["disabled"])
            ttk.Label(side, text="Windows only", style="Hint.TLabel").pack(anchor="w", padx=(22, 0))

        ttk.Frame(side, style="Card.TFrame").pack(fill="both", expand=True)
        self.write_btn = ttk.Button(side, text="Write metadata", style="Accent.TButton",
                                    command=self.write, state="disabled")
        self.write_btn.pack(fill="x")
        self.undo_btn = ttk.Button(side, text="Undo last write", command=self.undo, state="disabled")
        self.undo_btn.pack(fill="x", pady=(6, 0))

        main = ttk.Frame(body, style="Card.TFrame", padding=8)
        main.pack(side="left", fill="both", expand=True, padx=(12, 0))
        cols = (("file", "File", 200), ("now", "Shown now", 125), ("new", "New date", 125),
                ("from", "From", 145), ("write", "Will write", 175))
        self.tree = ttk.Treeview(main, columns=[c[0] for c in cols], show="headings", selectmode="none")
        for key, text, width in cols:
            self.tree.heading(key, text=text, anchor="w")
            self.tree.column(key, width=width, minwidth=80, anchor="w", stretch=key == "file")
        sb = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    # ---------- plan
    def _new_date(self, path):
        app = self.app
        if self.source.get() == "earliest":
            dt, key = app.pick_date(path, mode="earliest")
            return dt, SOURCES.get(key, "")
        info = app.dates.get(path, {})
        dt = info.get("filename")
        if not dt:
            return None, ""
        if info.get("filename_has_time"):
            return dt, "Date in filename"
        same_day = [to_local(info[k]) for k in ("apple", "exif", "video")
                    if isinstance(info.get(k), datetime) and to_local(info[k]).date() == dt.date()]
        if same_day:
            return min(same_day), "Filename + metadata time"
        return dt, "Date in filename (no time)"

    @staticmethod
    def _shown_now(path, info):
        ext = os.path.splitext(path)[1].lower()
        dt = info.get("exif") if ext in IMAGE_EXTS else info.get("video")
        return to_local(dt) if isinstance(dt, datetime) else None

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        self.plan = []
        counts = {"ok": 0, "same": 0, "skip": 0}
        fmt = "%d/%m/%Y %H:%M:%S"
        for path in sorted(self.app.files, key=lambda p: os.path.basename(p).lower()):
            info = self.app.dates.get(path, {})
            ext = os.path.splitext(path)[1].lower()
            now = self._shown_now(path, info)
            dt, src = self._new_date(path)
            what = []
            if self.w_content.get():
                if ext in JPEG_EXTS:
                    what.append("Date taken")
                elif ext in QUICKTIME_EXTS:
                    what.append("Media created")
            if self.w_modified.get():
                what.append("Modified")
            if self.w_created.get() and os.name == "nt":
                what.append("Created")
            name = os.path.basename(path)
            if not dt:
                tag, note = "skip", "(no date found)"
            elif not what:
                tag, note = "skip", "(nothing to write for this type)"
            else:
                current = {"Date taken": now, "Media created": now,
                           "Modified": info.get("modified"), "Created": info.get("created")}
                correct = all(isinstance(current[w], datetime) and
                              abs((to_local(current[w]) - dt).total_seconds()) < 1 for w in what)
                tag = "same" if correct else "ok"
                note = "(already correct)" if correct else ", ".join(what)
            counts[tag] += 1
            self.plan.append((path, dt, what, tag))
            self.tree.insert("", "end", tags=(tag,), values=(
                name, now.strftime(fmt) if now else "", dt.strftime(fmt) if dt else "", src, note))
        self.status.set(f"{len(self.app.files)} files    {counts['ok']} to update    "
                        f"{counts['same']} already correct    {counts['skip']} skipped"
                        if self.app.files else "Add files in the main window first.")
        self.write_btn.configure(state="normal" if counts["ok"] else "disabled")

    # ---------- actions
    def write(self):
        todo = [(p, dt, what) for p, dt, what, tag in self.plan if tag == "ok"]
        if not todo or not messagebox.askyesno(
                "Write metadata", f"Change the date metadata of {len(todo)} files?\n\n"
                                  "You can undo this while the app is open.", parent=self):
            return
        batch, errors, ok = [], [], 0
        for path, dt, what in todo:
            st = os.stat(path)
            rec = {"path": path, "mtime": st.st_mtime, "ctime": st.st_ctime}
            try:
                if "Date taken" in what:
                    rec["jpeg"] = write_jpeg_date(path, dt)
                if "Media created" in what:
                    rec["video"] = write_video_date(path, dt)
                # the content write touched the file: set or restore its dates
                set_file_times(path,
                               modified=dt.timestamp() if "Modified" in what else st.st_mtime,
                               created=dt.timestamp() if "Created" in what else st.st_ctime)
                batch.append(rec)
                ok += 1
            except Exception as e:
                errors.append(f"{os.path.basename(path)}: {e}")
                if len(rec) > 3:
                    batch.append(rec)  # partly written: keep it undoable
        if batch:
            self.undo_stack.append(batch)
            self.undo_btn.configure(state="normal")
        self._reload([r["path"] for r in batch])
        if errors:
            messagebox.showwarning("Some files failed", "\n".join(errors[:20]), parent=self)
        self.status.set(f"Updated {ok} files." + (f" {len(errors)} failed." if errors else ""))

    def undo(self):
        if not self.undo_stack:
            return
        batch, restored = self.undo_stack.pop(), 0
        for rec in reversed(batch):
            try:
                if "video" in rec:
                    restore_video(rec["path"], rec["video"])
                if "jpeg" in rec:
                    restore_jpeg(rec["path"], rec["jpeg"])
                set_file_times(rec["path"], modified=rec["mtime"], created=rec["ctime"])
                restored += 1
            except Exception:
                pass
        self.undo_btn.configure(state="normal" if self.undo_stack else "disabled")
        self._reload([r["path"] for r in batch])
        self.status.set(f"Undo: restored {restored} files.")

    def _reload(self, paths):
        for p in paths:
            if os.path.exists(p):
                self.app.dates[p] = read_all_dates(p)
        self.app.refresh_preview()  # also refreshes this window


if __name__ == "__main__":
    RenamerApp().mainloop()
