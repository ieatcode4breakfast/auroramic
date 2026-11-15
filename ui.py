"""Floating Wispr Flow-style microphone pill UI for AuroraMic using Win32 per-pixel alpha.

The right-click "Quit AuroraMic" menu is a custom dark-glass layered popup (not a native
Windows menu) that dismisses on any outside click, Escape, or foreground-window change.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import threading
import time
import tkinter as tk
from typing import Callable

import numpy as np
from PIL import Image, ImageDraw, ImageFont

WINDOW_WIDTH = 220
WINDOW_HEIGHT = 164
TOP_CENTER_X = WINDOW_WIDTH * 0.5
TOP_CENTER_Y = 50.0
TOP_BUTTON_SIZE = 74.0

BAR_PILL_WIDTH = 166.0
BAR_PILL_HEIGHT = 44.0
BAR_CENTER_X = TOP_CENTER_X
BAR_CENTER_Y = TOP_CENTER_Y + 37.0 + 10.0 + 22.0  # 119.0 (10px gap below 74px stop button)

SEND_TAB_LEFT = 118.0
SEND_TAB_RIGHT = 188.0
SEND_TAB_CENTER_X = 0.5 * (SEND_TAB_LEFT + SEND_TAB_RIGHT)
SEND_TAB_WIDTH = SEND_TAB_RIGHT - SEND_TAB_LEFT
SEND_TAB_HEIGHT = 50.0
SEND_TAB_RADIUS = 15.0
SEND_ICON_CENTER_X = 166.5
MAX_RECORD_SECONDS = 60.0

FRAME_INTERVAL_MS = 16
DRAG_THRESHOLD_PX = 5.0
SUCCESS_DURATION_MS = 450
ERROR_DURATION_MS = 1200

# Win32 constants for layered per-pixel alpha rendering.
GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
DIB_RGB_COLORS = 0
BI_RGB = 0
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
LONG_PTR = ctypes.c_ssize_t

# Colors (RGB 0..255).
BG_PILL_RGB = (20, 20, 24)
BG_PILL_HOVER_RGB = (28, 28, 35)
BORDER_IDLE_RGB = (45, 45, 53)
BORDER_HOVER_RGB = (75, 75, 90)
ACCENT_RECORDING_RGB = (255, 71, 87)
ACCENT_TRANSCRIBING_A_RGB = (83, 82, 237)
ACCENT_TRANSCRIBING_B_RGB = (112, 161, 255)
ACCENT_SUCCESS_RGB = (46, 213, 115)
ACCENT_ERROR_AMBER_RGB = (255, 165, 2)
ACCENT_ERROR_RED_RGB = (255, 71, 87)
TIMER_TEXT_RGB = (230, 232, 240)
TIMER_URGENT_RGB = (255, 107, 120)
CANCEL_ICON_RGB = (204, 208, 220)
INPUT_GLOW_A_RGB = (0, 229, 255)
INPUT_GLOW_B_RGB = (56, 249, 215)
SEND_ICON_RGB = (0, 132, 255)
SEND_ICON_HOVER_RGB = (56, 168, 255)
SEND_ICON_FLASH_RGB = (155, 220, 255)
WHITE_RGB = (255, 255, 255)

# Custom dark-glass "Quit AuroraMic" right-click popup.
QUIT_LABEL = "Quit AuroraMic"
QUIT_WIN_W = 168
QUIT_WIN_H = 58
QUIT_PILL_W = 146.0
QUIT_PILL_H = 36.0
QUIT_PILL_RADIUS = 13.0
QUIT_OPEN_SECONDS = 0.14
QUIT_FILL_RGB = (17, 19, 27)  # #11131B
QUIT_FILL_HOVER_RGB = (35, 24, 32)  # #231820
QUIT_BORDER_RGB = (50, 56, 76)  # #32384C
QUIT_BORDER_HOVER_RGB = (255, 85, 94)  # #FF555E
QUIT_GLYPH_RGB = (205, 210, 224)
QUIT_GLYPH_HOVER_RGB = (255, 214, 218)
QUIT_TEXT_RGB = (226, 229, 238)
QUIT_TEXT_HOVER_RGB = (255, 240, 241)

VK_LBUTTON = 0x01
VK_RBUTTON = 0x02
VK_MBUTTON = 0x04
VK_ESCAPE = 0x1B
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

VALID_STATES = frozenset({"idle", "recording", "transcribing", "success", "error"})

# 9-bar bell envelope gives speech waveforms an organic center focus.
BAR_ENVELOPE = (0.55, 0.72, 0.88, 0.98, 1.0, 0.98, 0.88, 0.72, 0.55)

_TIMER_SUPERSAMPLE = 4
_TIMER_MASK_W = 36
_TIMER_MASK_H = 22


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", wintypes.DWORD * 3),
    ]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", ctypes.c_ubyte),
        ("BlendFlags", ctypes.c_ubyte),
        ("SourceConstantAlpha", wintypes.BYTE if hasattr(wintypes, "BYTE") else ctypes.c_ubyte),
        ("AlphaFormat", ctypes.c_ubyte),
    ]


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _lerp_rgb(
    c1: tuple[int, int, int], c2: tuple[int, int, int], t: float
) -> tuple[int, int, int]:
    t = _clamp(t, 0.0, 1.0)
    return (
        int(round(c1[0] + (c2[0] - c1[0]) * t)),
        int(round(c1[1] + (c2[1] - c1[1]) * t)),
        int(round(c1[2] + (c2[2] - c1[2]) * t)),
    )


def _rgb_norm(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    return (rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)


def _load_timer_font(size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load clean tabular sans-serif font for the countdown timer."""
    for name in ("segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size_px)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size_px)
    except TypeError:
        return ImageFont.load_default()


def _sdf_rounded_box(
    px: np.ndarray,
    py: np.ndarray,
    cx: float,
    cy: float,
    half_w: float,
    half_h: float,
    radius: float,
) -> np.ndarray:
    """Analytical signed distance to a centered rounded rectangle."""
    r = min(radius, half_w, half_h)
    qx = np.abs(px - cx) - (half_w - r)
    qy = np.abs(py - cy) - (half_h - r)
    return (
        np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0))
        + np.minimum(np.maximum(qx, qy), 0.0)
        - r
    )


def _sdf_circle(
    px: np.ndarray, py: np.ndarray, cx: float, cy: float, radius: float
) -> np.ndarray:
    """Analytical signed distance to a circle."""
    return np.hypot(px - cx, py - cy) - radius


def _sdf_segment(
    px: np.ndarray,
    py: np.ndarray,
    ax: float,
    ay: float,
    bx: float,
    by: float,
    half_width: float,
) -> np.ndarray:
    """Analytical signed distance to a line segment with circular end caps."""
    pax = px - ax
    pay = py - ay
    bax = bx - ax
    bay = by - ay
    denom = bax * bax + bay * bay
    if denom <= 1e-8:
        return np.hypot(pax, pay) - half_width
    h = np.clip((pax * bax + pay * bay) / denom, 0.0, 1.0)
    return np.hypot(pax - bax * h, pay - bay * h) - half_width


def _sdf_triangle(
    px: np.ndarray,
    py: np.ndarray,
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> np.ndarray:
    """Exact analytical Euclidean signed distance to a 2D triangle."""
    e0x, e0y = x1 - x0, y1 - y0
    e1x, e1y = x2 - x1, y2 - y1
    e2x, e2y = x0 - x2, y0 - y2

    v0x, v0y = px - x0, py - y0
    v1x, v1y = px - x1, py - y1
    v2x, v2y = px - x2, py - y2

    t0 = np.clip((v0x * e0x + v0y * e0y) / max(1e-8, e0x * e0x + e0y * e0y), 0.0, 1.0)
    pq0x, pq0y = v0x - e0x * t0, v0y - e0y * t0

    t1 = np.clip((v1x * e1x + v1y * e1y) / max(1e-8, e1x * e1x + e1y * e1y), 0.0, 1.0)
    pq1x, pq1y = v1x - e1x * t1, v1y - e1y * t1

    t2 = np.clip((v2x * e2x + v2y * e2y) / max(1e-8, e2x * e2x + e2y * e2y), 0.0, 1.0)
    pq2x, pq2y = v2x - e2x * t2, v2y - e2y * t2

    s = 1.0 if (e0x * e2y - e0y * e2x) >= 0.0 else -1.0
    d2 = np.minimum(
        pq0x * pq0x + pq0y * pq0y,
        np.minimum(pq1x * pq1x + pq1y * pq1y, pq2x * pq2x + pq2y * pq2y),
    )
    c0 = s * (v0x * e0y - v0y * e0x)
    c1 = s * (v1x * e1y - v1y * e1x)
    c2 = s * (v2x * e2y - v2y * e2x)
    inside = np.minimum(c0, np.minimum(c1, c2))
    return np.where(inside > 0.0, -np.sqrt(d2), np.sqrt(d2))


def _sdf_bottom_semicircle_arc(
    px: np.ndarray,
    py: np.ndarray,
    cx: float,
    cy: float,
    radius: float,
    half_width: float,
) -> np.ndarray:
    """Analytical signed distance to a 0..180 deg U-cradle arc with rounded caps."""
    dx = px - cx
    dy = py - cy
    dist_lower = np.abs(np.hypot(dx, dy) - radius)
    dist_upper = np.hypot(np.abs(dx) - radius, dy)
    centerline_dist = np.where(dy >= 0.0, dist_lower, dist_upper)
    return centerline_dist - half_width


def _sdf_cov(d: np.ndarray) -> np.ndarray:
    """Convert SDF distance into sub-pixel coverage in [0, 1]."""
    return np.clip(0.5 - d, 0.0, 1.0).astype(np.float32)


def _blend_surface(
    surf_rgb: np.ndarray, cov: np.ndarray, color: tuple[float, float, float]
) -> None:
    """Blend an opaque RGB layer onto surf_rgb in place using coverage in [0, 1]."""
    inv = 1.0 - cov
    surf_rgb[:, :, 0] = surf_rgb[:, :, 0] * inv + color[0] * cov
    surf_rgb[:, :, 1] = surf_rgb[:, :, 1] * inv + color[1] * cov
    surf_rgb[:, :, 2] = surf_rgb[:, :, 2] * inv + color[2] * cov


def _composite_premul(
    dst_rgb_pm: np.ndarray,
    dst_a: np.ndarray,
    src_rgb: tuple[float, float, float] | np.ndarray,
    src_a: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Porter-Duff source-over composite in premultiplied float32 space."""
    inv = 1.0 - src_a
    if isinstance(src_rgb, tuple):
        out_r = src_rgb[0] * src_a + dst_rgb_pm[:, :, 0] * inv
        out_g = src_rgb[1] * src_a + dst_rgb_pm[:, :, 1] * inv
        out_b = src_rgb[2] * src_a + dst_rgb_pm[:, :, 2] * inv
    else:
        out_r = src_rgb[:, :, 0] * src_a + dst_rgb_pm[:, :, 0] * inv
        out_g = src_rgb[:, :, 1] * src_a + dst_rgb_pm[:, :, 1] * inv
        out_b = src_rgb[:, :, 2] * src_a + dst_rgb_pm[:, :, 2] * inv
    out_rgb_pm = np.stack((out_r, out_g, out_b), axis=-1)
    out_a = src_a + dst_a * inv
    return out_rgb_pm, out_a


def _load_label_font(size_px: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Load a clean semibold UI font for popup labels."""
    for name in ("seguisb.ttf", "segoeui.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(name, size_px)
        except Exception:
            continue
    try:
        return ImageFont.load_default(size=size_px)
    except TypeError:
        return ImageFont.load_default()


class _QuitPopup:
    """Non-activating, per-pixel-alpha dark-glass 'Quit AuroraMic' popup window."""

    def __init__(self, owner: FloatingMicUI) -> None:
        self._owner = owner
        self._user32 = owner._user32
        self._gdi32 = owner._gdi32

        self.pos_x = 0
        self.pos_y = 0
        self.visible = False
        self.open_t = 0.0
        self.hover_t = 0.0
        self._opened_at = 0.0
        self.last_ulw_ok = False
        self.last_ulw_error = 0
        self.last_rgba_bgra: np.ndarray | None = None

        self._px = np.arange(QUIT_WIN_W, dtype=np.float32)[None, :] + 0.5
        self._py = np.arange(QUIT_WIN_H, dtype=np.float32)[:, None] + 0.5
        self._bgra = np.empty((QUIT_WIN_H, QUIT_WIN_W, 4), dtype=np.uint8)
        self._hdc_screen: int | None = None
        self._hdc_mem: int | None = None
        self._hbmp: int | None = None
        self._old_bmp: int | None = None
        self._dib_bits = ctypes.c_void_p()
        self._ulw_size = wintypes.SIZE(QUIT_WIN_W, QUIT_WIN_H)
        self._ulw_pt_zero = wintypes.POINT(0, 0)
        self._ulw_blend = _BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)

        self._text_mask = np.zeros((1, 1), dtype=np.float32)
        self._text_w = 0.0
        self._build_text_mask()

        self.top = tk.Toplevel(owner.root)
        self.top.withdraw()
        self.top.overrideredirect(True)
        self.top.attributes("-topmost", True)
        self.top.configure(cursor="hand2")
        self.canvas = tk.Canvas(
            self.top,
            width=QUIT_WIN_W,
            height=QUIT_WIN_H,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonRelease-1>", owner._on_quit_release)

        self._init_surface()

        # Map once far off-screen so Tk creates its wrapper HWND, then style + hide it.
        self.top.geometry(f"{QUIT_WIN_W}x{QUIT_WIN_H}+-10000+-10000")
        self.top.deiconify()
        self.top.update_idletasks()
        self.hwnd = 0
        self.refresh_hwnd_style()
        self.draw(0.0, 0.0)
        self.top.withdraw()

    # -- Win32 / GDI surface -------------------------------------------------

    def refresh_hwnd_style(self) -> None:
        wid = self.top.winfo_id()
        parent = self._user32.GetParent(wid)
        self.hwnd = int(parent or wid)
        want = WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
        ex_style = int(self._owner._get_window_long(self.hwnd, GWL_EXSTYLE))
        if (ex_style & want) != want:
            self._owner._set_window_long(self.hwnd, GWL_EXSTYLE, ex_style | want)

    def _init_surface(self) -> None:
        hdc_screen = self._user32.GetDC(None)
        if not hdc_screen:
            raise OSError("GetDC(None) failed for quit popup")
        self._hdc_screen = int(hdc_screen)
        hdc_mem = self._gdi32.CreateCompatibleDC(hdc_screen)
        if not hdc_mem:
            self.cleanup_gdi()
            raise OSError("CreateCompatibleDC failed for quit popup")
        self._hdc_mem = int(hdc_mem)

        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = QUIT_WIN_W
        bmi.bmiHeader.biHeight = -QUIT_WIN_H
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB
        hbmp = self._gdi32.CreateDIBSection(
            hdc_screen, ctypes.byref(bmi), DIB_RGB_COLORS, ctypes.byref(self._dib_bits), None, 0
        )
        if not hbmp or not self._dib_bits.value:
            self.cleanup_gdi()
            raise OSError("CreateDIBSection failed for quit popup")
        self._hbmp = int(hbmp)
        old_bmp = self._gdi32.SelectObject(hdc_mem, hbmp)
        self._old_bmp = int(old_bmp) if old_bmp else None

    def cleanup_gdi(self) -> None:
        if self._hdc_mem and self._old_bmp:
            try:
                self._gdi32.SelectObject(self._hdc_mem, self._old_bmp)
            except Exception:
                pass
            self._old_bmp = None
        if self._hbmp:
            try:
                self._gdi32.DeleteObject(self._hbmp)
            except Exception:
                pass
            self._hbmp = None
        if self._hdc_mem:
            try:
                self._gdi32.DeleteDC(self._hdc_mem)
            except Exception:
                pass
            self._hdc_mem = None
        if self._hdc_screen:
            try:
                self._user32.ReleaseDC(None, self._hdc_screen)
            except Exception:
                pass
            self._hdc_screen = None
        self._dib_bits = ctypes.c_void_p()

    def destroy(self) -> None:
        self.visible = False
        self.cleanup_gdi()
        try:
            self.top.destroy()
        except tk.TclError:
            pass

    # -- Geometry / lifecycle ------------------------------------------------

    def pill_rect_screen(self) -> tuple[float, float, float, float]:
        left = self.pos_x + (QUIT_WIN_W - QUIT_PILL_W) * 0.5
        top = self.pos_y + (QUIT_WIN_H - QUIT_PILL_H) * 0.5
        return left, top, left + QUIT_PILL_W, top + QUIT_PILL_H

    def show(self, x: int, y: int) -> None:
        self.pos_x = int(x)
        self.pos_y = int(y)
        self.open_t = 0.0
        self.hover_t = 0.0
        self._opened_at = time.perf_counter()
        self.draw(0.0, 0.0)
        self.top.geometry(f"{QUIT_WIN_W}x{QUIT_WIN_H}+{self.pos_x}+{self.pos_y}")
        self.top.deiconify()
        self.top.attributes("-topmost", True)
        self.top.update_idletasks()
        self.refresh_hwnd_style()
        self.visible = True
        self.draw(0.0, 0.0)

    def hide(self) -> None:
        self.visible = False
        try:
            self.top.withdraw()
        except tk.TclError:
            pass

    def tick(self, now: float, dt: float, hovered: bool) -> None:
        if not self.visible:
            return
        changed = False
        if self.open_t < 1.0:
            self.open_t = _clamp((now - self._opened_at) / QUIT_OPEN_SECONDS, 0.0, 1.0)
            changed = True
        target = 1.0 if hovered else 0.0
        if abs(self.hover_t - target) > 0.002:
            self.hover_t += (target - self.hover_t) * (1.0 - math.exp(-18.0 * dt))
            changed = True
        elif self.hover_t != target:
            self.hover_t = target
            changed = True
        if changed:
            self.draw(self.open_t, self.hover_t)

    # -- Rendering -------------------------------------------------------------

    def _build_text_mask(self) -> None:
        ss = 4
        font = _load_label_font(12 * ss)
        probe = ImageDraw.Draw(Image.new("L", (1, 1), 0))
        bbox = probe.textbbox((0, 0), QUIT_LABEL, font=font)
        ref = probe.textbbox((0, 0), "AEx", font=font)
        self._text_w = (bbox[2] - bbox[0]) / ss
        tw = int(math.ceil(self._text_w)) + 4
        th = 20
        img = Image.new("L", (tw * ss, th * ss), 0)
        ImageDraw.Draw(img).text(
            (2 * ss - bbox[0], th * ss * 0.5 - (ref[1] + ref[3]) * 0.5),
            QUIT_LABEL,
            fill=255,
            font=font,
        )
        small = img.resize((tw, th), Image.Resampling.LANCZOS)
        self._text_mask = (np.asarray(small, dtype=np.float32) / 255.0).copy()

    def render(self, open_t: float, hover_t: float) -> np.ndarray:
        """Render premultiplied BGRA (H, W, 4) uint8."""
        w, h = QUIT_WIN_W, QUIT_WIN_H
        px, py = self._px, self._py
        e = 1.0 - (1.0 - _clamp(open_t, 0.0, 1.0)) ** 3
        hover_t = _clamp(hover_t, 0.0, 1.0)
        scale = 0.94 + 0.06 * e
        cx, cy = w * 0.5, h * 0.5
        hw = QUIT_PILL_W * 0.5 * scale
        hh = QUIT_PILL_H * 0.5 * scale
        rad = QUIT_PILL_RADIUS * scale

        d = _sdf_rounded_box(px, py, cx, cy, hw, hh, rad)
        d_shadow = _sdf_rounded_box(px, py, cx, cy + 2.0, hw, hh, rad)

        rgb_pm = np.zeros((h, w, 3), dtype=np.float32)
        a = np.zeros((h, w), dtype=np.float32)

        shadow_a = (np.clip(1.0 - d_shadow / 8.0, 0.0, 1.0) ** 2 * 0.36).astype(np.float32)
        rgb_pm, a = _composite_premul(rgb_pm, a, (0.0, 0.0, 0.0), shadow_a)

        if hover_t > 0.001:
            glow_a = (np.clip(1.0 - d / 8.0, 0.0, 1.0) ** 2.4 * 0.45 * hover_t).astype(np.float32)
            rgb_pm, a = _composite_premul(
                rgb_pm, a, tuple(float(c) for c in QUIT_BORDER_HOVER_RGB), glow_a
            )

        border = tuple(float(c) for c in _lerp_rgb(QUIT_BORDER_RGB, QUIT_BORDER_HOVER_RGB, hover_t))
        fill = tuple(float(c) for c in _lerp_rgb(QUIT_FILL_RGB, QUIT_FILL_HOVER_RGB, hover_t))
        rgb_pm, a = _composite_premul(rgb_pm, a, border, _sdf_cov(d))
        rgb_pm, a = _composite_premul(rgb_pm, a, fill, _sdf_cov(d + 1.25) * 0.97)

        if hover_t > 0.001:
            inner_a = (
                _sdf_cov(d + 1.25) * np.clip(1.0 + d / 6.0, 0.0, 1.0) ** 2 * 0.18 * hover_t
            ).astype(np.float32)
            rgb_pm, a = _composite_premul(
                rgb_pm, a, tuple(float(c) for c in QUIT_BORDER_HOVER_RGB), inner_a
            )

        # Power glyph (broken ring + top stem) and label, centered as a group.
        group_w = 14.0 + 9.0 + self._text_w
        left = cx - group_w * 0.5
        icx = left + 7.0
        icy = cy + 0.7
        ring_r = 6.1
        half_w = 0.95
        theta = math.radians(38.0)
        dx = px - icx
        dy = py - icy
        ring_d = np.abs(np.hypot(dx, dy) - ring_r) - half_w
        c1x, c2x = icx - ring_r * math.sin(theta), icx + ring_r * math.sin(theta)
        cap_y = icy - ring_r * math.cos(theta)
        cap_d = (
            np.minimum(np.hypot(px - c1x, py - cap_y), np.hypot(px - c2x, py - cap_y)) - half_w
        )
        in_wedge = (dy < 0.0) & (np.abs(dx) < -dy * math.tan(theta))
        arc_d = np.where(in_wedge, cap_d, ring_d)
        stem_d = _sdf_segment(px, py, icx, icy - ring_r - 1.5, icx, icy - 0.6, half_w)
        glyph_cov = _sdf_cov(np.minimum(arc_d, stem_d)) * np.clip(e * 1.0, 0.0, 1.0)
        glyph_rgb = tuple(float(c) for c in _lerp_rgb(QUIT_GLYPH_RGB, QUIT_GLYPH_HOVER_RGB, hover_t))
        rgb_pm, a = _composite_premul(rgb_pm, a, glyph_rgb, glyph_cov.astype(np.float32))

        tmask = np.zeros((h, w), dtype=np.float32)
        th_, tw_ = self._text_mask.shape
        x0 = int(round(left + 23.0 - 2.0))
        y0 = int(round(cy - th_ * 0.5))
        tmask[y0 : y0 + th_, x0 : x0 + tw_] = self._text_mask
        text_rgb = tuple(float(c) for c in _lerp_rgb(QUIT_TEXT_RGB, QUIT_TEXT_HOVER_RGB, hover_t))
        rgb_pm, a = _composite_premul(rgb_pm, a, text_rgb, tmask)

        a = np.clip(a * e, 0.0, 1.0)
        rgb_pm = rgb_pm * e
        a8 = np.rint(a * 255.0).astype(np.uint8)
        out = self._bgra
        for ch, src in ((0, 2), (1, 1), (2, 0)):
            out[:, :, ch] = np.minimum(
                np.clip(np.rint(rgb_pm[:, :, src]), 0, 255).astype(np.uint8), a8
            )
        out[:, :, 3] = a8
        return out

    def draw(self, open_t: float, hover_t: float) -> None:
        if not self._dib_bits.value or not self._hdc_screen or not self._hdc_mem or not self.hwnd:
            return
        bgra = self.render(open_t, hover_t)
        self.last_rgba_bgra = bgra
        ctypes.memmove(self._dib_bits, bgra.ctypes.data, bgra.nbytes)
        ctypes.set_last_error(0)
        ok = self._user32.UpdateLayeredWindow(
            self.hwnd,
            self._hdc_screen,
            None,
            ctypes.byref(self._ulw_size),
            self._hdc_mem,
            ctypes.byref(self._ulw_pt_zero),
            0,
            ctypes.byref(self._ulw_blend),
            ULW_ALPHA,
        )
        self.last_ulw_ok = bool(ok)
        self.last_ulw_error = ctypes.get_last_error()


def _default_state_path() -> Path:
    """Return the default persistent UI state file path under %LOCALAPPDATA%/AuroraMic."""
    local_app_data = os.getenv("LOCALAPPDATA")
    base = Path(local_app_data) if local_app_data else (Path.home() / "AppData" / "Local")
    return base / "AuroraMic" / "state.json"


class FloatingMicUI:
    """Frameless, always-on-top, per-pixel alpha audio-reactive microphone bar."""

    def __init__(
        self,
        on_start_record: Callable[[], None],
        on_stop_record: Callable[[], None],
        get_audio_level: Callable[[], float],
        on_window_ready: Callable[[int], None] | None = None,
        on_quit: Callable[[], None] | None = None,
        on_cancel_record: Callable[[], None] | None = None,
        on_send_enter: Callable[[], None] | None = None,
        state_path: str | Path | None = None,
    ) -> None:
        self.on_start_record = on_start_record
        self.on_stop_record = on_stop_record
        self.get_audio_level = get_audio_level
        self.on_window_ready = on_window_ready
        self.on_quit = on_quit
        self.on_cancel_record = on_cancel_record
        self.on_send_enter = on_send_enter
        self.state_path: Path = Path(state_path) if state_path is not None else _default_state_path()

        self._ui_thread_id = threading.get_ident()
        self._closed = False
        self._anim_after_id: str | None = None
        self._auto_reset_after_id: str | None = None
        self._state_generation = 0

        self._state = "idle"
        self._message = ""
        self._state_entered_at = time.perf_counter()
        self._last_frame_time = self._state_entered_at

        # Interpolated visual state (1x physical pixels).
        self._pill_w = TOP_BUTTON_SIZE
        self._pill_h = TOP_BUTTON_SIZE
        self._bar_vis = 0.0
        self._last_record_elapsed = 0.0
        self._border_rgb: tuple[float, float, float] = tuple(float(c) for c in BORDER_IDLE_RGB)  # type: ignore[assignment]
        self._hovered = False
        self._hover_t = 0.0
        self._cancel_hovered = False
        self._cancel_hover_t = 0.0
        self._send_hovered = False
        self._send_hover_t = 0.0
        self._send_flash_t = 0.0
        self._input_active = False
        self._input_active_t = 0.0
        self._audio_level = 0.0
        self._bar_levels = [0.0] * 9
        self._needs_redraw = True

        # Cached 4x-supersampled timer glyph masks ("M:SS" -> (H, W) float32).
        self._timer_font = _load_timer_font(12 * _TIMER_SUPERSAMPLE)
        self._timer_y_offset = self._compute_timer_y_offset()
        self._timer_mask_cache: dict[str, np.ndarray] = {}

        # Drag tracking.
        self._press_active = False
        self._is_dragging = False
        self._drag_start_root_x = 0
        self._drag_start_root_y = 0
        self._win_start_x = 0
        self._win_start_y = 0

        # Precomputed SDF pixel center grids in 1x physical pixels (broadcast to H x W).
        self._px = np.arange(WINDOW_WIDTH, dtype=np.float32)[None, :] + 0.5
        self._py = np.arange(WINDOW_HEIGHT, dtype=np.float32)[:, None] + 0.5
        self._bgra_buf = np.empty((WINDOW_HEIGHT, WINDOW_WIDTH, 4), dtype=np.uint8)

        # Win32 / GDI resources.
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self._configure_win32_api()
        self._hdc_screen: int | None = None
        self._hdc_mem: int | None = None
        self._hbmp: int | None = None
        self._old_bmp: int | None = None
        self._dib_bits = ctypes.c_void_p()
        self._ulw_size = wintypes.SIZE(WINDOW_WIDTH, WINDOW_HEIGHT)
        self._ulw_pt_zero = wintypes.POINT(0, 0)
        self._ulw_blend = _BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        self._last_ulw_ok = False
        self._last_ulw_error = 0

        self.root = tk.Tk()
        self.root.title("AuroraMic")
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(cursor="hand2")

        pos_x, pos_y = self._initial_window_position()
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{pos_x}+{pos_y}")

        self.canvas = tk.Canvas(
            self.root,
            width=WINDOW_WIDTH,
            height=WINDOW_HEIGHT,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self._quit_open = False
        self._quit_armed: dict[int, bool] = {}
        self._quit_fg_base = 0
        self._quit_popup: _QuitPopup | None = None

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_right_click)
        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<Enter>", self._on_enter)
        self.canvas.bind("<Leave>", self._on_leave)
        self.root.bind("<Destroy>", self._on_root_destroy)

        self.root.update_idletasks()
        self.hwnd: int = self._get_top_level_hwnd()
        self._apply_layered_style()
        self._init_layered_surface()
        self._quit_popup = _QuitPopup(self)
        self._draw_current_frame(self._state_entered_at)

        if self.on_window_ready is not None:
            self.on_window_ready(self.hwnd)
        self._apply_layered_style()
        self._draw_current_frame(self._state_entered_at)

    def _compute_timer_y_offset(self) -> float:
        """Center digits visually by their actual glyph bbox height."""
        probe = Image.new("L", (1, 1), 0)
        bbox = ImageDraw.Draw(probe).textbbox((0, 0), "0:58", font=self._timer_font)
        return (bbox[1] + bbox[3]) * 0.5

    def _get_timer_mask(self, label: str) -> np.ndarray:
        """Return a cached (H, W) float32 4x-LANCZOS alpha mask for timer label."""
        cached = self._timer_mask_cache.get(label)
        if cached is not None:
            return cached

        w4 = _TIMER_MASK_W * _TIMER_SUPERSAMPLE
        h4 = _TIMER_MASK_H * _TIMER_SUPERSAMPLE
        img = Image.new("L", (w4, h4), 0)
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), label, font=self._timer_font)
        text_w = bbox[2] - bbox[0]
        x = (w4 * 0.5) - bbox[0] - (text_w * 0.5)
        y = (h4 * 0.5) - self._timer_y_offset
        draw.text((x, y), label, fill=255, font=self._timer_font)
        small = img.resize((_TIMER_MASK_W, _TIMER_MASK_H), Image.Resampling.LANCZOS)
        mask = (np.asarray(small, dtype=np.float32) / 255.0).copy()
        self._timer_mask_cache[label] = mask
        return mask

    def _configure_win32_api(self) -> None:
        """Define explicit 64-bit/32-bit ctypes prototypes for user32 and gdi32."""
        if (
            ctypes.sizeof(ctypes.c_void_p) == 8
            and hasattr(self._user32, "GetWindowLongPtrW")
            and hasattr(self._user32, "SetWindowLongPtrW")
        ):
            self._get_window_long = self._user32.GetWindowLongPtrW
            self._set_window_long = self._user32.SetWindowLongPtrW
            long_ptr_type = LONG_PTR
        else:
            self._get_window_long = self._user32.GetWindowLongW
            self._set_window_long = self._user32.SetWindowLongW
            long_ptr_type = wintypes.LONG

        self._get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
        self._get_window_long.restype = long_ptr_type
        self._set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, long_ptr_type]
        self._set_window_long.restype = long_ptr_type

        self._user32.GetParent.argtypes = [wintypes.HWND]
        self._user32.GetParent.restype = wintypes.HWND

        self._user32.GetDC.argtypes = [wintypes.HWND]
        self._user32.GetDC.restype = wintypes.HDC
        self._user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self._user32.ReleaseDC.restype = ctypes.c_int

        self._user32.UpdateLayeredWindow.argtypes = [
            wintypes.HWND,
            wintypes.HDC,
            ctypes.POINTER(wintypes.POINT),
            ctypes.POINTER(wintypes.SIZE),
            wintypes.HDC,
            ctypes.POINTER(wintypes.POINT),
            wintypes.COLORREF,
            ctypes.POINTER(_BLENDFUNCTION),
            wintypes.DWORD,
        ]
        self._user32.UpdateLayeredWindow.restype = wintypes.BOOL

        self._gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        self._gdi32.CreateCompatibleDC.restype = wintypes.HDC
        self._gdi32.DeleteDC.argtypes = [wintypes.HDC]
        self._gdi32.DeleteDC.restype = wintypes.BOOL

        self._gdi32.CreateDIBSection.argtypes = [
            wintypes.HDC,
            ctypes.POINTER(_BITMAPINFO),
            wintypes.UINT,
            ctypes.POINTER(ctypes.c_void_p),
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self._gdi32.CreateDIBSection.restype = wintypes.HBITMAP

        self._gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        self._gdi32.SelectObject.restype = wintypes.HGDIOBJ
        self._gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        self._gdi32.DeleteObject.restype = wintypes.BOOL

        self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._user32.GetAsyncKeyState.restype = ctypes.c_short
        self._user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        self._user32.GetCursorPos.restype = wintypes.BOOL
        self._user32.GetForegroundWindow.argtypes = []
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetSystemMetrics.argtypes = [ctypes.c_int]
        self._user32.GetSystemMetrics.restype = ctypes.c_int

    def _get_top_level_hwnd(self) -> int:
        wid = self.root.winfo_id()
        parent = self._user32.GetParent(wid)
        return int(parent or wid)

    def _apply_layered_style(self) -> None:
        if not self.hwnd:
            return
        ex_style = int(self._get_window_long(self.hwnd, GWL_EXSTYLE))
        if not (ex_style & WS_EX_LAYERED):
            self._set_window_long(self.hwnd, GWL_EXSTYLE, ex_style | WS_EX_LAYERED)

    def _init_layered_surface(self) -> None:
        hdc_screen = self._user32.GetDC(None)
        if not hdc_screen:
            raise OSError("GetDC(None) failed")
        self._hdc_screen = int(hdc_screen)

        hdc_mem = self._gdi32.CreateCompatibleDC(hdc_screen)
        if not hdc_mem:
            self._cleanup_gdi()
            raise OSError("CreateCompatibleDC failed")
        self._hdc_mem = int(hdc_mem)

        bmi = _BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = WINDOW_WIDTH
        bmi.bmiHeader.biHeight = -WINDOW_HEIGHT  # top-down DIB
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = BI_RGB

        hbmp = self._gdi32.CreateDIBSection(
            hdc_screen,
            ctypes.byref(bmi),
            DIB_RGB_COLORS,
            ctypes.byref(self._dib_bits),
            None,
            0,
        )
        if not hbmp or not self._dib_bits.value:
            self._cleanup_gdi()
            raise OSError("CreateDIBSection failed")
        self._hbmp = int(hbmp)

        old_bmp = self._gdi32.SelectObject(hdc_mem, hbmp)
        self._old_bmp = int(old_bmp) if old_bmp else None

    def _cleanup_gdi(self) -> None:
        if self._hdc_mem and self._old_bmp:
            try:
                self._gdi32.SelectObject(self._hdc_mem, self._old_bmp)
            except Exception:
                pass
            self._old_bmp = None
        if self._hbmp:
            try:
                self._gdi32.DeleteObject(self._hbmp)
            except Exception:
                pass
            self._hbmp = None
        if self._hdc_mem:
            try:
                self._gdi32.DeleteDC(self._hdc_mem)
            except Exception:
                pass
            self._hdc_mem = None
        if self._hdc_screen:
            try:
                self._user32.ReleaseDC(None, self._hdc_screen)
            except Exception:
                pass
            self._hdc_screen = None
        self._dib_bits = ctypes.c_void_p()

    def set_input_active(self, active: bool) -> None:
        """Thread-safely toggle glowing border pulse when an input field is active."""
        if self._closed:
            return
        active_bool = bool(active)
        if threading.get_ident() == self._ui_thread_id:
            self._apply_input_active(active_bool)
            return
        try:
            self.root.after(0, lambda a=active_bool: self._apply_input_active(a))
        except (tk.TclError, RuntimeError):
            pass

    def _apply_input_active(self, active: bool) -> None:
        if self._closed:
            return
        if active != self._input_active:
            self._input_active = active
            self._needs_redraw = True

    def set_state(self, state: str, message: str = "") -> None:
        """Thread-safely switch UI state."""
        if state not in VALID_STATES:
            raise ValueError(f"Unsupported UI state: {state!r}")
        if self._closed:
            return
        if threading.get_ident() == self._ui_thread_id:
            self._apply_state(state, message)
            return
        try:
            self.root.after(0, lambda s=state, m=message: self._apply_state(s, m))
        except (tk.TclError, RuntimeError):
            pass

    def _apply_state(self, state: str, message: str = "") -> None:
        if self._closed:
            return
        self._state_generation += 1
        gen = self._state_generation

        if self._auto_reset_after_id is not None:
            try:
                self.root.after_cancel(self._auto_reset_after_id)
            except tk.TclError:
                pass
            self._auto_reset_after_id = None

        now = time.perf_counter()
        if self._state == "recording" and state != "recording":
            self._last_record_elapsed = max(0.0, now - self._state_entered_at)
        elif state == "recording":
            self._last_record_elapsed = 0.0

        self._state = state
        self._message = message
        self._state_entered_at = now
        self._cancel_hovered = False
        self._needs_redraw = True

        if state == "success":
            self._auto_reset_after_id = self.root.after(
                SUCCESS_DURATION_MS,
                lambda g=gen: self._auto_reset_to_idle(g),
            )
        elif state == "error":
            self._auto_reset_after_id = self.root.after(
                ERROR_DURATION_MS,
                lambda g=gen: self._auto_reset_to_idle(g),
            )

    def _auto_reset_to_idle(self, generation: int) -> None:
        self._auto_reset_after_id = None
        if not self._closed and generation == self._state_generation:
            self._apply_state("idle", "")

    def run(self) -> None:
        """Start the ~60 FPS animation loop and enter the Tk mainloop."""
        if self._closed:
            return
        self._last_frame_time = time.perf_counter()
        self._schedule_next_tick()
        self.root.mainloop()

    def stop(self) -> None:
        """Safely quit and destroy the Tk window."""
        if self._closed:
            return
        if threading.get_ident() == self._ui_thread_id:
            self._save_window_position()
            self._shutdown_on_ui_thread()
            return
        try:
            self.root.after(0, self._shutdown_on_ui_thread)
        except (tk.TclError, RuntimeError):
            pass

    def _shutdown_on_ui_thread(self) -> None:
        if self._closed:
            return
        self._save_window_position()
        self._closed = True
        for attr in ("_anim_after_id", "_auto_reset_after_id"):
            after_id = getattr(self, attr, None)
            if after_id is not None:
                try:
                    self.root.after_cancel(after_id)
                except tk.TclError:
                    pass
                setattr(self, attr, None)
        self._quit_open = False
        popup = self._quit_popup
        self._quit_popup = None
        if popup is not None:
            popup.destroy()
        self._cleanup_gdi()
        try:
            self.root.quit()
        except tk.TclError:
            pass
        try:
            self.root.destroy()
        except tk.TclError:
            pass

    def _on_root_destroy(self, event: tk.Event) -> None:
        if event.widget is self.root:
            self._closed = True
            self._quit_open = False
            if self._quit_popup is not None:
                self._quit_popup.cleanup_gdi()
            self._cleanup_gdi()

    def _cancel_button_center_x(self) -> float:
        return (BAR_CENTER_X + BAR_PILL_WIDTH * 0.5) - 21.0

    def _is_cancel_hit(self, x: float, y: float) -> bool:
        if self._state != "recording":
            return False
        return math.hypot(x - self._cancel_button_center_x(), y - BAR_CENTER_Y) <= 15.0

    def _is_send_enter_hit(self, x: float, y: float) -> bool:
        """Hit-test the visible Send/Enter tab to the right of the circular mic/stop button."""
        if not (140.0 <= x <= (SEND_TAB_RIGHT + 3.0) and 23.0 <= y <= 77.0):
            return False
        radius = TOP_BUTTON_SIZE * 0.5
        return x >= (TOP_CENTER_X + radius) or math.hypot(
            x - TOP_CENTER_X, y - TOP_CENTER_Y
        ) > radius

    def _on_press(self, event: tk.Event) -> None:
        self._hide_quit_popup()
        self._press_active = True
        self._is_dragging = False
        self._drag_start_root_x = event.x_root
        self._drag_start_root_y = event.y_root
        self._win_start_x = self.root.winfo_x()
        self._win_start_y = self.root.winfo_y()

    def _on_drag(self, event: tk.Event) -> None:
        if not self._press_active:
            return
        dx = event.x_root - self._drag_start_root_x
        dy = event.y_root - self._drag_start_root_y
        if not self._is_dragging and math.hypot(dx, dy) > DRAG_THRESHOLD_PX:
            self._is_dragging = True
        if self._is_dragging:
            new_x = self._win_start_x + dx
            new_y = self._win_start_y + dy
            self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{new_x}+{new_y}")

    def _on_release(self, event: tk.Event) -> None:
        if not self._press_active:
            return
        dx = event.x_root - self._drag_start_root_x
        dy = event.y_root - self._drag_start_root_y
        was_dragging = self._is_dragging or math.hypot(dx, dy) > DRAG_THRESHOLD_PX
        self._press_active = False
        self._is_dragging = False
        if was_dragging:
            self._save_window_position()
            return

        if self._is_send_enter_hit(event.x, event.y):
            self._send_flash_t = 1.0
            self._needs_redraw = True
            if self._state == "recording":
                self._apply_state("transcribing")
            if self.on_send_enter is not None:
                self.on_send_enter()
            return

        if self._state == "idle":
            self._apply_state("recording")
            self.on_start_record()
        elif self._state == "recording":
            if self._is_cancel_hit(event.x, event.y):
                self._apply_state("idle")
                if self.on_cancel_record is not None:
                    self.on_cancel_record()
            else:
                self._apply_state("transcribing")
                self.on_stop_record()

    def _on_right_click(self, event: tk.Event) -> None:
        self._show_quit_popup(int(event.x_root), int(event.y_root))

    # -- Custom Quit popup ---------------------------------------------------

    def _async_key_state(self, vk: int) -> int:
        return int(self._user32.GetAsyncKeyState(vk))

    def _cursor_pos(self) -> tuple[int, int]:
        pt = wintypes.POINT()
        if not self._user32.GetCursorPos(ctypes.byref(pt)):
            return (-1, -1)
        return (int(pt.x), int(pt.y))

    def _foreground_hwnd(self) -> int:
        return int(self._user32.GetForegroundWindow() or 0)

    def _virtual_screen_bounds(self) -> tuple[int, int, int, int]:
        """Return (left, top, right, bottom) of the virtual desktop, never smaller than primary."""
        try:
            vx = int(self._user32.GetSystemMetrics(SM_XVIRTUALSCREEN))
            vy = int(self._user32.GetSystemMetrics(SM_YVIRTUALSCREEN))
            vw = int(self._user32.GetSystemMetrics(SM_CXVIRTUALSCREEN))
            vh = int(self._user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))
        except Exception:
            vx = vy = vw = vh = 0
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        if vw <= 0 or vh <= 0:
            return (0, 0, sw, sh)
        return (min(vx, 0), min(vy, 0), max(vx + vw, sw), max(vy + vh, sh))

    def _quit_popup_origin(self, x_root: int, y_root: int) -> tuple[int, int]:
        """Place the pill below the cursor with its left edge aligned to x_root, clamped on-screen."""
        left, top, right, bottom = self._virtual_screen_bounds()
        pill_off_x = (QUIT_WIN_W - QUIT_PILL_W) * 0.5
        pill_off_y = (QUIT_WIN_H - QUIT_PILL_H) * 0.5
        x = int(round(x_root - pill_off_x))
        y = int(round(y_root + 6 - pill_off_y))
        x = int(max(left, min(x, right - QUIT_WIN_W)))
        y = int(max(top, min(y, bottom - QUIT_WIN_H)))
        return x, y

    def _show_quit_popup(self, x_root: int, y_root: int) -> None:
        popup = self._quit_popup
        if self._closed or popup is None:
            return
        x, y = self._quit_popup_origin(x_root, y_root)
        # Drain stale click transitions; ignore any button still held (e.g. the opening right-click).
        self._quit_armed = {}
        for vk in (VK_LBUTTON, VK_RBUTTON, VK_MBUTTON):
            self._quit_armed[vk] = not (self._async_key_state(vk) & 0x8000)
        self._async_key_state(VK_ESCAPE)
        self._quit_fg_base = self._foreground_hwnd()
        popup.show(x, y)
        self._quit_open = True

    def _hide_quit_popup(self) -> None:
        if not self._quit_open:
            return
        self._quit_open = False
        popup = self._quit_popup
        if popup is not None:
            popup.hide()

    def _is_point_in_quit_pill(self, screen_x: float, screen_y: float) -> bool:
        popup = self._quit_popup
        if popup is None:
            return False
        left, top, right, bottom = popup.pill_rect_screen()
        return left <= screen_x <= right and top <= screen_y <= bottom

    def _on_quit_release(self, event: tk.Event) -> None:
        if not self._quit_open or self._quit_popup is None:
            return
        sx = getattr(event, "x_root", None)
        sy = getattr(event, "y_root", None)
        if sx is None or sy is None:
            sx = self._quit_popup.pos_x + float(getattr(event, "x", -1))
            sy = self._quit_popup.pos_y + float(getattr(event, "y", -1))
        if self._is_point_in_quit_pill(float(sx), float(sy)):
            self._handle_quit()

    def _poll_quit_dismiss(self) -> bool:
        """Dismiss the Quit popup on outside click, Escape, or foreground change."""
        if not self._quit_open:
            return False
        cur_x, cur_y = self._cursor_pos()
        inside = self._is_point_in_quit_pill(cur_x, cur_y)
        for vk in (VK_LBUTTON, VK_RBUTTON, VK_MBUTTON):
            state = self._async_key_state(vk)
            if not self._quit_armed.get(vk, True):
                if not (state & 0x8000):
                    self._quit_armed[vk] = True
                continue
            if (state & 0x8001) and not inside:
                self._hide_quit_popup()
                return True
        if self._async_key_state(VK_ESCAPE) & 0x8001:
            self._hide_quit_popup()
            return True
        fg = self._foreground_hwnd()
        if self._quit_fg_base == 0:
            if fg:
                self._quit_fg_base = fg
        elif fg and fg != self._quit_fg_base:
            popup_hwnd = self._quit_popup.hwnd if self._quit_popup is not None else 0
            if fg not in (popup_hwnd, self.hwnd):
                self._hide_quit_popup()
                return True
        return False

    def _update_quit_popup(self, now: float, dt: float) -> None:
        if not self._quit_open or self._quit_popup is None:
            return
        if self._poll_quit_dismiss():
            return
        cur_x, cur_y = self._cursor_pos()
        self._quit_popup.tick(now, dt, self._is_point_in_quit_pill(cur_x, cur_y))

    def _on_motion(self, event: tk.Event) -> None:
        cancel_hovered = self._is_cancel_hit(event.x, event.y)
        send_hovered = self._is_send_enter_hit(event.x, event.y)
        circle_hovered = not send_hovered and not cancel_hovered
        changed = False
        if cancel_hovered != self._cancel_hovered:
            self._cancel_hovered = cancel_hovered
            changed = True
        if send_hovered != self._send_hovered:
            self._send_hovered = send_hovered
            changed = True
        if circle_hovered != self._hovered:
            self._hovered = circle_hovered
            changed = True
        if changed:
            self._needs_redraw = True

    def _on_enter(self, event: tk.Event) -> None:
        x = getattr(event, "x", None)
        y = getattr(event, "y", None)
        send_hovered = (
            x is not None
            and y is not None
            and self._is_send_enter_hit(float(x), float(y))
        )
        self._send_hovered = send_hovered
        self._hovered = not send_hovered
        self._needs_redraw = True

    def _on_leave(self, _event: tk.Event) -> None:
        self._hovered = False
        self._cancel_hovered = False
        self._send_hovered = False
        self._needs_redraw = True

    def _default_window_position(self) -> tuple[int, int]:
        screen_width = self.root.winfo_screenwidth()
        screen_height = self.root.winfo_screenheight()
        return (
            (screen_width - WINDOW_WIDTH) // 2,
            max(0, screen_height - WINDOW_HEIGHT - 44),
        )

    def _clamp_window_position(self, x: int, y: int) -> tuple[int, int]:
        vx0, vy0, vx1, vy1 = self._virtual_screen_bounds()
        clamped_x = int(max(vx0, min(int(x), vx1 - WINDOW_WIDTH)))
        clamped_y = int(max(vy0, min(int(y), vy1 - WINDOW_HEIGHT)))
        return clamped_x, clamped_y

    def _load_window_position(self) -> tuple[int, int] | None:
        try:
            raw = self.state_path.read_text(encoding="utf-8")
            data = json.loads(raw)
            if not isinstance(data, dict):
                return None
            x = data.get("x")
            y = data.get("y")
            if (
                isinstance(x, bool)
                or isinstance(y, bool)
                or not isinstance(x, (int, float))
                or not isinstance(y, (int, float))
                or not (math.isfinite(x) and math.isfinite(y))
            ):
                return None
            return self._clamp_window_position(int(round(x)), int(round(y)))
        except Exception:
            return None

    def _initial_window_position(self) -> tuple[int, int]:
        loaded = self._load_window_position()
        if loaded is not None:
            return loaded
        return self._default_window_position()

    def _save_window_position(self) -> None:
        try:
            self.root.update_idletasks()
            x = int(self.root.winfo_x())
            y = int(self.root.winfo_y())
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(
                json.dumps({"x": x, "y": y}),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _handle_quit(self) -> None:
        self._hide_quit_popup()
        self._save_window_position()
        try:
            if self.on_quit is not None:
                self.on_quit()
        finally:
            self.stop()

    def _schedule_next_tick(self) -> None:
        if self._closed or self._anim_after_id is not None:
            return
        try:
            self._anim_after_id = self.root.after(FRAME_INTERVAL_MS, self._tick)
        except (tk.TclError, RuntimeError):
            self._anim_after_id = None

    def _tick(self) -> None:
        self._anim_after_id = None
        if self._closed:
            return
        now = time.perf_counter()
        dt = _clamp(now - self._last_frame_time, 0.001, 0.05)
        self._last_frame_time = now

        if self._step_animation(now, dt):
            self._draw_current_frame(now)

        self._update_quit_popup(now, dt)
        self._schedule_next_tick()

    def _target_dimensions(self, now: float) -> tuple[float, float]:
        if self._state == "success":
            elapsed = now - self._state_entered_at
            pulse = 3.5 * math.sin(math.pi * _clamp(elapsed / 0.28, 0.0, 1.0))
            return TOP_BUTTON_SIZE + pulse, TOP_BUTTON_SIZE + pulse
        return TOP_BUTTON_SIZE, TOP_BUTTON_SIZE

    def _target_border_rgb(self, now: float) -> tuple[int, int, int]:
        if self._state == "idle":
            base_idle = _lerp_rgb(BORDER_IDLE_RGB, BORDER_HOVER_RGB, self._hover_t)
            if self._input_active_t > 0.01:
                pulse = 0.5 + 0.5 * math.sin(now * 5.0)
                cyan_glow = _lerp_rgb(INPUT_GLOW_A_RGB, INPUT_GLOW_B_RGB, pulse)
                return _lerp_rgb(base_idle, cyan_glow, self._input_active_t)
            return base_idle
        if self._state == "recording":
            pulse = 0.5 + 0.5 * math.sin(now * 6.0)
            return _lerp_rgb(ACCENT_RECORDING_RGB, (255, 118, 130), pulse * 0.25)
        if self._state == "transcribing":
            wave = 0.5 + 0.5 * math.sin(now * 4.5)
            return _lerp_rgb(ACCENT_TRANSCRIBING_A_RGB, ACCENT_TRANSCRIBING_B_RGB, wave)
        if self._state == "success":
            return ACCENT_SUCCESS_RGB
        flash = 0.5 + 0.5 * math.sin((now - self._state_entered_at) * 16.0)
        return _lerp_rgb(ACCENT_ERROR_RED_RGB, ACCENT_ERROR_AMBER_RGB, flash)

    def _step_animation(self, now: float, dt: float) -> bool:
        changed = self._needs_redraw
        self._needs_redraw = False

        # Auto-stop & transcribe after MAX_RECORD_SECONDS (60s).
        if (
            self._state == "recording"
            and (now - self._state_entered_at) >= MAX_RECORD_SECONDS
        ):
            self._apply_state("transcribing")
            self.on_stop_record()
            changed = True

        target_hover = 1.0 if (self._hovered and self._state == "idle") else 0.0
        if abs(target_hover - self._hover_t) > 0.005:
            alpha_h = 1.0 - math.exp(-18.0 * dt)
            self._hover_t = _lerp(self._hover_t, target_hover, alpha_h)
            changed = True
        elif self._hover_t != target_hover:
            self._hover_t = target_hover
            changed = True

        target_input_active = 1.0 if self._input_active else 0.0
        if abs(target_input_active - self._input_active_t) > 0.005:
            alpha_ia = 1.0 - math.exp(-12.0 * dt)
            self._input_active_t = _lerp(
                self._input_active_t, target_input_active, alpha_ia
            )
            changed = True
        elif self._input_active_t != target_input_active:
            self._input_active_t = target_input_active
            changed = True

        target_bar_vis = 1.0 if self._state == "recording" else 0.0
        if abs(target_bar_vis - self._bar_vis) > 0.005:
            alpha_bar = 1.0 - math.exp(-18.0 * dt)
            self._bar_vis = _lerp(self._bar_vis, target_bar_vis, alpha_bar)
            changed = True
        elif self._bar_vis != target_bar_vis:
            self._bar_vis = target_bar_vis
            changed = True

        target_cancel_h = (
            1.0 if (self._cancel_hovered and self._state == "recording") else 0.0
        )
        if abs(target_cancel_h - self._cancel_hover_t) > 0.005:
            alpha_ch = 1.0 - math.exp(-22.0 * dt)
            self._cancel_hover_t = _lerp(self._cancel_hover_t, target_cancel_h, alpha_ch)
            changed = True
        elif self._cancel_hover_t != target_cancel_h:
            self._cancel_hover_t = target_cancel_h
            changed = True

        target_send_h = 1.0 if self._send_hovered else 0.0
        if abs(target_send_h - self._send_hover_t) > 0.005:
            alpha_sh = 1.0 - math.exp(-18.0 * dt)
            self._send_hover_t = _lerp(self._send_hover_t, target_send_h, alpha_sh)
            changed = True
        elif self._send_hover_t != target_send_h:
            self._send_hover_t = target_send_h
            changed = True

        if self._send_flash_t > 0.005:
            self._send_flash_t *= math.exp(-10.0 * dt)
            changed = True
        elif self._send_flash_t != 0.0:
            self._send_flash_t = 0.0
            changed = True

        target_w, target_h = self._target_dimensions(now)
        size_alpha = 1.0 - math.exp(-18.0 * dt)
        if abs(target_w - self._pill_w) > 0.15:
            self._pill_w = _lerp(self._pill_w, target_w, size_alpha)
            changed = True
        elif self._pill_w != target_w:
            self._pill_w = target_w
            changed = True

        if abs(target_h - self._pill_h) > 0.15:
            self._pill_h = _lerp(self._pill_h, target_h, size_alpha)
            changed = True
        elif self._pill_h != target_h:
            self._pill_h = target_h
            changed = True

        target_border = self._target_border_rgb(now)
        color_alpha = 1.0 - math.exp(-18.0 * dt)
        new_border = []
        for idx in range(3):
            diff = target_border[idx] - self._border_rgb[idx]
            if abs(diff) > 0.35:
                new_border.append(self._border_rgb[idx] + diff * color_alpha)
                changed = True
            else:
                if self._border_rgb[idx] != float(target_border[idx]):
                    changed = True
                new_border.append(float(target_border[idx]))
        self._border_rgb = (new_border[0], new_border[1], new_border[2])

        if self._state == "recording":
            try:
                raw_level = float(self.get_audio_level())
            except Exception:
                raw_level = 0.0
            raw_level = _clamp(raw_level, 0.0, 1.0)
            perceptual = _clamp((raw_level * 1.45) ** 0.72, 0.0, 1.0)
            rate = 0.48 if perceptual > self._audio_level else 0.18
            self._audio_level = _lerp(self._audio_level, perceptual, rate)

            for i in range(9):
                phase = 0.5 + 0.5 * (
                    math.sin(now * 8.2 + i * 0.92) * 0.65
                    + math.cos(now * 4.3 - i * 0.58) * 0.35
                )
                ambient = 0.08 + 0.07 * phase
                driven = self._audio_level * BAR_ENVELOPE[i] * (0.45 + 0.55 * phase)
                target_bar = _clamp(max(ambient, driven), 0.06, 1.0)
                bar_rate = 0.52 if target_bar > self._bar_levels[i] else 0.24
                self._bar_levels[i] = _lerp(self._bar_levels[i], target_bar, bar_rate)
            changed = True
        else:
            if self._audio_level > 0.002:
                self._audio_level *= math.exp(-16.0 * dt)
                for i in range(9):
                    self._bar_levels[i] *= math.exp(-16.0 * dt)
                changed = True
            else:
                self._audio_level = 0.0
                for i in range(9):
                    self._bar_levels[i] = 0.0

        if self._state in ("recording", "transcribing", "success", "error"):
            changed = True
        elif self._state == "idle" and (
            self._input_active or self._input_active_t > 0.005
        ):
            changed = True

        return changed

    def _draw_current_frame(self, now: float) -> None:
        if self._closed or not self._dib_bits.value or not self._hdc_screen or not self._hdc_mem:
            return
        rgba = self._render_frame(now)
        self._push_layered_frame(rgba)

    def _push_layered_frame(self, rgba: np.ndarray) -> None:
        alpha = rgba[:, :, 3]
        rgb_premul = (rgba[:, :, :3].astype(np.uint16) * alpha[:, :, None] + 127) // 255
        bgra = self._bgra_buf
        bgra[:, :, 0] = rgb_premul[:, :, 2]
        bgra[:, :, 1] = rgb_premul[:, :, 1]
        bgra[:, :, 2] = rgb_premul[:, :, 0]
        bgra[:, :, 3] = alpha

        ctypes.memmove(self._dib_bits, bgra.ctypes.data, bgra.nbytes)
        ctypes.set_last_error(0)
        ok = self._user32.UpdateLayeredWindow(
            self.hwnd,
            self._hdc_screen,
            None,
            ctypes.byref(self._ulw_size),
            self._hdc_mem,
            ctypes.byref(self._ulw_pt_zero),
            0,
            ctypes.byref(self._ulw_blend),
            ULW_ALPHA,
        )
        self._last_ulw_ok = bool(ok)
        self._last_ulw_error = ctypes.get_last_error()

    def _local_roi(
        self,
        px: np.ndarray,
        py: np.ndarray,
        cx: float,
        cy: float,
        half_w: float,
        half_h: float,
        ox_px: int,
        oy_px: int,
        roi_w: int,
        roi_h: int,
    ) -> tuple[slice, slice, np.ndarray, np.ndarray] | None:
        """Return local ROI slices and coordinate views bounded to (roi_h, roi_w)."""
        x0 = max(0, int(math.floor(cx - half_w)) - ox_px - 2)
        y0 = max(0, int(math.floor(cy - half_h)) - oy_px - 2)
        x1 = min(roi_w, int(math.ceil(cx + half_w)) - ox_px + 2)
        y1 = min(roi_h, int(math.ceil(cy + half_h)) - oy_px + 2)
        if x0 >= x1 or y0 >= y1:
            return None
        ys = slice(y0, y1)
        xs = slice(x0, x1)
        return ys, xs, px[:, xs], py[ys, :]

    def _compose_pill_into(
        self,
        out_rgb_pm: np.ndarray,
        out_a: np.ndarray,
        cx: float,
        cy: float,
        half_w: float,
        half_h: float,
        fill_rgb_u8: tuple[int, int, int],
        border_rgb_u8: tuple[int, int, int],
        glow_strength: float,
        glow_sigma: float,
        inner_glow_strength: float,
        specular_strength: float,
        border_w: float,
        opacity: float,
        draw_content: Callable[[np.ndarray, np.ndarray, np.ndarray, int, int], None]
        | None,
        radius: float | None = None,
    ) -> tuple[int, int, int, int] | None:
        """Render one pill layer (shadow, outer glow, glass body, content, border)."""
        ox = max(0, int(math.floor(cx - half_w - 13.0)))
        oy = max(0, int(math.floor(cy - half_h - 13.0)))
        ox1 = min(WINDOW_WIDTH, int(math.ceil(cx + half_w + 13.0)))
        oy1 = min(WINDOW_HEIGHT, int(math.ceil(cy + half_h + 15.0)))
        roi_w = ox1 - ox
        roi_h = oy1 - oy
        if roi_w <= 0 or roi_h <= 0 or opacity <= 0.0:
            return None

        px = self._px[:, ox:ox1]
        py = self._py[oy:oy1, :]
        pill_radius = min(half_w, half_h) if radius is None else radius
        border_col = _rgb_norm(border_rgb_u8)
        fill_col = _rgb_norm(fill_rgb_u8)

        dst_pm = out_rgb_pm[oy:oy1, ox:ox1]
        dst_a = out_a[oy:oy1, ox:ox1]

        d_pill = _sdf_rounded_box(px, py, cx, cy, half_w, half_h, pill_radius)

        # 1. Soft Gaussian drop shadow underneath (+2.5px Y offset).
        d_shadow = _sdf_rounded_box(px, py - 2.5, cx, cy, half_w, half_h, pill_radius)
        shadow_a = (
            0.40 * opacity * np.exp(-0.5 * (np.maximum(d_shadow, 0.0) / 2.0) ** 2)
        ).astype(np.float32)
        dst_pm, dst_a = _composite_premul(dst_pm, dst_a, (0.0, 0.0, 0.0), shadow_a)

        # 2. Ambient outer glow around pill perimeter.
        if glow_strength > 0.005:
            glow_a = (
                glow_strength
                * opacity
                * np.exp(-0.5 * (np.maximum(d_pill, 0.0) / glow_sigma) ** 2)
            ).astype(np.float32)
            dst_pm, dst_a = _composite_premul(dst_pm, dst_a, border_col, glow_a)

        # 3. Pill surface fill, inward rim glow, and top specular glass highlight.
        surf_rgb = np.empty((roi_h, roi_w, 3), dtype=np.float32)
        surf_rgb[:, :, 0] = fill_col[0]
        surf_rgb[:, :, 1] = fill_col[1]
        surf_rgb[:, :, 2] = fill_col[2]

        if inner_glow_strength > 0.005:
            inward_t = (
                inner_glow_strength
                * np.exp(-0.5 * (np.maximum(-d_pill, 0.0) / 2.6) ** 2)
            ).astype(np.float32)
            _blend_surface(surf_rgb, inward_t, border_col)

        top_weight = np.clip((cy - py) / max(1.0, half_h * 0.9), 0.0, 1.0) ** 1.4
        spec_cov = (
            np.clip(1.0 - np.abs(d_pill + 1.65) / 0.65, 0.0, 1.0)
            * top_weight
            * specular_strength
        ).astype(np.float32)
        _blend_surface(surf_rgb, spec_cov, (1.0, 1.0, 1.0))

        # 4. Crisp SDF content inside the pill.
        if draw_content is not None:
            draw_content(surf_rgb, px, py, ox, oy)

        # 5. Crisp 1-pixel anti-aliased border ring.
        border_cov = np.clip(border_w + d_pill + 0.5, 0.0, 1.0).astype(np.float32)
        _blend_surface(surf_rgb, border_cov, border_col)

        # 6. Composite smooth pill body over shadow & glow.
        pill_alpha = (_sdf_cov(d_pill) * opacity).astype(np.float32)
        dst_pm, dst_a = _composite_premul(dst_pm, dst_a, surf_rgb, pill_alpha)

        out_rgb_pm[oy:oy1, ox:ox1] = dst_pm
        out_a[oy:oy1, ox:ox1] = dst_a
        return ox, oy, ox1, oy1

    def _render_frame(self, now: float) -> np.ndarray:
        """Render a smooth anti-aliased RGBA (H, W, 4) uint8 frame via analytical SDFs."""
        shake_x = 0.0
        if self._state == "error":
            elapsed = now - self._state_entered_at
            if elapsed < 0.35:
                shake_x = 2.5 * math.sin(elapsed * 45.0) * math.exp(-elapsed * 8.0)

        out_rgb_pm = np.zeros((WINDOW_HEIGHT, WINDOW_WIDTH, 3), dtype=np.float32)
        out_a = np.zeros((WINDOW_HEIGHT, WINDOW_WIDTH), dtype=np.float32)
        ux0, uy0, ux1, uy1 = WINDOW_WIDTH, WINDOW_HEIGHT, 0, 0

        # 1. Render unfolding 1x bottom bar first so it slides cleanly from behind the top button.
        if self._bar_vis > 0.01:
            bar_cy = _lerp(TOP_CENTER_Y + 28.0, BAR_CENTER_Y, self._bar_vis)
            bar_pulse = 0.5 + 0.5 * math.sin(now * 6.0)
            bar_border_rgb = _lerp_rgb(
                ACCENT_RECORDING_RGB, (255, 118, 130), bar_pulse * 0.25
            )
            bar_glow = 0.20 + 0.06 * bar_pulse
            bar_inward = 0.16 + 0.06 * bar_pulse
            box = self._compose_pill_into(
                out_rgb_pm,
                out_a,
                BAR_CENTER_X,
                bar_cy,
                BAR_PILL_WIDTH * 0.5,
                BAR_PILL_HEIGHT * 0.5,
                BG_PILL_RGB,
                bar_border_rgb,
                bar_glow,
                2.1,
                bar_inward,
                0.14,
                1.3,
                self._bar_vis,
                lambda surf, px, py, ox, oy: self._draw_bottom_bar_sdf(
                    surf, px, py, BAR_CENTER_X, bar_cy, now, ox, oy
                ),
            )
            if box is not None:
                ux0, uy0 = min(ux0, box[0]), min(uy0, box[1])
                ux1, uy1 = max(ux1, box[2]), max(uy1, box[3])

        # 2. Render always-visible right Send/Enter tab underneath the circular pill's right arc.
        send_cx = SEND_TAB_CENTER_X + shake_x
        send_cy = TOP_CENTER_Y
        send_intensity = max(self._send_hover_t, self._send_flash_t)
        send_fill_rgb = _lerp_rgb(BG_PILL_RGB, BG_PILL_HOVER_RGB, send_intensity)
        send_border_rgb = _lerp_rgb(
            _lerp_rgb(
                BORDER_IDLE_RGB,
                _lerp_rgb(BORDER_HOVER_RGB, SEND_ICON_HOVER_RGB, 0.45),
                self._send_hover_t,
            ),
            SEND_ICON_FLASH_RGB,
            self._send_flash_t,
        )
        send_glow = max(0.14 * self._send_hover_t, 0.36 * self._send_flash_t)
        send_inward = max(0.08 * self._send_hover_t, 0.28 * self._send_flash_t)
        send_border_w = _lerp(1.3, 1.85, self._send_flash_t)
        send_icon_cx = SEND_ICON_CENTER_X + shake_x
        send_box = self._compose_pill_into(
            out_rgb_pm,
            out_a,
            send_cx,
            send_cy,
            SEND_TAB_WIDTH * 0.5,
            SEND_TAB_HEIGHT * 0.5,
            send_fill_rgb,
            send_border_rgb,
            send_glow,
            2.1,
            send_inward,
            0.12 + 0.06 * send_intensity,
            send_border_w,
            1.0,
            lambda surf, px, py, ox, oy: self._draw_send_tab_sdf(
                surf, px, py, send_icon_cx, send_cy, ox, oy
            ),
            radius=SEND_TAB_RADIUS,
        )
        if send_box is not None:
            ux0, uy0 = min(ux0, send_box[0]), min(uy0, send_box[1])
            ux1, uy1 = max(ux1, send_box[2]), max(uy1, send_box[3])

        # 3. Render top circular 74x74 pill cleanly ON TOP (idle, recording, transcribing, success, error).
        cx = TOP_CENTER_X + shake_x
        cy = TOP_CENTER_Y
        half_w = self._pill_w * 0.5
        half_h = self._pill_h * 0.5
        border_rgb_u8 = (
            int(round(self._border_rgb[0])),
            int(round(self._border_rgb[1])),
            int(round(self._border_rgb[2])),
        )
        border_col = _rgb_norm(border_rgb_u8)
        fill_rgb_u8 = BG_PILL_RGB
        if self._state == "idle" and self._hover_t > 0.0:
            fill_rgb_u8 = _lerp_rgb(BG_PILL_RGB, BG_PILL_HOVER_RGB, self._hover_t)

        glow_strength = 0.0
        glow_sigma = 2.1
        inner_glow_strength = 0.0
        border_w = 1.3
        if self._state == "idle":
            glow_strength = 0.16 * self._hover_t
            inner_glow_strength = 0.10 * self._hover_t
            if self._input_active_t > 0.01:
                input_pulse = 0.5 + 0.5 * math.sin(now * 5.0)
                glow_strength = max(
                    glow_strength, (0.62 + 0.20 * input_pulse) * self._input_active_t
                )
                glow_sigma = _lerp(2.1, 2.65, self._input_active_t)
                inner_glow_strength = max(
                    inner_glow_strength,
                    (0.36 + 0.14 * input_pulse) * self._input_active_t,
                )
                border_w = _lerp(1.3, 2.2, self._input_active_t)
        elif self._state == "recording":
            rec_pulse = 0.5 + 0.5 * math.sin(now * 6.0)
            glow_strength = 0.22 + 0.06 * rec_pulse
            inner_glow_strength = 0.18 + 0.06 * rec_pulse
        elif self._state == "transcribing":
            glow_strength = 0.18
            inner_glow_strength = 0.15
        elif self._state == "success":
            elapsed_ok = now - self._state_entered_at
            glow_strength = 0.20 * (1.0 - 0.5 * _clamp(elapsed_ok / 0.45, 0.0, 1.0))
            inner_glow_strength = 0.15
        elif self._state == "error":
            glow_strength = 0.18
            inner_glow_strength = 0.15

        def draw_top_content(
            surf_rgb: np.ndarray, px: np.ndarray, py: np.ndarray, ox: int, oy: int
        ) -> None:
            if self._state == "idle":
                mic_vis = _clamp(
                    1.0 - (self._pill_w - TOP_BUTTON_SIZE) / 22.0, 0.0, 1.0
                )
                if mic_vis > 0.02:
                    self._draw_idle_mic_sdf(surf_rgb, px, py, cx, cy, mic_vis, ox, oy)
            elif self._state == "recording":
                stop_vis = _clamp(
                    1.0 - (self._pill_w - TOP_BUTTON_SIZE) / 22.0, 0.0, 1.0
                )
                if stop_vis > 0.02:
                    self._draw_top_stop_sdf(
                        surf_rgb, px, py, cx, cy, now, stop_vis, ox, oy
                    )
            elif self._state == "transcribing":
                trans_vis = _clamp(
                    1.0 - (self._pill_w - TOP_BUTTON_SIZE) / 22.0, 0.0, 1.0
                )
                if trans_vis > 0.02:
                    self._draw_transcribing_sdf(
                        surf_rgb, px, py, cx, cy, now, trans_vis, ox, oy
                    )
            elif self._state == "success":
                ok_vis = _clamp(1.0 - (self._pill_w - 82.0) / 36.0, 0.0, 1.0)
                if ok_vis > 0.02:
                    self._draw_success_sdf(
                        surf_rgb, px, py, cx, cy, now, ok_vis, ox, oy
                    )
            elif self._state == "error":
                err_vis = _clamp(1.0 - (self._pill_w - 82.0) / 36.0, 0.0, 1.0)
                if err_vis > 0.02:
                    self._draw_error_sdf(
                        surf_rgb, px, py, cx, cy, border_col, err_vis, ox, oy
                    )

        top_box = self._compose_pill_into(
            out_rgb_pm,
            out_a,
            cx,
            cy,
            half_w,
            half_h,
            fill_rgb_u8,
            border_rgb_u8,
            glow_strength,
            glow_sigma,
            inner_glow_strength,
            0.14 + 0.06 * self._hover_t,
            border_w,
            1.0,
            draw_top_content,
        )
        if top_box is not None:
            ux0, uy0 = min(ux0, top_box[0]), min(uy0, top_box[1])
            ux1, uy1 = max(ux1, top_box[2]), max(uy1, top_box[3])

        rgba = np.zeros((WINDOW_HEIGHT, WINDOW_WIDTH, 4), dtype=np.uint8)
        if ux0 < ux1 and uy0 < uy1:
            sub_pm = out_rgb_pm[uy0:uy1, ux0:ux1]
            sub_a = np.clip(out_a[uy0:uy1, ux0:ux1], 0.0, 1.0)
            alpha_u8 = np.rint(sub_a * 255.0).astype(np.uint8)
            nonzero = alpha_u8 > 0
            safe_a = np.where(nonzero, sub_a, 1.0)[:, :, None]
            straight_rgb = np.where(
                nonzero[:, :, None], np.clip(sub_pm / safe_a, 0.0, 1.0), 0.0
            )
            rgba[uy0:uy1, ux0:ux1, :3] = np.rint(straight_rgb * 255.0).astype(np.uint8)
            rgba[uy0:uy1, ux0:ux1, 3] = alpha_u8
        return rgba

    def _draw_idle_mic_sdf(
        self,
        surf_rgb: np.ndarray,
        px: np.ndarray,
        py: np.ndarray,
        cx: float,
        cy: float,
        visibility: float,
        ox: int,
        oy: int,
    ) -> None:
        roi = self._local_roi(
            px, py, cx, cy, 12.0, 17.0, ox, oy, surf_rgb.shape[1], surf_rgb.shape[0]
        )
        if roi is None:
            return
        ys, xs, rpx, rpy = roi

        d_cap = _sdf_rounded_box(rpx, rpy, cx, cy - 5.5, 4.8, 8.8, 4.8)

        arc_cy = cy - 1.6
        arc_r = 9.2
        half_stroke = 1.25
        d_arc = _sdf_bottom_semicircle_arc(rpx, rpy, cx, arc_cy, arc_r, half_stroke)
        d_tip_l = _sdf_segment(
            rpx, rpy, cx - arc_r, arc_cy - 1.6, cx - arc_r, arc_cy, half_stroke
        )
        d_tip_r = _sdf_segment(
            rpx, rpy, cx + arc_r, arc_cy - 1.6, cx + arc_r, arc_cy, half_stroke
        )

        stem_top = arc_cy + arc_r
        foot_y = cy + 14.0
        d_stem = _sdf_segment(rpx, rpy, cx, stem_top, cx, foot_y, half_stroke)
        d_base = _sdf_segment(
            rpx, rpy, cx - 5.5, foot_y, cx + 5.5, foot_y, half_stroke
        )

        d_mic = np.minimum(
            d_cap,
            np.minimum(
                np.minimum(d_arc, np.minimum(d_tip_l, d_tip_r)),
                np.minimum(d_stem, d_base),
            ),
        )
        cov = (_sdf_cov(d_mic) * visibility).astype(np.float32)
        _blend_surface(surf_rgb[ys, xs], cov, (1.0, 1.0, 1.0))

    def _draw_top_stop_sdf(
        self,
        surf_rgb: np.ndarray,
        px: np.ndarray,
        py: np.ndarray,
        cx: float,
        cy: float,
        now: float,
        visibility: float,
        ox: int,
        oy: int,
    ) -> None:
        pulse = 0.5 + 0.5 * math.sin(now * 6.5)
        halo_r = 21.0 + 3.5 * pulse
        roi = self._local_roi(
            px,
            py,
            cx,
            cy,
            halo_r + 1.5,
            halo_r + 1.5,
            ox,
            oy,
            surf_rgb.shape[1],
            surf_rgb.shape[0],
        )
        if roi is None:
            return
        ys, xs, rpx, rpy = roi
        sub = surf_rgb[ys, xs]
        coral = _rgb_norm(ACCENT_RECORDING_RGB)

        d_halo = _sdf_circle(rpx, rpy, cx, cy, halo_r)
        halo_cov = (_sdf_cov(d_halo) * (0.24 + 0.14 * pulse) * visibility).astype(
            np.float32
        )
        _blend_surface(sub, halo_cov, coral)

        d_btn = _sdf_circle(rpx, rpy, cx, cy, 16.0)
        btn_cov = (_sdf_cov(d_btn) * visibility).astype(np.float32)
        _blend_surface(sub, btn_cov, coral)

        d_sq = _sdf_rounded_box(rpx, rpy, cx, cy, 5.6, 5.6, 2.0)
        sq_cov = (_sdf_cov(d_sq) * visibility).astype(np.float32)
        _blend_surface(sub, sq_cov, (1.0, 1.0, 1.0))

    def _draw_bottom_bar_sdf(
        self,
        surf_rgb: np.ndarray,
        px: np.ndarray,
        py: np.ndarray,
        bar_cx: float,
        bar_cy: float,
        now: float,
        ox: int,
        oy: int,
    ) -> None:
        roi_h, roi_w = surf_rgb.shape[:2]
        bar_left = bar_cx - BAR_PILL_WIDTH * 0.5
        bar_right = bar_cx + BAR_PILL_WIDTH * 0.5

        # Left: 9-bar audio-reactive waveform at 1x original size.
        bars_left = bar_left + 16.0
        bars_right = bar_left + 84.0
        bar_step = (bars_right - bars_left) / 8.0
        bar_hw = 1.75
        for i in range(9):
            bx = bars_left + i * bar_step
            lvl = self._bar_levels[i]
            bar_hh = _lerp(1.75, 13.0, lvl)
            bar_roi = self._local_roi(
                px, py, bx, bar_cy, bar_hw + 1.0, bar_hh + 1.0, ox, oy, roi_w, roi_h
            )
            if bar_roi is None:
                continue
            ys, xs, rpx, rpy = bar_roi
            d_bar = _sdf_rounded_box(rpx, rpy, bx, bar_cy, bar_hw, bar_hh, bar_hw)
            bar_cov = _sdf_cov(d_bar)
            bar_col = _rgb_norm(_lerp_rgb((215, 218, 226), WHITE_RGB, lvl))
            _blend_surface(surf_rgb[ys, xs], bar_cov, bar_col)

        # Right: circular X cancel button inside its tight ROI.
        cancel_cx = bar_right - 21.0
        cancel_roi = self._local_roi(
            px, py, cancel_cx, bar_cy, 11.5, 11.5, ox, oy, roi_w, roi_h
        )
        if cancel_roi is not None:
            ys, xs, rpx, rpy = cancel_roi
            sub = surf_rgb[ys, xs]
            d_cancel_bg = _sdf_circle(rpx, rpy, cancel_cx, bar_cy, 10.5)
            bg_alpha = _lerp(0.09, 0.24, self._cancel_hover_t)
            bg_col = _rgb_norm(
                _lerp_rgb(WHITE_RGB, ACCENT_RECORDING_RGB, self._cancel_hover_t * 0.65)
            )
            cancel_bg_cov = (_sdf_cov(d_cancel_bg) * bg_alpha).astype(np.float32)
            _blend_surface(sub, cancel_bg_cov, bg_col)

            cross_half = 3.6
            d_x1 = _sdf_segment(
                rpx,
                rpy,
                cancel_cx - cross_half,
                bar_cy - cross_half,
                cancel_cx + cross_half,
                bar_cy + cross_half,
                0.95,
            )
            d_x2 = _sdf_segment(
                rpx,
                rpy,
                cancel_cx - cross_half,
                bar_cy + cross_half,
                cancel_cx + cross_half,
                bar_cy - cross_half,
                0.95,
            )
            d_cross = np.minimum(d_x1, d_x2)
            cross_cov = _sdf_cov(d_cross)
            cross_col = _rgb_norm(
                _lerp_rgb(CANCEL_ICON_RGB, WHITE_RGB, self._cancel_hover_t)
            )
            _blend_surface(sub, cross_cov, cross_col)

        # Middle: 1x Segoe UI 12px countdown timer centered symmetrically between waveform & cancel X.
        elapsed = (
            max(0.0, now - self._state_entered_at)
            if self._state == "recording"
            else self._last_record_elapsed
        )
        secs_left = max(0, min(60, 60 - int(elapsed)))
        timer_label = f"{secs_left // 60}:{secs_left % 60:02d}"
        timer_rgb = TIMER_URGENT_RGB if secs_left <= 10 else TIMER_TEXT_RGB
        timer_mask = self._get_timer_mask(timer_label)
        timer_cx = 0.5 * ((bars_right + bar_hw) + (cancel_cx - 10.5))
        self._blit_mask(
            surf_rgb,
            timer_mask,
            timer_cx,
            bar_cy,
            _rgb_norm(timer_rgb),
            1.0,
            ox,
            oy,
        )

    def _blit_mask(
        self,
        surf_rgb: np.ndarray,
        mask: np.ndarray,
        cx: float,
        cy: float,
        color: tuple[float, float, float],
        opacity: float,
        ox: int,
        oy: int,
    ) -> None:
        mh, mw = mask.shape
        x0 = int(round(cx - mw * 0.5)) - ox
        y0 = int(round(cy - mh * 0.5)) - oy
        x1 = x0 + mw
        y1 = y0 + mh

        dx0 = max(0, x0)
        dy0 = max(0, y0)
        dx1 = min(surf_rgb.shape[1], x1)
        dy1 = min(surf_rgb.shape[0], y1)
        if dx0 >= dx1 or dy0 >= dy1:
            return

        sx0 = dx0 - x0
        sy0 = dy0 - y0
        sx1 = sx0 + (dx1 - dx0)
        sy1 = sy0 + (dy1 - dy0)
        cov = mask[sy0:sy1, sx0:sx1] * opacity
        _blend_surface(surf_rgb[dy0:dy1, dx0:dx1], cov, color)

    def _draw_send_tab_sdf(
        self,
        surf_rgb: np.ndarray,
        px: np.ndarray,
        py: np.ndarray,
        sx: float,
        sy: float,
        ox: int,
        oy: int,
    ) -> None:
        roi = self._local_roi(
            px, py, sx, sy, 14.0, 12.5, ox, oy, surf_rgb.shape[1], surf_rgb.shape[0]
        )
        if roi is None:
            return
        ys, xs, rpx, rpy = roi
        left_x = sx - 11.5
        tip_x = sx + 11.5
        notch_x = sx - 6.0
        top_y = sy - 10.0
        bot_y = sy + 10.0

        d_upper = _sdf_triangle(rpx, rpy, left_x, top_y, tip_x, sy, notch_x, sy)
        d_lower = _sdf_triangle(rpx, rpy, left_x, bot_y, tip_x, sy, notch_x, sy)
        d_plane = np.minimum(d_upper, d_lower)
        d_crease = _sdf_segment(rpx, rpy, notch_x - 0.5, sy, sx + 3.0, sy, 0.85)
        d_icon = np.maximum(d_plane, -d_crease)
        cov = _sdf_cov(d_icon)

        base_col = _lerp_rgb(SEND_ICON_RGB, SEND_ICON_HOVER_RGB, self._send_hover_t)
        icon_col = _rgb_norm(_lerp_rgb(base_col, SEND_ICON_FLASH_RGB, self._send_flash_t))
        _blend_surface(surf_rgb[ys, xs], cov, icon_col)

    def _draw_transcribing_sdf(
        self,
        surf_rgb: np.ndarray,
        px: np.ndarray,
        py: np.ndarray,
        cx: float,
        cy: float,
        now: float,
        visibility: float,
        ox: int,
        oy: int,
    ) -> None:
        roi_h, roi_w = surf_rgb.shape[:2]
        dot_spacing = 10.5
        start_x = cx - 2.0 * dot_spacing
        for i in range(5):
            phase = now * 6.5 - i * 0.75
            wave = math.sin(phase)
            norm = 0.5 + 0.5 * wave
            dx = start_x + i * dot_spacing
            dy = cy - 4.2 * wave
            r = 2.7 + 0.9 * norm
            dot_roi = self._local_roi(
                px, py, dx, dy, r + 2.5, r + 2.5, ox, oy, roi_w, roi_h
            )
            if dot_roi is None:
                continue
            ys, xs, rpx, rpy = dot_roi
            sub = surf_rgb[ys, xs]
            dot_col = _rgb_norm(
                _lerp_rgb(ACCENT_TRANSCRIBING_A_RGB, ACCENT_TRANSCRIBING_B_RGB, norm)
            )
            d_glow = _sdf_circle(rpx, rpy, dx, dy, r + 1.8)
            glow_cov = (_sdf_cov(d_glow) * 0.22 * visibility).astype(np.float32)
            _blend_surface(sub, glow_cov, dot_col)

            d_dot = _sdf_circle(rpx, rpy, dx, dy, r)
            dot_cov = (_sdf_cov(d_dot) * visibility).astype(np.float32)
            _blend_surface(sub, dot_cov, dot_col)

    def _draw_success_sdf(
        self,
        surf_rgb: np.ndarray,
        px: np.ndarray,
        py: np.ndarray,
        cx: float,
        cy: float,
        now: float,
        visibility: float,
        ox: int,
        oy: int,
    ) -> None:
        roi = self._local_roi(
            px, py, cx, cy, 29.0, 29.0, ox, oy, surf_rgb.shape[1], surf_rgb.shape[0]
        )
        if roi is None:
            return
        ys, xs, rpx, rpy = roi
        sub = surf_rgb[ys, xs]
        green = _rgb_norm(ACCENT_SUCCESS_RGB)
        elapsed = now - self._state_entered_at
        ring_t = _clamp(elapsed / 0.40, 0.0, 1.0)
        ring_r = 15.0 + 11.0 * ring_t
        ring_alpha = (1.0 - ring_t) * 0.45 * visibility
        if ring_alpha > 0.01:
            d_ring = np.abs(_sdf_circle(rpx, rpy, cx, cy, ring_r)) - 1.0
            ring_cov = (_sdf_cov(d_ring) * ring_alpha).astype(np.float32)
            _blend_surface(sub, ring_cov, green)

        d_s1 = _sdf_segment(rpx, rpy, cx - 9.8, cy + 0.5, cx - 2.4, cy + 7.6, 1.75)
        d_s2 = _sdf_segment(rpx, rpy, cx - 2.4, cy + 7.6, cx + 10.4, cy - 7.4, 1.75)
        d_chk = np.minimum(d_s1, d_s2)
        chk_cov = (_sdf_cov(d_chk) * visibility).astype(np.float32)
        _blend_surface(sub, chk_cov, green)

    def _draw_error_sdf(
        self,
        surf_rgb: np.ndarray,
        px: np.ndarray,
        py: np.ndarray,
        cx: float,
        cy: float,
        border_col: tuple[float, float, float],
        visibility: float,
        ox: int,
        oy: int,
    ) -> None:
        roi = self._local_roi(
            px, py, cx, cy, 5.0, 14.0, ox, oy, surf_rgb.shape[1], surf_rgb.shape[0]
        )
        if roi is None:
            return
        ys, xs, rpx, rpy = roi
        d_bar = _sdf_segment(rpx, rpy, cx, cy - 9.8, cx, cy + 2.4, 1.75)
        d_dot = _sdf_circle(rpx, rpy, cx, cy + 9.4, 2.1)
        d_err = np.minimum(d_bar, d_dot)
        cov = (_sdf_cov(d_err) * visibility).astype(np.float32)
        _blend_surface(surf_rgb[ys, xs], cov, border_col)
