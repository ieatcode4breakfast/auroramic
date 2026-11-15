"""Generate multi-resolution Windows application icon `auroramic.ico` for AuroraMic."""

from __future__ import annotations

from pathlib import Path
import numpy as np
from PIL import Image

ICON_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
MASTER_SIZE = 512

BG_SQUIRCLE_RGB = (15, 17, 24)  # #0F1118
AURORA_CYAN_RGB = (0, 216, 255)  # #00D8FF
AURORA_AQUA_RGB = (30, 240, 255)  # #1EF0FF
SEND_BLUE_RGB = (0, 132, 255)  # #0084FF
SEND_BLUE_HIGHLIGHT_RGB = (64, 176, 255)
WHITE_RGB = (255, 255, 255)


def _norm(rgb: tuple[int, int, int]) -> tuple[float, float, float]:
    return (rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0)


def _cov(d: np.ndarray, aa: float = 1.5) -> np.ndarray:
    return np.clip(0.5 - d / aa, 0.0, 1.0).astype(np.float32)


def _blend(
    rgb: np.ndarray,
    cov: np.ndarray,
    color: tuple[float, float, float] | np.ndarray,
) -> None:
    inv = 1.0 - cov
    if isinstance(color, tuple):
        rgb[:, :, 0] = rgb[:, :, 0] * inv + color[0] * cov
        rgb[:, :, 1] = rgb[:, :, 1] * inv + color[1] * cov
        rgb[:, :, 2] = rgb[:, :, 2] * inv + color[2] * cov
    else:
        rgb[:, :, 0] = rgb[:, :, 0] * inv + color[:, :, 0] * cov
        rgb[:, :, 1] = rgb[:, :, 1] * inv + color[:, :, 1] * cov
        rgb[:, :, 2] = rgb[:, :, 2] * inv + color[:, :, 2] * cov


def _sdf_rounded_box(
    px: np.ndarray, py: np.ndarray, cx: float, cy: float, hw: float, hh: float, r: float
) -> np.ndarray:
    r = min(r, hw, hh)
    qx = np.abs(px - cx) - (hw - r)
    qy = np.abs(py - cy) - (hh - r)
    return (
        np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0))
        + np.minimum(np.maximum(qx, qy), 0.0)
        - r
    )


def _sdf_circle(
    px: np.ndarray, py: np.ndarray, cx: float, cy: float, r: float
) -> np.ndarray:
    return np.hypot(px - cx, py - cy) - r


def _sdf_segment(
    px: np.ndarray,
    py: np.ndarray,
    ax: float,
    ay: float,
    bx: float,
    by: float,
    half_width: float,
) -> np.ndarray:
    pax = px - ax
    pay = py - ay
    bax = bx - ax
    bay = by - ay
    denom = max(1e-8, bax * bax + bay * bay)
    h = np.clip((pax * bax + pay * bay) / denom, 0.0, 1.0)
    return np.hypot(pax - bax * h, pay - bay * h) - half_width


def _sdf_bottom_semicircle_arc(
    px: np.ndarray,
    py: np.ndarray,
    cx: float,
    cy: float,
    radius: float,
    half_width: float,
) -> np.ndarray:
    dx = px - cx
    dy = py - cy
    dist_lower = np.abs(np.hypot(dx, dy) - radius)
    dist_upper = np.hypot(np.abs(dx) - radius, dy)
    return np.where(dy >= 0.0, dist_lower, dist_upper) - half_width


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


def render_icon_master(size: int = MASTER_SIZE) -> Image.Image:
    """Render the high-res AuroraMic RGBA master image using analytical SDFs."""
    px = np.arange(size, dtype=np.float32)[None, :] + 0.5
    py = np.arange(size, dtype=np.float32)[:, None] + 0.5
    cx = size * 0.5
    cy = size * 0.5

    # Dark rounded squircle orb (#0F1118).
    squircle_hw = size * 0.445
    squircle_r = size * 0.215
    d_sq = _sdf_rounded_box(px, py, cx, cy, squircle_hw, squircle_hw, squircle_r)
    sq_alpha = _cov(d_sq, aa=1.8)

    bg_col = _norm(BG_SQUIRCLE_RGB)
    cyan_col = _norm(AURORA_CYAN_RGB)
    aqua_col = _norm(AURORA_AQUA_RGB)
    blue_col = _norm(SEND_BLUE_RGB)
    blue_hi_col = _norm(SEND_BLUE_HIGHLIGHT_RGB)

    surf = np.empty((size, size, 3), dtype=np.float32)
    surf[:, :, 0] = bg_col[0]
    surf[:, :, 1] = bg_col[1]
    surf[:, :, 2] = bg_col[2]

    # Subtle vertical aurora tint inside the squircle + crisp subtle border rim.
    grad_t = np.clip((px - py * 0.6 + size * 0.2) / (size * 1.1), 0.0, 1.0)
    aurora_field = np.empty((size, size, 3), dtype=np.float32)
    for ch in range(3):
        aurora_field[:, :, ch] = cyan_col[ch] * (1.0 - grad_t) + aqua_col[ch] * grad_t

    rim_glow = (0.18 * np.exp(-0.5 * (np.maximum(-d_sq, 0.0) / (size * 0.045)) ** 2)).astype(
        np.float32
    )
    _blend(surf, rim_glow, aurora_field)
    squircle_rim = (
        np.clip(1.0 - np.abs(d_sq + 2.5) / 2.2, 0.0, 1.0) * 0.35
    ).astype(np.float32)
    _blend(surf, squircle_rim, aurora_field)

    # Vibrant cyan-aqua glowing aurora ring centered slightly upper-left to balance the send spark.
    ring_cx = cx - size * 0.02
    ring_cy = cy
    ring_r = size * 0.295
    d_ring_band = np.abs(_sdf_circle(px, py, ring_cx, ring_cy, ring_r))
    ring_halo = (
        0.58 * np.exp(-0.5 * (d_ring_band / (size * 0.042)) ** 2)
    ).astype(np.float32)
    _blend(surf, ring_halo, aurora_field)

    ring_core = _cov(d_ring_band - size * 0.015, aa=1.8)
    _blend(surf, ring_core, aurora_field)

    # Crisp white microphone silhouette centered inside the aurora ring.
    mic_cx = ring_cx
    mic_cy = ring_cy - size * 0.005
    d_cap = _sdf_rounded_box(
        px, py, mic_cx, mic_cy - size * 0.046, size * 0.036, size * 0.068, size * 0.036
    )
    arc_cy = mic_cy - size * 0.012
    arc_r = size * 0.068
    half_stroke = size * 0.0115
    d_arc = _sdf_bottom_semicircle_arc(px, py, mic_cx, arc_cy, arc_r, half_stroke)
    d_tip_l = _sdf_segment(
        px, py, mic_cx - arc_r, arc_cy - size * 0.014, mic_cx - arc_r, arc_cy, half_stroke
    )
    d_tip_r = _sdf_segment(
        px, py, mic_cx + arc_r, arc_cy - size * 0.014, mic_cx + arc_r, arc_cy, half_stroke
    )
    stem_top = arc_cy + arc_r
    foot_y = mic_cy + size * 0.112
    d_stem = _sdf_segment(px, py, mic_cx, stem_top, mic_cx, foot_y, half_stroke)
    d_base = _sdf_segment(
        px, py, mic_cx - size * 0.044, foot_y, mic_cx + size * 0.044, foot_y, half_stroke
    )
    d_mic = np.minimum(
        d_cap,
        np.minimum(
            np.minimum(d_arc, np.minimum(d_tip_l, d_tip_r)),
            np.minimum(d_stem, d_base),
        ),
    )
    _blend(surf, _cov(d_mic, aa=1.6), _norm(WHITE_RGB))

    # Messenger-blue (#0084FF) send spark / paper-plane accent on the lower-right aurora rim.
    sx = cx + size * 0.22
    sy = cy + size * 0.205
    badge_r = size * 0.125
    d_badge = _sdf_circle(px, py, sx, sy, badge_r)
    badge_halo = (
        0.42 * np.exp(-0.5 * (np.maximum(d_badge, 0.0) / (size * 0.032)) ** 2)
    ).astype(np.float32)
    _blend(surf, badge_halo, blue_col)
    _blend(surf, _cov(d_badge, aa=1.8), bg_col)
    badge_ring = np.clip(1.0 - np.abs(d_badge + 2.5) / 2.2, 0.0, 1.0).astype(np.float32)
    _blend(surf, badge_ring * 0.85, blue_hi_col)

    plane_scale = size * 0.088
    p_left = sx - plane_scale * 0.82
    p_tip = sx + plane_scale * 0.92
    p_notch = sx - plane_scale * 0.38
    p_top = sy - plane_scale * 0.72
    p_bot = sy + plane_scale * 0.72

    d_upper = _sdf_triangle(px, py, p_left, p_top, p_tip, sy, p_notch, sy)
    d_lower = _sdf_triangle(px, py, p_left, p_bot, p_tip, sy, p_notch, sy)
    d_plane = np.minimum(d_upper, d_lower)
    d_crease = _sdf_segment(
        px, py, p_notch - 2.0, sy, sx + plane_scale * 0.28, sy, size * 0.0055
    )
    d_spark = np.maximum(d_plane, -d_crease)
    _blend(surf, _cov(d_spark, aa=1.4), blue_col)

    rgba = np.empty((size, size, 4), dtype=np.uint8)
    rgba[:, :, :3] = np.rint(np.clip(surf, 0.0, 1.0) * 255.0).astype(np.uint8)
    rgba[:, :, 3] = np.rint(np.clip(sq_alpha, 0.0, 1.0) * 255.0).astype(np.uint8)
    return Image.fromarray(rgba, mode="RGBA")


def generate_icon(output_path: Path | None = None) -> Path:
    """Render and write multi-resolution `auroramic.ico` next to `main.py`."""
    repo_root = Path(__file__).resolve().parent.parent
    target = output_path if output_path is not None else (repo_root / "auroramic.ico")
    target.parent.mkdir(parents=True, exist_ok=True)
    master = render_icon_master()
    master.save(target, format="ICO", sizes=ICON_SIZES)
    return target


if __name__ == "__main__":
    out = generate_icon()
    print(f"Generated {out}")
