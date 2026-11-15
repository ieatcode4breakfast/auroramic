"""End-to-end verification for AuroraMic Send/Enter tab, sticky-glow fix, dual-pill 220x164 UI, and main.py."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import io
import json
import logging
import sys
import tempfile
import threading
import time
import types
import wave
from pathlib import Path
from unittest.mock import patch

APP_DIR = Path(__file__).resolve().parent.parent
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

import httpx  # noqa: F401
import dotenv  # noqa: F401
import PIL  # noqa: F401
import win32api  # noqa: F401
import numpy as np
import sounddevice  # noqa: F401

import config
import main
from recorder import AudioRecorder
from transcriber import GroqTranscriber
from typer import (
    CURSORINFO,
    GWL_EXSTYLE,
    INPUT_KEYBOARD,
    KEYEVENTF_KEYUP,
    VK_LBUTTON,
    VK_RETURN,
    WS_EX_NOACTIVATE,
    WS_EX_TOOLWINDOW,
    WS_EX_TOPMOST,
)
from ui import (
    BAR_CENTER_Y,
    FloatingMicUI,
    QUIT_LABEL,
    QUIT_WIN_H,
    QUIT_WIN_W,
    SEND_ICON_CENTER_X,
    SEND_ICON_RGB,
    SEND_TAB_HEIGHT,
    SEND_TAB_LEFT,
    SEND_TAB_RIGHT,
    TOP_BUTTON_SIZE,
    TOP_CENTER_X,
    TOP_CENTER_Y,
    WINDOW_HEIGHT,
    WINDOW_WIDTH,
)


def make_silent_wav(duration_s: float = 0.25, sample_rate: int = 16000) -> bytes:
    num_frames = int(duration_s * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00\x00" * num_frames)
    return buf.getvalue()


def make_mouse_event(
    x: float, y: float, x_root: int = 500, y_root: int = 500
) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        x=int(round(x)), y=int(round(y)), x_root=x_root, y_root=y_root
    )


def alpha_bbox(rgba_pm: np.ndarray, min_alpha: int = 245) -> tuple[int, int, int, int]:
    ys, xs = np.where(rgba_pm[:, :, 3] >= min_alpha)
    if len(xs) == 0 or len(ys) == 0:
        return (0, 0, -1, -1)
    return (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))


def main_verify() -> None:
    print("1) Checking config & branding constants...")
    assert config.SAMPLE_RATE == 16000
    assert config.CHANNELS == 1
    assert config.GROQ_MODEL == "whisper-large-v3-turbo"
    assert main.LOG_PATH.name == "auroramic.log"
    assert main.ICO_PATH.name == "auroramic.ico"
    assert main.ICO_PATH.is_file(), f"Missing icon at {main.ICO_PATH}"
    assert main.MUTEX_NAME == "Local\\AuroraMic_SingleInstance"
    assert hasattr(main, "AuroraMicApp")
    print(
        f"config OK: api_key_present={bool(config.GROQ_API_KEY)} "
        f"rate={config.SAMPLE_RATE} ch={config.CHANNELS} model={config.GROQ_MODEL}"
    )

    print("2) Checking DPI awareness & error sanitization...")
    main.enable_dpi_awareness()
    if config.GROQ_API_KEY:
        redacted = main._sanitize_error(RuntimeError(f"boom {config.GROQ_API_KEY} end"))
        assert config.GROQ_API_KEY not in redacted and "[REDACTED]" in redacted
    else:
        with patch.object(config, "GROQ_API_KEY", "test-dummy-secret"):
            redacted = main._sanitize_error(RuntimeError("boom test-dummy-secret end"))
            assert "test-dummy-secret" not in redacted and "[REDACTED]" in redacted
    print(f"sanitize OK: {redacted}")

    print("3) Checking AudioRecorder real capture & short-clip...")
    rec = AudioRecorder(sample_rate=config.SAMPLE_RATE, channels=config.CHANNELS)
    assert rec.get_level() == 0.0
    rec.start()
    assert rec.is_recording is True
    time.sleep(0.03)
    short_wav = rec.stop()
    assert short_wav == b"", f"Expected empty for short clip, got {len(short_wav)}"
    assert rec.is_recording is False
    assert rec.get_level() == 0.0

    rec.start()
    time.sleep(0.28)
    lvl = rec.get_level()
    assert 0.0 <= lvl <= 1.0
    wav_bytes = rec.stop()
    assert rec.is_recording is False
    assert rec.get_level() == 0.0
    assert len(wav_bytes) > 44
    with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
        assert wf.getnchannels() == 1
        assert wf.getsampwidth() == 2
        assert wf.getframerate() == 16000
        nframes = wf.getnframes()
    print(
        f"recorder OK: short_clip=0B real_clip={len(wav_bytes)}B "
        f"frames={nframes} level={lvl:.4f}"
    )

    print("4) Checking GroqTranscriber...")
    tr = GroqTranscriber()
    assert tr.transcribe(b"") == ""
    if config.GROQ_API_KEY:
        live_text = tr.transcribe(make_silent_wav(0.25))
        assert isinstance(live_text, str)
        print(f"transcriber OK: silent_live_text_repr={live_text!r}")
    else:
        print("transcriber OK: skipped live API call (no GROQ_API_KEY configured)")

    print("5) Checking AuroraMicApp + 220x164 UI + Send/Enter + Sticky-Glow Fix...")
    log_stream = io.StringIO()
    test_logger = logging.getLogger("auroramic.verify")
    test_logger.handlers.clear()
    test_logger.setLevel(logging.INFO)
    test_logger.propagate = False
    test_logger.addHandler(logging.StreamHandler(log_stream))

    tmp_state_ctx = tempfile.TemporaryDirectory()
    tmp_state_dir = Path(tmp_state_ctx.name)
    tmp_state_path = tmp_state_dir / "AuroraMic" / "state.json"
    default_state_patch = patch("ui._default_state_path", return_value=tmp_state_path)
    default_state_patch.start()

    app = main.AuroraMicApp(logger=test_logger)
    ui = app.create_ui()
    assert app.ui is ui
    assert ui.state_path == tmp_state_path
    assert not tmp_state_path.exists()
    assert app._ui_hwnd == ui.hwnd and ui.hwnd > 0
    assert ctypes.windll.user32.IsWindow(ui.hwnd)
    assert app._tracker_after_id is not None
    assert ui.root.title() == "AuroraMic"
    assert QUIT_LABEL == "Quit AuroraMic"
    assert not hasattr(ui, "_menu")
    assert ui._quit_popup is not None and ui._quit_open is False
    assert ui._quit_popup.visible is False
    assert (ui._quit_popup.top.cget("cursor"), ui._quit_popup.canvas.cget("cursor")) == ("hand2", "hand2")

    assert (WINDOW_WIDTH, WINDOW_HEIGHT) == (220, 164)
    rect = wintypes.RECT()
    assert ctypes.windll.user32.GetWindowRect(ui.hwnd, ctypes.byref(rect))
    win_w = rect.right - rect.left
    win_h = rect.bottom - rect.top
    assert (win_w, win_h) == (220, 164), f"Unexpected window size: {(win_w, win_h)}"

    # Cursor before, during drag, and after release stays hand2; drag persists state.json
    cursors_before = (ui.root.cget("cursor"), ui.canvas.cget("cursor"))
    assert cursors_before == ("hand2", "hand2")
    orig_x, orig_y = ui.root.winfo_x(), ui.root.winfo_y()
    expected_default_pos = ui._default_window_position()
    assert (orig_x, orig_y) == expected_default_pos, (
        f"Expected bottom-center default {expected_default_pos}, got {(orig_x, orig_y)}"
    )
    ui._on_press(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y, 500, 500))
    ui._on_drag(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y, 515, 510))
    cursors_during = (ui.root.cget("cursor"), ui.canvas.cget("cursor"))
    assert cursors_during == ("hand2", "hand2")
    ui._on_release(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y, 515, 510))
    cursors_after = (ui.root.cget("cursor"), ui.canvas.cget("cursor"))
    assert cursors_after == ("hand2", "hand2")
    drag_saved = json.loads(tmp_state_path.read_text(encoding="utf-8"))
    assert drag_saved == {"x": orig_x + 15, "y": orig_y + 10}, f"Unexpected saved drag pos: {drag_saved}"
    ui.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+{orig_x}+{orig_y}")
    ui.root.update_idletasks()

    get_ex = (
        ctypes.windll.user32.GetWindowLongPtrW
        if ctypes.sizeof(ctypes.c_void_p) == 8
        else ctypes.windll.user32.GetWindowLongW
    )
    ex_style = int(get_ex(ui.hwnd, GWL_EXSTYLE))
    assert ex_style & WS_EX_NOACTIVATE
    assert ex_style & WS_EX_TOPMOST
    assert ex_style & WS_EX_TOOLWINDOW
    assert ex_style & 0x00080000
    assert ui._last_ulw_ok is True and ui._last_ulw_error == 0

    # Idle bounds (top circle + Send tab, alpha==0 for y>=97)
    assert SEND_TAB_LEFT == 118.0
    assert SEND_TAB_RIGHT == 188.0
    assert SEND_TAB_HEIGHT == 50.0
    assert SEND_ICON_CENTER_X == 166.5
    assert SEND_ICON_RGB == (0, 132, 255)

    idle_frame = ui._render_frame(time.perf_counter())
    ix0, iy0, ix1, iy1 = alpha_bbox(idle_frame, min_alpha=245)
    assert (iy1 - iy0 + 1) == 74
    assert ix0 == 73 and ix1 == 187, f"Unexpected idle x span: {(ix0, ix1)}"
    tab_ys = np.where(idle_frame[:, 166, 3] >= 245)[0]
    tab_h = int(tab_ys.max() - tab_ys.min() + 1)
    assert tab_h == 50, f"Expected Send tab height 50, got {tab_h}"
    icon_roi = idle_frame[40:60, 155:178, :3]
    messenger_blue_px = int(
        np.sum(
            (icon_roi[:, :, 0] == 0)
            & (icon_roi[:, :, 1] == 132)
            & (icon_roi[:, :, 2] == 255)
        )
    )
    assert messenger_blue_px >= 20, f"Expected solid #0084FF pixels, got {messenger_blue_px}"
    idle_bottom_max_a = int(idle_frame[97:, :, 3].max())
    assert idle_bottom_max_a == 0

    # Recording dual-pill: top 74x74 + Send tab + bottom 166x44 with vertical_gap == 10
    saved_state, saved_w, saved_h, saved_bar = (
        ui._state,
        ui._pill_w,
        ui._pill_h,
        ui._bar_vis,
    )
    ui._state = "recording"
    ui._pill_w = TOP_BUTTON_SIZE
    ui._pill_h = TOP_BUTTON_SIZE
    ui._bar_vis = 1.0
    rec_frame = ui._render_frame(time.perf_counter())
    tx0, ty0, tx1, ty1 = alpha_bbox(rec_frame[:92, :147, :], min_alpha=245)
    assert int(rec_frame[:24, 147, 3].max()) < 245 and int(rec_frame[76:92, 147, 3].max()) < 245
    bx0, by0, bx1, by1 = alpha_bbox(rec_frame[92:, :, :], min_alpha=245)
    by0_abs, by1_abs = by0 + 92, by1 + 92
    rec_top_size = (tx1 - tx0 + 1, ty1 - ty0 + 1)
    rec_bot_size = (bx1 - bx0 + 1, by1_abs - by0_abs + 1)
    vertical_gap = by0_abs - ty1 - 1
    ui._state, ui._pill_w, ui._pill_h, ui._bar_vis = (
        saved_state,
        saved_w,
        saved_h,
        saved_bar,
    )
    assert rec_top_size == (74, 74), f"Expected top (74, 74), got {rec_top_size}"
    assert rec_bot_size == (166, 44), f"Expected bot (166, 44), got {rec_bot_size}"
    assert vertical_gap == 10, f"Expected vertical_gap 10, got {vertical_gap}"

    assert ui._is_send_enter_hit(166.5, 50.0) is True
    assert ui._is_send_enter_hit(SEND_ICON_CENTER_X, TOP_CENTER_Y) is True
    assert ui._is_send_enter_hit(TOP_CENTER_X, TOP_CENTER_Y) is False

    popup_ex_style = int(get_ex(ui._quit_popup.hwnd, GWL_EXSTYLE))
    assert (popup_ex_style & 0x08080088) == 0x08080088, f"Unexpected popup ex_style: {hex(popup_ex_style)}"
    assert ctypes.windll.user32.IsWindowVisible(ui._quit_popup.hwnd) == 0

    print(
        f"window & geometry OK: title={ui.root.title()!r} quit_label={QUIT_LABEL!r} "
        f"hwnd={ui.hwnd} ex_style={hex(ex_style)} popup_ex_style={hex(popup_ex_style)} "
        f"ulw=({ui._last_ulw_ok}, {ui._last_ulw_error}) win={(win_w, win_h)} "
        f"idle_box={(ix0, ix1, iy0, iy1)} tab_h={tab_h} blue_px={messenger_blue_px} "
        f"rec_top={rec_top_size} rec_bot={rec_bot_size} gap={vertical_gap}"
    )

    results: dict[str, object] = {
        "win_size": (win_w, win_h),
        "rec_top_size": rec_top_size,
        "rec_bot_size": rec_bot_size,
        "vertical_gap": vertical_gap,
        "cursors": (cursors_before, cursors_during, cursors_after),
    }
    errors: list[BaseException] = []

    def fail_out(exc: BaseException):
        errors.append(exc)
        app.on_quit()
        ui.stop()

    def wait_for(predicate, on_done, timeout=4.0, deadline=None):
        if deadline is None:
            deadline = time.perf_counter() + timeout
        try:
            if predicate():
                on_done()
                return
            if time.perf_counter() >= deadline:
                raise TimeoutError(f"Timed out waiting; ui._state={ui._state!r}")
            ui.root.after(25, lambda: wait_for(predicate, on_done, timeout, deadline))
        except BaseException as exc:
            fail_out(exc)

    # Phase 5A: Test real typer.is_input_active -> AuroraMicApp._track_foreground_tick -> ui.set_input_active
    def step_test_sticky_glow_and_passive_ibeam():
        fake_fg = 0x55551
        fake_ibeam_handle = 0x9999
        uia_mode: dict[str, object] = {"val": None}
        key_state: dict[int, int] = {}
        ibeam_active = {"val": True}

        def fake_get_fg():
            return fake_fg

        def fake_is_win(hwnd):
            return hwnd == fake_fg

        def fake_get_tid(hwnd, pid_ptr):
            return 1234

        def fake_get_gti(tid, gti_ptr):
            return 0  # No Win32 caret

        def fake_get_cls(hwnd, buf, length):
            buf.value = "Chrome_WidgetWin_1"
            return len(buf.value)

        def fake_get_cursor(ci_ptr):
            info = ctypes.cast(ci_ptr, ctypes.POINTER(CURSORINFO)).contents
            info.hCursor = fake_ibeam_handle if ibeam_active["val"] else 0x1111
            return 1

        def fake_async_key(vk):
            return key_state.get(int(vk), 0)

        patches = [
            patch.object(app.typer, "_ibeam_cursor", fake_ibeam_handle),
            patch.object(app.typer._user32, "GetForegroundWindow", side_effect=fake_get_fg),
            patch.object(app.typer._user32, "IsWindow", side_effect=fake_is_win),
            patch.object(app.typer._user32, "GetWindowThreadProcessId", side_effect=fake_get_tid),
            patch.object(app.typer._user32, "GetGUIThreadInfo", side_effect=fake_get_gti),
            patch.object(app.typer._user32, "GetClassNameW", side_effect=fake_get_cls),
            patch.object(app.typer._user32, "GetCursorInfo", side_effect=fake_get_cursor),
            patch.object(app.typer._user32, "GetAsyncKeyState", side_effect=fake_async_key),
            patch.object(app.typer, "_probe_uia_input_state", side_effect=lambda: uia_mode["val"]),
        ]
        for p in patches:
            p.start()

        def stop_patches():
            for p in reversed(patches):
                p.stop()

        try:
            # 1) Passive I-beam hover (no click, no typing, UIA=None) MUST NOT glow!
            app.typer._last_polled_fg_hwnd = None
            app.typer._input_focused_hwnd = None
            app.typer._last_typing_time = 0.0
            ui.set_input_active(False)

            def check_passive_ibeam():
                try:
                    assert ui._input_active is False, "Passive I-beam hover must NOT latch glow"
                    # 2) UIA Edit becomes active -> tracker must turn glow ON
                    uia_mode["val"] = True
                    ui.root.after(240, check_uia_edit_glow)
                except BaseException as exc:
                    stop_patches()
                    fail_out(exc)

            def check_uia_edit_glow():
                try:
                    assert ui._input_active is True, "Expected glow ON for UIA Edit"
                    # 3) Fast click-out (VK_LBUTTON bit 0x0001) + UIA Button (False) within <3s of typing
                    app.typer._last_typing_time = time.monotonic()  # simulate recent typing < 3s ago
                    uia_mode["val"] = False
                    ibeam_active["val"] = False
                    key_state[VK_LBUTTON] = 0x0001
                    ui.root.after(240, check_click_out_clears)
                except BaseException as exc:
                    stop_patches()
                    fail_out(exc)

            def check_click_out_clears():
                try:
                    assert ui._input_active is False, "Click-out must immediately clear glow even <3s after typing"
                    assert app.typer._input_focused_hwnd is None
                    assert app.typer._last_typing_time == 0.0
                    results["sticky_glow_and_passive_ibeam_fix"] = "OK"
                finally:
                    stop_patches()
                step_send_while_idle()

            ui.root.after(240, check_passive_ibeam)
        except BaseException as exc:
            stop_patches()
            fail_out(exc)

    # Phase 5B: Send tab while IDLE -> stays idle, calls send_enter once
    def step_send_while_idle():
        dispatched_batches: list[list[tuple[int, int, int]]] = []

        def fake_send_input(n_inputs, p_inputs, cb_size):
            batch = []
            for i in range(int(n_inputs)):
                inp = p_inputs[i]
                batch.append((int(inp.type), int(inp.ki.wVk), int(inp.ki.dwFlags)))
            dispatched_batches.append(batch)
            return int(n_inputs)

        p1 = patch.object(app.typer, "_focus_window_safely", return_value=None)
        p2 = patch.object(app.typer._user32, "SendInput", side_effect=fake_send_input)
        p1.start()
        p2.start()

        def cleanup():
            p2.stop()
            p1.stop()

        try:
            assert ui._state == "idle"
            ui._on_press(make_mouse_event(166.5, 50.0))
            ui._on_release(make_mouse_event(166.5, 50.0))
            assert ui._state == "idle"

            def after_idle_send():
                try:
                    assert ui._state == "idle"
                    assert len(dispatched_batches) == 1
                    assert dispatched_batches[0] == [
                        (INPUT_KEYBOARD, VK_RETURN, 0),
                        (INPUT_KEYBOARD, VK_RETURN, KEYEVENTF_KEYUP),
                    ]
                    results["send_idle"] = dispatched_batches[0]
                finally:
                    cleanup()
                step_send_while_recording_start()

            wait_for(lambda: len(dispatched_batches) == 1, after_idle_send, timeout=2.0)
        except BaseException as exc:
            cleanup()
            fail_out(exc)

    # Phase 5C: Send tab while RECORDING -> transcribes + types + presses Enter -> success
    def step_send_while_recording_start():
        try:
            ui._on_press(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y))
            ui._on_release(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y))
            assert ui._state == "recording"
            assert app._is_recording is True
            ui.root.after(260, step_send_while_recording_click)
        except BaseException as exc:
            fail_out(exc)

    def step_send_while_recording_click():
        dispatched_batches: list[list[tuple[int, int, int, int]]] = []
        stop_cb_spy = patch.object(app, "on_stop_record", wraps=app.on_stop_record)

        def fake_send_input(n_inputs, p_inputs, cb_size):
            batch = []
            for i in range(int(n_inputs)):
                inp = p_inputs[i]
                batch.append((int(inp.type), int(inp.ki.wVk), int(inp.ki.wScan), int(inp.ki.dwFlags)))
            dispatched_batches.append(batch)
            return int(n_inputs)

        m_stop_cb = stop_cb_spy.start()
        p1 = patch.object(app.transcriber, "transcribe", return_value="Hi")
        p2 = patch.object(app.typer, "_focus_window_safely", return_value=None)
        p3 = patch.object(app.typer._user32, "SendInput", side_effect=fake_send_input)
        p1.start()
        p2.start()
        p3.start()

        def cleanup():
            p3.stop()
            p2.stop()
            p1.stop()
            stop_cb_spy.stop()

        try:
            ui._on_press(make_mouse_event(166.5, 50.0))
            ui._on_release(make_mouse_event(166.5, 50.0))
            assert ui._state == "transcribing"
            assert m_stop_cb.call_count == 0, "Send tab during recording must NOT call on_stop_record"

            def after_rec_send():
                try:
                    assert ui._state == "success"
                    # Text 'Hi' = 4 events, Enter = 2 events
                    flat = [ev for b in dispatched_batches for ev in b]
                    assert len(flat) == 6
                    assert flat[-2:] == [
                        (INPUT_KEYBOARD, VK_RETURN, 0, 0),
                        (INPUT_KEYBOARD, VK_RETURN, 0, KEYEVENTF_KEYUP),
                    ]
                    results["send_while_recording"] = {"total_events": len(flat), "state": ui._state}
                finally:
                    cleanup()
                wait_for(lambda: ui._state == "idle", step_send_recording_empty_start, timeout=1.5)

            wait_for(lambda: ui._state == "success", after_rec_send, timeout=3.0)
        except BaseException as exc:
            cleanup()
            fail_out(exc)

    # Phase 5D: Send tab while RECORDING with blank transcription -> idle, NO Enter
    def step_send_recording_empty_start():
        try:
            ui._on_press(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y))
            ui._on_release(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y))
            assert ui._state == "recording"
            ui.root.after(260, step_send_recording_empty_click)
        except BaseException as exc:
            fail_out(exc)

    def step_send_recording_empty_click():
        sendinput_calls: list[int] = []
        p1 = patch.object(app.transcriber, "transcribe", return_value="   ")
        p2 = patch.object(app.typer._user32, "SendInput", side_effect=lambda n, p, s: sendinput_calls.append(int(n)) or int(n))
        p1.start()
        p2.start()

        def cleanup():
            p2.stop()
            p1.stop()

        try:
            ui._on_press(make_mouse_event(166.5, 50.0))
            ui._on_release(make_mouse_event(166.5, 50.0))
            assert ui._state == "transcribing"

            def after_blank_send():
                try:
                    assert ui._state == "idle"
                    assert len(sendinput_calls) == 0, "Must NOT press Enter on blank transcription"
                    assert app._pending_enter_on_finish is False
                    results["send_recording_blank"] = {"sendinput_calls": 0, "state": ui._state}
                finally:
                    cleanup()
                step_send_while_transcribing_start()

            wait_for(lambda: ui._state == "idle" and not app._transcribe_in_flight, after_blank_send, timeout=3.0)
        except BaseException as exc:
            cleanup()
            fail_out(exc)

    # Phase 5E: Send tab while ALREADY TRANSCRIBING -> queues Enter and sends after typing finishes
    def step_send_while_transcribing_start():
        try:
            ui._on_press(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y))
            ui._on_release(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y))
            assert ui._state == "recording"
            ui.root.after(260, step_send_while_transcribing_run)
        except BaseException as exc:
            fail_out(exc)

    def step_send_while_transcribing_run():
        entered_transcribe = threading.Event()
        release_transcribe = threading.Event()
        dispatched_batches: list[list[tuple[int, int, int, int]]] = []

        def blocking_transcribe(wav):
            entered_transcribe.set()
            release_transcribe.wait(timeout=3.0)
            return "Queued"

        def fake_send_input(n_inputs, p_inputs, cb_size):
            batch = []
            for i in range(int(n_inputs)):
                inp = p_inputs[i]
                batch.append((int(inp.type), int(inp.ki.wVk), int(inp.ki.wScan), int(inp.ki.dwFlags)))
            dispatched_batches.append(batch)
            return int(n_inputs)

        p1 = patch.object(app.transcriber, "transcribe", side_effect=blocking_transcribe)
        p2 = patch.object(app.typer, "_focus_window_safely", return_value=None)
        p3 = patch.object(app.typer._user32, "SendInput", side_effect=fake_send_input)
        p1.start()
        p2.start()
        p3.start()

        def cleanup():
            release_transcribe.set()
            p3.stop()
            p2.stop()
            p1.stop()

        try:
            # Stop via top button (no Enter yet)
            ui._on_press(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y))
            ui._on_release(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y))
            assert ui._state == "transcribing"

            def on_transcribing_entered():
                try:
                    assert app._transcribe_in_flight is True
                    # Click Send tab while already transcribing
                    ui._on_press(make_mouse_event(166.5, 50.0))
                    ui._on_release(make_mouse_event(166.5, 50.0))
                    assert app._pending_enter_on_finish is True
                    release_transcribe.set()

                    def after_queued_send():
                        try:
                            assert ui._state == "success"
                            flat = [ev for b in dispatched_batches for ev in b]
                            # 'Queued' (12 events) + Enter (2 events) = 14 events
                            assert len(flat) == 14
                            assert flat[-2:] == [
                                (INPUT_KEYBOARD, VK_RETURN, 0, 0),
                                (INPUT_KEYBOARD, VK_RETURN, 0, KEYEVENTF_KEYUP),
                            ]
                            results["send_while_transcribing"] = {"total_events": len(flat), "state": ui._state}
                        finally:
                            cleanup()
                        wait_for(lambda: ui._state == "idle", step_case_a_top_stop_start, timeout=1.5)

                    wait_for(lambda: ui._state == "success", after_queued_send, timeout=3.0)
                except BaseException as exc:
                    cleanup()
                    fail_out(exc)

            wait_for(lambda: entered_transcribe.is_set(), on_transcribing_entered, timeout=2.0)
        except BaseException as exc:
            cleanup()
            fail_out(exc)

    # Phase 5F: Normal top Stop -> multilingual UTF-16 + newline (46 events)
    def step_case_a_top_stop_start():
        try:
            ui._on_press(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y))
            ui._on_release(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y))
            ui.root.after(260, step_case_a_top_stop)
        except BaseException as exc:
            fail_out(exc)

    def step_case_a_top_stop():
        sendinput_calls: list[int] = []
        p1 = patch.object(app.transcriber, "transcribe", return_value="Hello 世界 café 🚀\nLine 2")
        p2 = patch.object(app.typer, "_focus_window_safely", return_value=None)
        p3 = patch.object(app.typer._user32, "SendInput", side_effect=lambda n, p, s: sendinput_calls.append(int(n)) or int(n))
        p1.start()
        p2.start()
        p3.start()

        def cleanup():
            p3.stop()
            p2.stop()
            p1.stop()

        try:
            ui._on_press(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y))
            ui._on_release(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y))

            def after_success():
                try:
                    assert ui._state == "success"
                    assert sum(sendinput_calls) == 46
                    results["multilingual_sendinput_events"] = sum(sendinput_calls)
                finally:
                    cleanup()
                wait_for(lambda: ui._state == "idle", step_case_cancel_start, timeout=1.5)

            wait_for(lambda: ui._state == "success", after_success, timeout=3.0)
        except BaseException as exc:
            cleanup()
            fail_out(exc)

    # Phase 5G: X cancel
    def step_case_cancel_start():
        try:
            ui._on_press(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y))
            ui._on_release(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y))
            ui.root.after(260, step_case_cancel_click_x)
        except BaseException as exc:
            fail_out(exc)

    def step_case_cancel_click_x():
        stop_orig = app.recorder.stop
        stop_calls = 0

        def counted_stop():
            nonlocal stop_calls
            stop_calls += 1
            return stop_orig()

        p_stop = patch.object(app.recorder, "stop", side_effect=counted_stop)
        p_trans = patch.object(app.transcriber, "transcribe", return_value="no")
        p_type = patch.object(app.typer, "type_text", return_value=True)
        p_stop.start()
        m_trans = p_trans.start()
        m_type = p_type.start()

        def cleanup():
            p_type.stop()
            p_trans.stop()
            p_stop.stop()

        try:
            ui._on_press(make_mouse_event(172.0, 119.0))
            ui._on_release(make_mouse_event(172.0, 119.0))
            assert ui._state == "idle"

            def after_cancel():
                try:
                    assert stop_calls == 1 and not app.recorder.is_recording
                    assert m_trans.call_count == 0 and m_type.call_count == 0
                    results["cancel_flow"] = {"stop_calls": 1, "state": ui._state}
                finally:
                    cleanup()
                step_case_60s_start()

            wait_for(lambda: stop_calls == 1 and not app.recorder.is_recording, after_cancel, timeout=2.0)
        except BaseException as exc:
            cleanup()
            fail_out(exc)

    # Phase 5H: 60s auto-stop
    def step_case_60s_start():
        try:
            ui._on_press(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y))
            ui._on_release(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y))
            ui.root.after(260, step_case_60s_expire)
        except BaseException as exc:
            fail_out(exc)

    def step_case_60s_expire():
        sendinput_calls: list[int] = []
        p1 = patch.object(app.transcriber, "transcribe", return_value="60s auto stop")
        p2 = patch.object(app.typer, "_focus_window_safely", return_value=None)
        p3 = patch.object(app.typer._user32, "SendInput", side_effect=lambda n, p, s: sendinput_calls.append(int(n)) or int(n))
        m1 = p1.start()
        p2.start()
        p3.start()

        def cleanup():
            p3.stop()
            p2.stop()
            p1.stop()

        try:
            ui._state_entered_at = time.perf_counter() - 60.05

            def after_60s():
                try:
                    assert ui._state == "success"
                    assert m1.call_count == 1 and sum(sendinput_calls) == 26
                    results["auto_60s_flow"] = {"sendinput_events": sum(sendinput_calls), "state": ui._state}
                finally:
                    cleanup()
                step_case_quit_popup()

            wait_for(lambda: ui._state == "success", after_60s, timeout=3.0)
        except BaseException as exc:
            cleanup()
            fail_out(exc)

    # Phase 5I: Custom dark-glass Quit AuroraMic popup (open, outside-click dismiss, inside-click quit)
    def step_case_quit_popup():
        key_state: dict[int, int] = {}
        cur_pos = [500, 500]
        fg_hwnd = [0x1234]

        p_key = patch.object(ui, "_async_key_state", side_effect=lambda vk: key_state.get(int(vk), 0))
        p_pos = patch.object(ui, "_cursor_pos", side_effect=lambda: (cur_pos[0], cur_pos[1]))
        p_fg = patch.object(ui, "_foreground_hwnd", side_effect=lambda: fg_hwnd[0])
        p_key.start()
        p_pos.start()
        p_fg.start()

        def stop_quit_patches():
            p_fg.stop()
            p_pos.stop()
            p_key.stop()

        try:
            popup = ui._quit_popup
            assert popup is not None and ui._quit_open is False

            # Right-click opens dark-glass layered Quit popup (168x58)
            ui._on_right_click(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y, 500, 500))
            assert ui._quit_open is True and popup.visible is True
            assert popup.last_ulw_ok is True and popup.last_ulw_error == 0
            qrect = wintypes.RECT()
            assert ctypes.windll.user32.GetWindowRect(popup.hwnd, ctypes.byref(qrect))
            q_size = (qrect.right - qrect.left, qrect.bottom - qrect.top)
            assert q_size == (QUIT_WIN_W, QUIT_WIN_H) == (168, 58), f"Unexpected quit popup size: {q_size}"

            # Simulated desktop left-click outside the pill dismisses without quitting
            pl, pt, pr, pb = popup.pill_rect_screen()
            assert pl == 500.0 and pt == 506.0, f"Expected quit pill left-aligned below cursor (pl={pl}, pt={pt})"
            sw, sh = ui.root.winfo_screenwidth(), ui.root.winfo_screenheight()
            assert ui._quit_popup_origin(-200, -200) == (0, 0)
            assert ui._quit_popup_origin(sw + 200, sh + 200) == (sw - QUIT_WIN_W, sh - QUIT_WIN_H)
            cur_pos[0], cur_pos[1] = int(pl) - 40, int(pt) - 40
            key_state[VK_LBUTTON] = 0x8001

            def after_outside_dismiss():
                try:
                    assert ui._quit_open is False and popup.visible is False
                    assert app._stopping is False

                    # Re-open via right-click and click inside pill -> cleanly quits
                    key_state.clear()
                    cur_pos[0], cur_pos[1] = 500, 500
                    ui._on_right_click(make_mouse_event(TOP_CENTER_X, TOP_CENTER_Y, 500, 500))
                    assert ui._quit_open is True and popup.visible is True

                    rl, rt, rr, rb = popup.pill_rect_screen()
                    cx = int(round(0.5 * (rl + rr)))
                    cy = int(round(0.5 * (rt + rb)))
                    cur_pos[0], cur_pos[1] = cx, cy
                    results["quit_popup"] = {
                        "size": q_size,
                        "outside_dismissed": True,
                        "ulw": (popup.last_ulw_ok, popup.last_ulw_error),
                    }
                    stop_quit_patches()
                    ui._on_quit_release(make_mouse_event(10, 10, cx, cy))
                    assert ui._quit_open is False
                except BaseException as exc:
                    stop_quit_patches()
                    fail_out(exc)

            wait_for(lambda: not ui._quit_open, after_outside_dismiss, timeout=1.5)
        except BaseException as exc:
            stop_quit_patches()
            fail_out(exc)

    ui.root.after(100, step_test_sticky_glow_and_passive_ibeam)
    app.run()

    if errors:
        raise errors[0]

    assert app._stopping is True
    assert app._tracker_after_id is None
    assert app.recorder.is_recording is False
    assert app._is_recording is False
    assert app._transcribe_in_flight is False

    try:
        quit_saved = json.loads(tmp_state_path.read_text(encoding="utf-8"))
        assert quit_saved == {"x": orig_x, "y": orig_y}, f"Unexpected quit-saved pos: {quit_saved}"

        # b) Creating a second FloatingMicUI with saved drag coordinates restores (x, y) on startup
        restore_path = tmp_state_dir / "restore_state.json"
        restore_path.write_text(json.dumps(drag_saved), encoding="utf-8")
        ui_restored = FloatingMicUI(lambda: None, lambda: None, lambda: 0.0, state_path=restore_path)
        restored_pos = (ui_restored.root.winfo_x(), ui_restored.root.winfo_y())
        vx0, vy0, vx1, vy1 = ui_restored._virtual_screen_bounds()
        ui_restored.stop()
        assert restored_pos == (drag_saved["x"], drag_saved["y"]), f"Expected restored {drag_saved}, got {restored_pos}"

        # c) Out-of-bounds saved coordinates are clamped cleanly inside virtual screen bounds
        oob_hi_path = tmp_state_dir / "oob_hi.json"
        oob_hi_path.write_text(json.dumps({"x": 99999, "y": 99999}), encoding="utf-8")
        ui_hi = FloatingMicUI(lambda: None, lambda: None, lambda: 0.0, state_path=oob_hi_path)
        hi_pos = (ui_hi.root.winfo_x(), ui_hi.root.winfo_y())
        ui_hi.stop()
        assert hi_pos == (vx1 - WINDOW_WIDTH, vy1 - WINDOW_HEIGHT), f"Unexpected high-clamp pos: {hi_pos}"

        oob_lo_path = tmp_state_dir / "oob_lo.json"
        oob_lo_path.write_text(json.dumps({"x": -9999, "y": -9999}), encoding="utf-8")
        ui_lo = FloatingMicUI(lambda: None, lambda: None, lambda: 0.0, state_path=oob_lo_path)
        lo_pos = (ui_lo.root.winfo_x(), ui_lo.root.winfo_y())
        ui_lo.stop()
        assert lo_pos == (vx0, vy0), f"Unexpected low-clamp pos: {lo_pos}"

        # d) Corrupt state.json falls back cleanly to default without raising
        corrupt_path = tmp_state_dir / "corrupt.json"
        corrupt_path.write_text("{corrupt-json", encoding="utf-8")
        ui_corrupt = FloatingMicUI(lambda: None, lambda: None, lambda: 0.0, state_path=corrupt_path)
        corrupt_pos = (ui_corrupt.root.winfo_x(), ui_corrupt.root.winfo_y())
        ui_corrupt.stop()
        assert corrupt_pos == expected_default_pos, f"Expected fallback {expected_default_pos}, got {corrupt_pos}"

        results["position_memory"] = {
            "default": expected_default_pos,
            "drag_saved": drag_saved,
            "restored": restored_pos,
            "clamped_hi": hi_pos,
            "clamped_lo": lo_pos,
            "corrupt_fallback": corrupt_pos,
        }
    finally:
        default_state_patch.stop()
        tmp_state_ctx.cleanup()

    print(f"app integration OK: {results}")
    print("ALL_E2E_CHECKS_PASSED")


def test_auroramic_e2e() -> None:
    """Pytest entry point executing full AuroraMic end-to-end verification."""
    main_verify()


if __name__ == "__main__":
    main_verify()
