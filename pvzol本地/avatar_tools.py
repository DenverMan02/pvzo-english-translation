"""Avatar decoding, square cropping and a modal editor.

Pillow and Tk are loaded on demand. This module never writes files: the caller
owns the confirmed result and any temporary PNG needed by the profile save.
"""
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlsplit
import math
import sys
import warnings

LIBRARY = Path(__file__).resolve().parent / "launcher_lib"
if LIBRARY.is_dir() and str(LIBRARY) not in sys.path:
    sys.path.insert(0, str(LIBRARY))

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
AVATAR_SIZE = 256


def _pillow():
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise RuntimeError("Avatar preview and cropping require the Pillow image component.") from exc
    return Image, ImageOps


def load_image(path):
    """Decode a bounded PNG/JPEG, apply EXIF orientation and detach as RGBA."""
    Image, ImageOps = _pillow()
    source = Path(path).expanduser()
    if not source.is_file() or not 0 < source.stat().st_size <= MAX_IMAGE_BYTES:
        raise ValueError("Choose a PNG or JPG image no larger than 5 MiB.")
    data = source.read_bytes()
    if not 0 < len(data) <= MAX_IMAGE_BYTES:
        raise ValueError("Avatar images must not exceed 5 MiB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as image:
                if image.format not in ("PNG", "JPEG"):
                    raise ValueError("Choose a valid PNG or JPG image.")
                width, height = image.size
                if min(width, height) <= 0 or width * height > MAX_IMAGE_PIXELS:
                    raise ValueError("Avatar images can contain up to 25 megapixels.")
                image.load()
                return ImageOps.exif_transpose(image).convert("RGBA")
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise ValueError("Avatar images can contain up to 25 megapixels.") from exc
    except (OSError, SyntaxError) as exc:
        raise ValueError("Could not read the avatar image. Choose a complete PNG or JPG file.") from exc


def resolve_avatar_path(player, root):
    """Resolve a local current avatar or builtin image, never fetch a URL.

    Local served URLs must remain within image/head after URL decoding and
    symlink resolution. An external or missing avatar falls back to its builtin
    face/default image. A local URL that escapes this directory is rejected.
    """
    root = Path(root).resolve()
    heads = (root / "webroot/image/head").resolve()
    if not heads.is_relative_to(root):
        raise ValueError("The avatar directory is outside the game directory.")
    url = str(player.get("qq_face_url") or "")
    if url:
        try:
            parsed = urlsplit(url)
            local = (parsed.scheme == "http" and parsed.hostname == "127.0.0.1"
                     and parsed.port == 8080 and not parsed.username and not parsed.password)
        except ValueError:
            local = False
        if local:
            decoded = unquote(parsed.path).replace("\\", "/")
            prefix = "/image/head/"
            if decoded.startswith(prefix):
                candidate = (heads / decoded[len(prefix):]).resolve()
                if not candidate.is_relative_to(heads):
                    raise ValueError("The avatar path is outside the avatar directory.")
                if candidate.is_file():
                    return candidate
    try:
        face = int(player.get("face_id") or 0)
    except (TypeError, ValueError):
        face = 0
    builtin = heads / ("%d.png" % face if face > 0 else "unknow.png")
    if builtin.is_file():
        return builtin
    default = heads / "unknow.png"
    return default if default.is_file() else None


class CropModel:
    """A square viewport covered by a movable image at 1–5× cover zoom.

    Offsets and drags use viewport pixels. Zoom keeps the viewport centre on
    the same source point, and clamping prevents new blank image borders.
    Transparent source pixels remain transparent in the confirmed crop.
    """
    def __init__(self, image, viewport_size=320, max_zoom=5.0):
        if type(viewport_size) is not int or viewport_size < 1:
            raise ValueError("Invalid crop viewport size.")
        if not math.isfinite(float(max_zoom)) or max_zoom < 1:
            raise ValueError("Invalid maximum zoom ratio.")
        if min(image.size) < 1:
            raise ValueError("Invalid image dimensions.")
        self.original = image.convert("RGBA").copy()
        self.viewport_size = viewport_size
        self.max_zoom = float(max_zoom)
        self.reset()

    @property
    def scale(self):
        return self.base_scale * self.zoom

    def _cover_scale(self):
        self.base_scale = max(self.viewport_size / self.image.width,
                              self.viewport_size / self.image.height)

    def _clamp(self):
        edge = self.viewport_size
        self.offset_x = min(0.0, max(edge - self.image.width * self.scale, self.offset_x))
        self.offset_y = min(0.0, max(edge - self.image.height * self.scale, self.offset_y))

    def reset(self):
        self.image = self.original.copy()
        self.quarter_turns = 0
        self.zoom = 1.0
        self._cover_scale()
        self.offset_x = (self.viewport_size - self.image.width * self.scale) / 2
        self.offset_y = (self.viewport_size - self.image.height * self.scale) / 2
        self._clamp()

    def set_zoom(self, value):
        value = float(value)
        if not math.isfinite(value):
            raise ValueError("Invalid zoom ratio.")
        middle = self.viewport_size / 2
        focus_x = (middle - self.offset_x) / self.scale
        focus_y = (middle - self.offset_y) / self.scale
        self.zoom = min(self.max_zoom, max(1.0, value))
        self.offset_x = middle - focus_x * self.scale
        self.offset_y = middle - focus_y * self.scale
        self._clamp()

    def drag(self, dx, dy):
        dx, dy = float(dx), float(dy)
        if not math.isfinite(dx) or not math.isfinite(dy):
            raise ValueError("Invalid drag position.")
        self.offset_x += dx
        self.offset_y += dy
        self._clamp()

    def rotate_clockwise(self):
        Image, _ = _pillow()
        middle = self.viewport_size / 2
        old_x = (middle - self.offset_x) / self.scale
        old_y = (middle - self.offset_y) / self.scale
        focus_x, focus_y = self.image.height - old_y, old_x
        self.image = self.image.transpose(Image.Transpose.ROTATE_270)
        self.quarter_turns = (self.quarter_turns + 1) % 4
        self._cover_scale()
        self.offset_x = middle - focus_x * self.scale
        self.offset_y = middle - focus_y * self.scale
        self._clamp()

    def crop_box(self):
        side = min(self.viewport_size / self.scale, self.image.width, self.image.height)
        left = min(self.image.width - side, max(0.0, -self.offset_x / self.scale))
        top = min(self.image.height - side, max(0.0, -self.offset_y / self.scale))
        return left, top, left + side, top + side

    def render(self, size=AVATAR_SIZE):
        if type(size) is not int or size < 1:
            raise ValueError("Invalid avatar dimensions.")
        Image, _ = _pillow()
        return self.image.resize((size, size), Image.Resampling.LANCZOS, box=self.crop_box())


def preview_image(image, size=96, checkerboard=False):
    """Fit an image in a stable square preview; do not alter the saved crop."""
    Image, ImageOps = _pillow()
    if type(size) is not int or size < 1:
        raise ValueError("Invalid preview dimensions.")
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    if checkerboard:
        from PIL import ImageDraw
        draw = ImageDraw.Draw(canvas)
        tile = 10
        for y in range(0, size, tile):
            for x in range(0, size, tile):
                color = (43, 53, 40, 255) if (x // tile + y // tile) % 2 else (62, 74, 58, 255)
                draw.rectangle((x, y, x + tile - 1, y + tile - 1), fill=color)
    fitted = ImageOps.contain(image.convert("RGBA"), (size, size), Image.Resampling.LANCZOS)
    canvas.alpha_composite(fitted, ((size - fitted.width) // 2, (size - fitted.height) // 2))
    return canvas


def make_preview_photo(image, master, size=96, checkerboard=False):
    """Create a Tk photo; caller must retain its Python reference."""
    _pillow()
    try:
        from PIL import ImageTk
        return ImageTk.PhotoImage(preview_image(image, size, checkerboard), master=master)
    except ImportError as exc:
        raise RuntimeError("Avatar preview requires Pillow's Tk image component.") from exc


def crop_viewport_size(parent):
    """Choose a square once; leave room for instructions, controls and chrome."""
    try:
        return min(320, max(160, int(parent.winfo_screenheight()) - 230),
                   max(160, int(parent.winfo_screenwidth()) - 210))
    except (AttributeError, TypeError, ValueError):
        return 320


def pick_cropped_avatar(parent, path, theme, center_window):
    """Return a confirmed 256×256 RGBA crop, or None on cancel; never write."""
    import tkinter as tk
    from tkinter import ttk
    model = CropModel(load_image(path), viewport_size=crop_viewport_size(parent))
    palette = theme or {}
    def color(name, fallback):
        return palette.get(name, palette.get(name.lower(), fallback))
    bg, panel = color("BG", "#101b12"), color("PANEL", "#19281b")
    fg, muted = color("FG", "#e3eadb"), color("MUTED", "#9ba990")
    accent = color("ACCENT", "#99ca63")
    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title("Crop Avatar")
    dialog.transient(parent)
    dialog.configure(bg=bg)
    dialog.resizable(False, False)
    previous_grab = parent.grab_current()
    result = None
    try:
        body = tk.Frame(dialog, bg=bg)
        body.pack(padx=18, pady=16)
        tk.Label(body, text="Drag the image to adjust its position", bg=bg, fg=fg,
                 font=("Segoe UI", 12, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 3))
        tk.Label(body, text="The area inside the frame will be saved as your avatar", bg=bg, fg=muted,
                 font=("Segoe UI", 9)).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 12))
        canvas = tk.Canvas(body, width=model.viewport_size, height=model.viewport_size,
                           bg=panel, highlightbackground=accent, highlightthickness=1, cursor="fleur")
        canvas.grid(row=2, column=0, sticky="nw")
        canvas_image = canvas.create_image(0, 0, anchor="nw")
        side = tk.Frame(body, bg=bg)
        side.grid(row=2, column=1, sticky="nw", padx=(16, 0))
        tk.Label(side, text="Avatar Preview", bg=bg, fg=muted, font=("Segoe UI", 9)).pack(pady=(0, 7))
        preview = tk.Label(side, bg=panel)
        preview.pack()
        zoom_var = tk.DoubleVar(master=dialog, value=1.0)
        zoom_text = tk.StringVar(master=dialog, value="1.00×")
        tk.Label(side, text="Zoom", bg=bg, fg=muted, font=("Segoe UI", 9)).pack(anchor="w", pady=(18, 3))
        tk.Label(side, textvariable=zoom_text, bg=bg, fg=fg, font=("Segoe UI", 9)).pack(anchor="w")

        def redraw():
            photo = make_preview_photo(model.render(model.viewport_size), dialog,
                                       model.viewport_size, checkerboard=True)
            canvas.itemconfigure(canvas_image, image=photo)
            canvas.image = photo
            photo = make_preview_photo(model.render(96), dialog, 96, checkerboard=True)
            preview.configure(image=photo)
            preview.image = photo
            zoom_text.set("%.2f×" % model.zoom)

        def zoom(value):
            model.set_zoom(value)
            redraw()

        ttk.Scale(side, from_=1.0, to=model.max_zoom, variable=zoom_var,
                  orient="horizontal", length=115, command=zoom).pack(fill="x", pady=(4, 14))
        def rotate():
            model.rotate_clockwise()
            redraw()
        def reset():
            model.reset()
            zoom_var.set(model.zoom)
            redraw()
        for text, command in (("Rotate 90°", rotate), ("Reset", reset)):
            tk.Button(side, text=text, command=command, bg=panel, fg=fg, relief="flat",
                      cursor="hand2", font=("Segoe UI", 9), padx=8, pady=5).pack(fill="x", pady=4)

        drag_point = [None]
        def down(event):
            drag_point[0] = (event.x, event.y)
        def move(event):
            if drag_point[0] is not None:
                x, y = drag_point[0]
                model.drag(event.x - x, event.y - y)
                drag_point[0] = (event.x, event.y)
                redraw()
        def up(_event):
            drag_point[0] = None
        canvas.bind("<ButtonPress-1>", down)
        canvas.bind("<B1-Motion>", move)
        canvas.bind("<ButtonRelease-1>", up)
        def cancel(_event=None):
            dialog.destroy()
        def confirm(_event=None):
            nonlocal result
            result = model.render(AVATAR_SIZE)
            dialog.destroy()
        footer = tk.Frame(body, bg=bg)
        footer.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(16, 0))
        tk.Button(footer, text="Use Cropped Avatar", command=confirm, bg=accent, fg="#12220a",
                  relief="flat", cursor="hand2", font=("Segoe UI", 10, "bold"),
                  padx=14, pady=7).pack(side="right")
        tk.Button(footer, text="Cancel", command=cancel, bg=panel, fg=fg, relief="flat",
                  cursor="hand2", font=("Segoe UI", 10), padx=14, pady=7).pack(side="right", padx=(0, 9))
        dialog.protocol("WM_DELETE_WINDOW", cancel)
        dialog.bind("<Escape>", cancel)
        dialog.bind("<Return>", confirm)
        redraw()
        center_window(dialog, parent=parent)
        dialog.deiconify()
        dialog.grab_set()
        dialog.wait_window()
        return result
    finally:
        if dialog.winfo_exists():
            dialog.destroy()
        if previous_grab is not None:
            try:
                if previous_grab.winfo_exists():
                    previous_grab.grab_set()
            except tk.TclError:
                pass
