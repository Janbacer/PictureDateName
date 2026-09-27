"""
Photo & Video Date Renamer
Rename images and videos by a date you choose (EXIF, video metadata,
filename, modified or created date), with a live preview and undo.

Requirements:  pip install pillow hachoir pywin32
"""
import os
import re
import threading
import tkinter as tk
from datetime import datetime, timezone
from tkinter import filedialog, messagebox, ttk

from PIL import ExifTags, Image

try:
    from hachoir.metadata import extractMetadata
    from hachoir.parser import createParser
    HAS_HACHOIR = True
except ImportError:
    HAS_HACHOIR = False

try:  # read dates exactly as Windows Explorer shows them (Windows only)
    import pythoncom
    from win32com.propsys import propsys, pscon
    HAS_WINPROPS = True
except ImportError:
    HAS_WINPROPS = False

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".gif", ".heic", ".webp"}
VIDEO_EXTS = {".mp4", ".mpg", ".mpeg", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".webm", ".mts", ".m2ts", ".3gp"}
ALL_EXTS = IMAGE_EXTS | VIDEO_EXTS

# key -> label shown in the UI
SOURCES = {
    "apple": "iPhone capture date (videos)",
    "exif": "EXIF date taken (photos)",
    "video": "Video metadata date",
    "media": "Windows: Media created / Date taken",
    "filename": "Date in filename (e.g. WhatsApp)",
    "modified": "File modified date",
    "created": "File created date",
}
# Most reliable first. Modified comes before created because copying a file
# keeps its modified date but resets its created date to the copy time.
DEFAULT_ORDER = ["apple", "media", "exif", "video", "filename", "modified", "created"]
CUTOFF = datetime(2000, 1, 1)
DEFAULT_ON = {"apple": True, "media": True, "exif": True, "video": True, "filename": True, "modified": False, "created": False}

DATE_FORMATS = [
    "%Y%m%d_%H%M%S",
    "%Y-%m-%d_%H-%M-%S",
    "%Y-%m-%d %H.%M.%S",
    "%Y%m%d",
    "%Y-%m-%d",
]


# ---------------------------------------------------------------- date readers
def read_exif(path):
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


def read_video(path):
    if not HAS_HACHOIR:
        return None
    try:
        parser = createParser(path)
        if not parser:
            return None
        with parser:
            meta = extractMetadata(parser)
        if meta:
            for key in ("creation_date", "date"):
                if meta.has(key):
                    dt = meta.get(key)
                    if isinstance(dt, datetime) and dt.year > 1971:
                        return dt  # usually UTC
    except Exception:
        pass
    return None


# ---- Apple QuickTime capture date (iPhone .mov / .mp4)
def _atoms(f, start, end):
    """Yield (type, data_start, data_end) for atoms between start and end."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return
        size = int.from_bytes(head[:4], "big")
        kind = head[4:8]
        hdr = 8
        if size == 1:
            size = int.from_bytes(f.read(8), "big")
            hdr = 16
        elif size == 0:
            size = end - pos
        if size < hdr:
            return
        yield kind, pos + hdr, min(pos + size, end)
        pos += size


def read_apple_date(path):
    """com.apple.quicktime.creationdate: real capture time incl. time zone."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            file_end = f.tell()
            moov = next(((s, e) for k, s, e in _atoms(f, 0, file_end) if k == b"moov"), None)
            if not moov:
                return None
            metas = []
            for k, s, e in _atoms(f, *moov):
                if k == b"meta":
                    metas.append((s, e))
                elif k == b"udta":
                    metas += [(s2, e2) for k2, s2, e2 in _atoms(f, s, e) if k2 == b"meta"]
            for s, e in metas:
                f.seek(s)
                if f.read(4) == b"\0\0\0\0":  # MP4-style meta has version/flags
                    s += 4
                children = {k: (cs, ce) for k, cs, ce in _atoms(f, s, e)}
                if b"keys" not in children or b"ilst" not in children:
                    continue
                ks, ke = children[b"keys"]
                f.seek(ks + 4)
                count = int.from_bytes(f.read(4), "big")
                keys = {}
                for i in range(1, count + 1):
                    size = int.from_bytes(f.read(4), "big")
                    f.read(4)  # namespace
                    keys[i] = f.read(max(size - 8, 0)).decode("utf-8", "ignore")
                for k, cs, ce in _atoms(f, *children[b"ilst"]):
                    if keys.get(int.from_bytes(k, "big")) != "com.apple.quicktime.creationdate":
                        continue
                    for k2, ds, de in _atoms(f, cs, ce):
                        if k2 == b"data":
                            f.seek(ds + 8)  # skip type + locale
                            text = f.read(de - ds - 8).decode("utf-8", "ignore").strip()
                            for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z"):
                                try:
                                    return datetime.strptime(text, fmt)
                                except ValueError:
                                    pass
    except Exception:
        pass
    return None


def read_windows_date(path):
    """Explorer's 'Media created' (videos) or 'Date taken' (photos), as aware UTC."""
    if not HAS_WINPROPS:
        return None
    ext = os.path.splitext(path)[1].lower()
    key = pscon.PKEY_Media_DateEncoded if ext in VIDEO_EXTS else pscon.PKEY_Photo_DateTaken
    try:
        store = propsys.SHGetPropertyStoreFromParsingName(os.path.abspath(path))
        value = store.GetValue(key).GetValue()
        if not value:
            return None
        dt = datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)
        return dt.replace(tzinfo=timezone.utc)  # Windows stores these in UTC
    except Exception:
        return None


def read_media_created(path):
    dt = read_windows_date(path)
    if dt:
        return dt
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        return read_exif(path)
    if ext in VIDEO_EXTS:
        return read_video(path)
    return None


FILENAME_PATTERNS = [
    # 20251026_143012 / 2025-10-26 14.30.12 / 20251026-143012
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


def read_filename(path):
    return read_filename_full(path)[0]


def read_modified(path):
    return datetime.fromtimestamp(os.path.getmtime(path))


def read_created(path):
    # On Windows getctime is the creation time
    return datetime.fromtimestamp(os.path.getctime(path))


READERS = {
    "apple": read_apple_date,
    "media": read_media_created,
    "exif": read_exif,
    "video": read_video,
    "filename": read_filename,
    "modified": read_modified,
    "created": read_created,
}


def read_all_dates(path):
    ext = os.path.splitext(path)[1].lower()
    dates = {}
    for key, fn in READERS.items():
        if key == "exif" and ext not in IMAGE_EXTS:
            continue
        if key in ("video", "apple") and ext not in VIDEO_EXTS:
            continue
        dates[key] = fn(path)
    dates["filename_has_time"] = read_filename_full(path)[1]
    return dates


def utc_to_local(dt):
    return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


# ---------------------------------------------------------------- app
THEMES = {
    "dark": dict(bg="#16181d", card="#1f2229", field="#2a2e37", border="#353a45",
                 fg="#e6e8ec", muted="#8b93a1", accent="#3b82f6", accent_hover="#2563eb",
                 accent_off="#2b3f63", head="#2a2e37", select="#334155",
                 btn="#2a2e37", btn_hover="#353a45", ok="#e6e8ec", skip="#f87171"),
    "light": dict(bg="#f5f6f8", card="#ffffff", field="#ffffff", border="#d1d5db",
                  fg="#111827", muted="#6b7280", accent="#2563eb", accent_hover="#1d4ed8",
                  accent_off="#93c5fd", head="#e5e7eb", select="#dbeafe",
                  btn="#e5e7eb", btn_hover="#d1d5db", ok="#111827", skip="#b91c1c"),
}


class RenamerApp(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("Photo & Video Date Renamer")
        self.geometry("1150x680")
        self.minsize(950, 560)
        self.theme = "dark"

        self.files = []          # list of paths
        self.dates = {}          # path -> {source: datetime|None}
        self.plan = []           # list of (old_path, new_path|None, source, status)
        self.undo_stack = []     # list of [(new_path, old_path), ...]

        self.order = list(DEFAULT_ORDER)
        self.enabled = {k: tk.BooleanVar(value=v) for k, v in DEFAULT_ON.items()}
        self.img_prefix = tk.StringVar(value="IMG_")
        self.vid_prefix = tk.StringVar(value="VID_")
        self.date_fmt = tk.StringVar(value=DATE_FORMATS[0])
        self.video_local = tk.BooleanVar(value=True)
        self.earliest_after_2000 = tk.BooleanVar(value=False)
        self.recursive = tk.BooleanVar(value=False)
        self.status = tk.StringVar(value="Add files or a folder to get started.")

        self._style()
        self._build()
        self._apply_tree_colors()
        for var in (self.img_prefix, self.vid_prefix, self.date_fmt, self.video_local,
                self.earliest_after_2000):
            var.trace_add("write", lambda *_: self.refresh_preview())
        for var in self.enabled.values():
            var.trace_add("write", lambda *_: self.refresh_preview())
        self.refresh_preview()

    # ---------- look
    def _style(self):
        t = THEMES[self.theme]
        self.configure(bg=t["bg"])
        s = ttk.Style(self)
        s.theme_use("clam")
        base = ("Segoe UI", 10)
        s.configure(".", font=base, background=t["bg"], foreground=t["fg"],
                    bordercolor=t["border"], darkcolor=t["bg"], lightcolor=t["bg"],
                    troughcolor=t["bg"], focuscolor=t["accent"], selectbackground=t["select"],
                    selectforeground=t["fg"], fieldbackground=t["field"], insertcolor=t["fg"])
        s.configure("TFrame", background=t["bg"])
        s.configure("TLabel", background=t["bg"], foreground=t["fg"])
        s.configure("Card.TFrame", background=t["card"])
        s.configure("Card.TLabel", background=t["card"], foreground=t["fg"])
        for name, bg in (("TCheckbutton", t["bg"]), ("Card.TCheckbutton", t["card"])):
            s.configure(name, background=bg, foreground=t["fg"], indicatorbackground=t["field"],
                        indicatorforeground=t["fg"], upperbordercolor=t["border"], lowerbordercolor=t["border"])
            s.map(name, background=[("active", bg)],
                  indicatorbackground=[("selected", t["accent"]), ("pressed", t["accent"])],
                  indicatorforeground=[("selected", "white")])
        s.configure("Title.TLabel", font=("Segoe UI Semibold", 16), background=t["bg"], foreground=t["fg"])
        s.configure("Sub.TLabel", foreground=t["muted"], background=t["bg"])
        s.configure("H.TLabel", font=("Segoe UI Semibold", 11), background=t["card"], foreground=t["fg"])
        s.configure("Hint.TLabel", foreground=t["muted"], background=t["card"], font=("Segoe UI", 9))
        s.configure("TButton", padding=(10, 5), background=t["btn"], foreground=t["fg"],
                    bordercolor=t["border"], lightcolor=t["btn"], darkcolor=t["btn"])
        s.map("TButton", background=[("disabled", t["card"]), ("active", t["btn_hover"])],
              foreground=[("disabled", t["muted"])])
        s.configure("Small.TButton", padding=(4, 0), font=("Segoe UI", 9))
        s.configure("Accent.TButton", background=t["accent"], foreground="white",
                    font=("Segoe UI Semibold", 10), padding=(16, 7), borderwidth=0,
                    lightcolor=t["accent"], darkcolor=t["accent"])
        s.map("Accent.TButton", background=[("disabled", t["accent_off"]), ("active", t["accent_hover"])],
              foreground=[("disabled", t["muted"])])
        for w in ("TEntry", "TCombobox"):
            s.configure(w, fieldbackground=t["field"], foreground=t["fg"], background=t["btn"],
                        bordercolor=t["border"], lightcolor=t["field"], darkcolor=t["field"],
                        arrowcolor=t["fg"], insertcolor=t["fg"])
            s.map(w, fieldbackground=[("readonly", t["field"])], bordercolor=[("focus", t["accent"])])
        # dropdown list of the combobox
        self.option_add("*TCombobox*Listbox.background", t["field"])
        self.option_add("*TCombobox*Listbox.foreground", t["fg"])
        self.option_add("*TCombobox*Listbox.selectBackground", t["accent"])
        self.option_add("*TCombobox*Listbox.selectForeground", "white")
        s.configure("TSeparator", background=t["border"])
        s.configure("Vertical.TScrollbar", background=t["btn"], troughcolor=t["card"],
                    bordercolor=t["card"], arrowcolor=t["muted"], lightcolor=t["btn"], darkcolor=t["btn"])
        s.map("Vertical.TScrollbar", background=[("active", t["btn_hover"])])
        s.configure("Treeview", rowheight=26, background=t["card"], fieldbackground=t["card"],
                    foreground=t["fg"], borderwidth=0, lightcolor=t["card"], darkcolor=t["card"])
        s.configure("Treeview.Heading", font=("Segoe UI Semibold", 10), background=t["head"],
                    foreground=t["fg"], relief="flat", bordercolor=t["border"],
                    lightcolor=t["head"], darkcolor=t["head"])
        s.map("Treeview.Heading", background=[("active", t["btn_hover"])])
        s.configure("Status.TLabel", background=t["head"], foreground=t["muted"], padding=(10, 4))

    def _apply_tree_colors(self):
        t = THEMES[self.theme]
        self.tree.tag_configure("ok", foreground=t["ok"])
        self.tree.tag_configure("same", foreground=t["muted"])
        self.tree.tag_configure("skip", foreground=t["skip"])

    def toggle_theme(self):
        self.theme = "light" if self.theme == "dark" else "dark"
        self._style()
        self._apply_tree_colors()
        self.theme_btn.configure(text="Light mode" if self.theme == "dark" else "Dark mode")

    # ---------- layout
    def _build(self):
        header = ttk.Frame(self, padding=(18, 14, 18, 6))
        header.pack(fill="x")
        ttk.Label(header, text="Photo & Video Date Renamer", style="Title.TLabel").pack(anchor="w")
        ttk.Label(header, text="Pick which date to use, check the preview, then rename.",
                  style="Sub.TLabel").pack(anchor="w")

        toolbar = ttk.Frame(self, padding=(18, 6))
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="Add files", command=self.add_files).pack(side="left")
        ttk.Button(toolbar, text="Add folder", command=self.add_folder).pack(side="left", padx=6)
        ttk.Checkbutton(toolbar, text="Include subfolders", variable=self.recursive).pack(side="left", padx=6)
        ttk.Button(toolbar, text="Clear list", command=self.clear).pack(side="left", padx=6)
        self.theme_btn = ttk.Button(toolbar, text="Light mode", command=self.toggle_theme)
        self.theme_btn.pack(side="right")

        body = ttk.Frame(self, padding=(18, 6, 18, 12))
        body.pack(fill="both", expand=True)

        # settings card
        side = ttk.Frame(body, style="Card.TFrame", padding=14)
        side.pack(side="left", fill="y")

        ttk.Label(side, text="1. Date source", style="H.TLabel").pack(anchor="w")
        self.mode_hint = ttk.Label(side, style="Hint.TLabel")
        self.mode_hint.pack(anchor="w", pady=(0, 6))
        self.src_frame = ttk.Frame(side, style="Card.TFrame")
        self.src_frame.pack(fill="x")
        self._draw_sources()
        ttk.Checkbutton(side, text="Convert video time from UTC to local",
                        variable=self.video_local, style="Card.TCheckbutton").pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(side, text="Use earliest checked date (2000 to today)",
                variable=self.earliest_after_2000, style="Card.TCheckbutton").pack(anchor="w")
        if not HAS_WINPROPS:
            ttk.Label(side, text="pywin32 not installed: Windows dates use fallback readers",
                      style="Hint.TLabel", foreground="#f87171").pack(anchor="w")
        if not HAS_HACHOIR:
            ttk.Label(side, text="hachoir not installed: video metadata disabled",
                      style="Hint.TLabel", foreground="#f87171").pack(anchor="w")

        ttk.Separator(side).pack(fill="x", pady=12)
        ttk.Label(side, text="2. Name format", style="H.TLabel").pack(anchor="w", pady=(0, 6))
        grid = ttk.Frame(side, style="Card.TFrame")
        grid.pack(fill="x")
        ttk.Label(grid, text="Photo prefix", style="Card.TLabel").grid(row=0, column=0, sticky="w", pady=3)
        ttk.Entry(grid, textvariable=self.img_prefix, width=14).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(grid, text="Video prefix", style="Card.TLabel").grid(row=1, column=0, sticky="w", pady=3)
        ttk.Entry(grid, textvariable=self.vid_prefix, width=14).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Label(grid, text="Date format", style="Card.TLabel").grid(row=2, column=0, sticky="w", pady=3)
        ttk.Combobox(grid, textvariable=self.date_fmt, values=DATE_FORMATS, width=20).grid(
            row=2, column=1, sticky="w", padx=8)
        self.example = ttk.Label(side, style="Hint.TLabel")
        self.example.pack(anchor="w", pady=(6, 0))
        ttk.Label(side, text="%Y year  %m month  %d day  %H hour  %M min  %S sec",
                  style="Hint.TLabel").pack(anchor="w")

        ttk.Separator(side).pack(fill="x", pady=12)
        self.rename_btn = ttk.Button(side, text="Rename files", style="Accent.TButton",
                                     command=self.do_rename, state="disabled")
        self.rename_btn.pack(fill="x")
        self.undo_btn = ttk.Button(side, text="Undo last rename", command=self.undo, state="disabled")
        self.undo_btn.pack(fill="x", pady=(6, 0))

        # preview card
        main = ttk.Frame(body, style="Card.TFrame", padding=10)
        main.pack(side="left", fill="both", expand=True, padx=(12, 0))
        cols = ("old", "new", "source", "status")
        self.tree = ttk.Treeview(main, columns=cols, show="headings", selectmode="none")
        for c, text, w in (("old", "Current name", 260), ("new", "New name", 260),
                           ("source", "Date from", 170), ("status", "Status", 120)):
            self.tree.heading(c, text=text)
            self.tree.column(c, width=w, anchor="w")
        sb = ttk.Scrollbar(main, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        ttk.Label(self, textvariable=self.status, style="Status.TLabel").pack(fill="x", side="bottom")
        self._update_example()

    def _draw_sources(self):
        for w in self.src_frame.winfo_children():
            w.destroy()
        for i, key in enumerate(self.order):
            ttk.Checkbutton(self.src_frame, text=SOURCES[key], variable=self.enabled[key],
                            style="Card.TCheckbutton").grid(row=i, column=0, sticky="w", pady=1)
            ttk.Button(self.src_frame, text="▲", width=2, style="Small.TButton",
                       command=lambda k=key: self.move(k, -1)).grid(row=i, column=1, padx=(8, 2))
            ttk.Button(self.src_frame, text="▼", width=2, style="Small.TButton",
                       command=lambda k=key: self.move(k, 1)).grid(row=i, column=2)

    def move(self, key, step):
        i = self.order.index(key)
        j = i + step
        if 0 <= j < len(self.order):
            self.order[i], self.order[j] = self.order[j], self.order[i]
            self._draw_sources()
            self.refresh_preview()

    def _update_example(self):
        try:
            sample = datetime(2025, 10, 26, 14, 30, 12).strftime(self.date_fmt.get())
            self.example.configure(text=f"Example: {self.img_prefix.get()}{sample}.jpg")
        except Exception:
            self.example.configure(text="Example: invalid format")

    # ---------- files
    def add_files(self):
        pattern = " ".join(f"*{e}" for e in sorted(ALL_EXTS))
        paths = filedialog.askopenfilenames(title="Select photos and videos",
                                            filetypes=[("Photos & videos", pattern), ("All files", "*.*")])
        self._load([p for p in paths if os.path.splitext(p)[1].lower() in ALL_EXTS])

    def add_folder(self):
        folder = filedialog.askdirectory(title="Select folder")
        if not folder:
            return
        paths = []
        if self.recursive.get():
            for root, _, names in os.walk(folder):
                paths += [os.path.join(root, n) for n in names]
        else:
            paths = [os.path.join(folder, n) for n in os.listdir(folder)]
        self._load([p for p in paths if os.path.isfile(p) and os.path.splitext(p)[1].lower() in ALL_EXTS])

    def clear(self):
        self.files, self.dates = [], {}
        self.refresh_preview()
        self.status.set("List cleared.")

    def _load(self, paths):
        new = [os.path.normpath(p) for p in paths if os.path.normpath(p) not in self.dates]
        if not new:
            self.status.set("No new supported files found.")
            return
        self.rename_btn.configure(state="disabled")

        def work():
            if HAS_WINPROPS:
                pythoncom.CoInitialize()  # COM must be set up in each thread
            for i, p in enumerate(new, 1):
                self.dates[p] = read_all_dates(p)
                if i % 10 == 0 or i == len(new):
                    self.after(0, self.status.set, f"Reading dates... {i}/{len(new)}")
            self.files.extend(new)
            self.after(0, self.refresh_preview)

        threading.Thread(target=work, daemon=True).start()

    # ---------- preview
    def _get_date(self, path, key):
        """Date from one source, as naive local time, or None."""
        dt = self.dates.get(path, {}).get(key)
        if not isinstance(dt, datetime):
            return None
        if dt.tzinfo is not None:
            # time zone is known (e.g. from Windows): convert to local time
            return dt.astimezone().replace(tzinfo=None)
        is_video_date = key == "video" or (
            key == "media" and os.path.splitext(path)[1].lower() in VIDEO_EXTS)
        if is_video_date and self.video_local.get():
            dt = utc_to_local(dt)
        return dt

    def pick_date(self, path):
        active = [k for k in self.order if self.enabled[k].get()]

        if not self.earliest_after_2000.get():
            # priority mode: first checked source that has a date wins
            for key in active:
                dt = self._get_date(path, key)
                if dt:
                    return dt, key
            return None, None

        # earliest mode: among all checked sources, take the earliest date
        # between 2000-01-01 and now (drops 1970/1980 reset dates and future dates)
        now = datetime.now()
        candidates = []
        for key in active:
            dt = self._get_date(path, key)
            if dt and CUTOFF <= dt <= now:
                candidates.append((dt, key))
        if not candidates:
            return None, None
        dt, key = min(candidates, key=lambda c: c[0])

        # a date-only filename (e.g. WhatsApp) reads as 00:00:00 and would always
        # "win" that day; if another source has the same day with a real time, use it
        if key == "filename" and not self.dates[path].get("filename_has_time"):
            same_day = [c for c in candidates if c[1] != "filename" and c[0].date() == dt.date()]
            if same_day:
                dt, key = min(same_day, key=lambda c: c[0])
        return dt, key

    def refresh_preview(self):
        self._update_example()
        self.mode_hint.configure(
            text="Earliest date among the checked sources wins." if self.earliest_after_2000.get()
            else "Tried from top to bottom. First one found wins.")
        self.tree.delete(*self.tree.get_children())
        self.plan = []
        taken = set()
        fmt = self.date_fmt.get()
        counts = {"ok": 0, "same": 0, "skip": 0}

        for path in sorted(self.files, key=lambda p: os.path.basename(p).lower()):
            folder, old = os.path.split(path)
            ext = os.path.splitext(old)[1].lower()
            dt, src = self.pick_date(path)
            if not dt:
                self.plan.append((path, None, None, "skip"))
                self.tree.insert("", "end", values=(old, "", "", "No date found"), tags=("skip",))
                counts["skip"] += 1
                continue
            prefix = self.img_prefix.get() if ext in IMAGE_EXTS else self.vid_prefix.get()
            try:
                stem = prefix + dt.strftime(fmt)
            except Exception:
                stem = prefix + dt.strftime(DATE_FORMATS[0])
            stem = re.sub(r'[\\/:*?"<>|]', "-", stem)
            candidate, n = stem + ext, 1
            while True:
                full = os.path.join(folder, candidate)
                key = full.lower()
                clash = key in taken or (os.path.exists(full) and os.path.normcase(full) != os.path.normcase(path))
                if not clash:
                    break
                candidate, n = f"{stem}_{n}{ext}", n + 1
            taken.add(os.path.join(folder, candidate).lower())
            if candidate == old:
                status, tag = "Already named", "same"
            else:
                status, tag = "Ready", "ok"
            counts[tag] += 1
            self.plan.append((path, os.path.join(folder, candidate), src, tag))
            self.tree.insert("", "end", values=(old, candidate, SOURCES[src], status), tags=(tag,))

        total = len(self.files)
        if total:
            self.status.set(f"{total} files  |  {counts['ok']} to rename  |  "
                            f"{counts['same']} unchanged  |  {counts['skip']} skipped (no date)")
        self.rename_btn.configure(state="normal" if counts["ok"] else "disabled")

    # ---------- actions
    def do_rename(self):
        todo = [(o, n) for o, n, _, tag in self.plan if tag == "ok"]
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
        batch = self.undo_stack.pop()
        restored = {}
        for new, old in reversed(batch):
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
        """Update the internal list after files were renamed on disk."""
        self.files = [mapping.get(p, p) for p in self.files]
        self.dates = {mapping.get(p, p): d for p, d in self.dates.items()}
        # modified/created dates stay valid; filename date must be re-read
        for p in mapping.values():
            if p in self.dates:
                self.dates[p]["filename"], self.dates[p]["filename_has_time"] = read_filename_full(p)
        self.refresh_preview()


if __name__ == "__main__":
    RenamerApp().mainloop()
