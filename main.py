"""Entry point and orchestration for the AuroraMic floating voice app."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import logging
import sys
import threading
from pathlib import Path
from typing import Callable

_APP_DIR = str(Path(__file__).resolve().parent)
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)

import config
from recorder import AudioRecorder
from transcriber import GroqTranscriber
from typer import TextTyper
from ui import FloatingMicUI

LOG_PATH = Path(__file__).with_name("auroramic.log")
ICO_PATH = Path(__file__).with_name("auroramic.ico")
MUTEX_NAME = "Local\\AuroraMic_SingleInstance"
ERROR_ALREADY_EXISTS = 183

_INSTANCE_MUTEX_HANDLE: int | None = None
_KERNEL32_MUTEX: ctypes.WinDLL | None = None


def setup_logging() -> logging.Logger:
    """Configure file (and console stderr if attached) logging to auroramic.log."""
    logger = logging.getLogger("auroramic")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        formatter = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        )
        file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)
        if sys.stderr is not None:
            stream_handler = logging.StreamHandler(sys.stderr)
            stream_handler.setFormatter(formatter)
            logger.addHandler(stream_handler)
    return logger


def enable_dpi_awareness() -> None:
    """Enable Windows DPI awareness before creating any Tk windows."""
    try:
        res = ctypes.windll.shcore.SetProcessDpiAwareness(1)
        if res != 0:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def _sanitize_error(exc: BaseException) -> str:
    """Format an exception message without ever leaking the API key."""
    msg = f"{type(exc).__name__}: {exc}"
    key = getattr(config, "GROQ_API_KEY", "")
    if key:
        msg = msg.replace(key, "[REDACTED]")
    return msg


class AuroraMicApp:
    """Coordinates FloatingMicUI, AudioRecorder, GroqTranscriber, and TextTyper."""

    def __init__(
        self,
        recorder: AudioRecorder | None = None,
        transcriber: GroqTranscriber | None = None,
        typer: TextTyper | None = None,
        ui_factory: Callable[..., FloatingMicUI] = FloatingMicUI,
        logger: logging.Logger | None = None,
    ) -> None:
        self.logger = logger or setup_logging()
        self.recorder = recorder or AudioRecorder(
            sample_rate=config.SAMPLE_RATE,
            channels=config.CHANNELS,
        )
        self.transcriber = transcriber or GroqTranscriber()
        self.typer = typer or TextTyper()
        self._ui_factory = ui_factory

        self.ui: FloatingMicUI | None = None
        self._ui_hwnd: int | None = None
        self._saved_hwnd: int | None = None
        self._tracker_after_id: str | None = None
        self._stopping = False
        self._state_lock = threading.Lock()
        self._audio_lock = threading.Lock()
        self._record_generation = 0
        self._is_recording = False
        self._transcribe_in_flight = False
        self._pending_enter_on_finish = False

    def _safe_set_state(self, state: str, message: str = "") -> None:
        if self._stopping or self.ui is None:
            return
        try:
            self.ui.set_state(state, message)
        except Exception:
            pass

    def _start_foreground_tracker(self) -> None:
        if self._stopping or self.ui is None or self._tracker_after_id is not None:
            return
        self._schedule_next_track()

    def _schedule_next_track(self) -> None:
        if self._stopping or self.ui is None:
            return
        try:
            self._tracker_after_id = self.ui.root.after(200, self._track_foreground_tick)
        except Exception:
            self._tracker_after_id = None

    def _track_foreground_tick(self) -> None:
        self._tracker_after_id = None
        if self._stopping:
            return
        if self.ui is not None and getattr(self.ui, "is_api_key_open", lambda: False)():
            try:
                self.ui.set_input_active(False)
            except Exception as exc:
                self.logger.debug("Input active tracker error: %s", _sanitize_error(exc))
            self._schedule_next_track()
            return
        try:
            self.typer.remember_foreground_window(exclude_hwnd=self._ui_hwnd)
        except Exception as exc:
            self.logger.debug("Foreground tracker error: %s", _sanitize_error(exc))
        try:
            if self.ui is not None:
                active = self.typer.is_input_active(exclude_hwnd=self._ui_hwnd)
                self.ui.set_input_active(bool(active))
        except Exception as exc:
            self.logger.debug("Input active tracker error: %s", _sanitize_error(exc))
        self._schedule_next_track()

    def on_window_ready(self, hwnd: int) -> None:
        """Apply WS_EX_NOACTIVATE / topmost style and start foreground tracking."""
        self._ui_hwnd = hwnd
        try:
            self.typer.configure_no_activate_window(hwnd)
        except Exception as exc:
            self.logger.error(
                "Failed to configure no-activate window: %s", _sanitize_error(exc)
            )
        if self.ui is not None:
            self._start_foreground_tracker()

    def on_start_record(self) -> None:
        """Capture active target window and begin recording microphone audio."""
        hwnd = (
            self._ui_hwnd
            if self._ui_hwnd is not None
            else getattr(self.ui, "hwnd", None)
        )
        try:
            saved = self.typer.remember_foreground_window(exclude_hwnd=hwnd)
            with self._state_lock:
                self._saved_hwnd = saved
        except Exception as exc:
            self.logger.error(
                "Failed to remember foreground window: %s", _sanitize_error(exc)
            )

        try:
            with self._audio_lock:
                self._record_generation += 1
                self.recorder.start()
            with self._state_lock:
                self._is_recording = True
                self._pending_enter_on_finish = False
        except Exception as exc:
            with self._state_lock:
                self._is_recording = False
            self.logger.error("Failed to start recording: %s", _sanitize_error(exc))
            self._safe_set_state("error", "Mic unavailable")

    def on_stop_record(self) -> None:
        """Stop recording, transcribe via Groq in a daemon thread, and type result."""
        with self._state_lock:
            self._is_recording = False
            self._transcribe_in_flight = True
            self._pending_enter_on_finish = False
            target_hwnd = self._saved_hwnd
            self._saved_hwnd = None

        worker = threading.Thread(
            target=self._transcribe_and_type,
            args=(target_hwnd,),
            daemon=True,
            name="auroramic-transcribe",
        )
        worker.start()

    def _transcribe_and_type(self, target_hwnd: int | None) -> None:
        fallback_message = "Transcription failed"
        try:
            fallback_message = "Audio capture failed"
            with self._audio_lock:
                wav_bytes = self.recorder.stop()
            if not wav_bytes:
                with self._state_lock:
                    self._pending_enter_on_finish = False
                self._safe_set_state("idle")
                return

            fallback_message = "Transcription failed"
            text = self.transcriber.transcribe(wav_bytes)
            if text and text.strip():
                with self._state_lock:
                    should_enter = self._pending_enter_on_finish
                    self._pending_enter_on_finish = False

                fallback_message = "Typing failed"
                self.typer.type_text(
                    text,
                    target_hwnd=target_hwnd,
                    press_enter=should_enter,
                )

                while True:
                    with self._state_lock:
                        extra_enter = self._pending_enter_on_finish
                        self._pending_enter_on_finish = False
                    if not extra_enter:
                        break
                    self.typer.send_enter(target_hwnd=target_hwnd)

                self._safe_set_state("success")
            else:
                with self._state_lock:
                    self._pending_enter_on_finish = False
                self._safe_set_state("idle")
        except Exception as exc:
            with self._state_lock:
                self._pending_enter_on_finish = False
            self.logger.error(
                "Transcription or typing failed: %s", _sanitize_error(exc)
            )
            self._safe_set_state(
                "error", getattr(exc, "user_message", fallback_message)
            )
        finally:
            with self._state_lock:
                self._transcribe_in_flight = False

    def on_cancel_record(self) -> None:
        """Stop recording on a daemon thread and discard audio without transcribing."""
        with self._state_lock:
            self._is_recording = False
            self._pending_enter_on_finish = False
            self._saved_hwnd = None
            generation = self._record_generation

        if getattr(self.ui, "_state", None) != "idle":
            self._safe_set_state("idle")

        worker = threading.Thread(
            target=self._cancel_recording,
            args=(generation,),
            daemon=True,
            name="auroramic-cancel",
        )
        worker.start()

    def _cancel_recording(self, generation: int) -> None:
        try:
            with self._audio_lock:
                if generation == self._record_generation:
                    self.recorder.stop()
        except Exception as exc:
            self.logger.debug(
                "Cancel stop recording failed: %s", _sanitize_error(exc)
            )

    def on_send_enter(self) -> None:
        """Handle Send/Enter button clicks across idle, recording, and transcribing."""
        hwnd = (
            self._ui_hwnd
            if self._ui_hwnd is not None
            else getattr(self.ui, "hwnd", None)
        )
        start_transcribe = False
        target_hwnd: int | None = None

        with self._state_lock:
            recording_active = self._is_recording or bool(
                getattr(self.recorder, "is_recording", False)
            )
            if recording_active:
                self._is_recording = False
                self._transcribe_in_flight = True
                self._pending_enter_on_finish = True
                target_hwnd = self._saved_hwnd
                self._saved_hwnd = None
                start_transcribe = True
            elif self._transcribe_in_flight:
                self._pending_enter_on_finish = True
                return

        if start_transcribe:
            if getattr(self.ui, "_state", None) != "transcribing":
                self._safe_set_state("transcribing")
            worker = threading.Thread(
                target=self._transcribe_and_type,
                args=(target_hwnd,),
                daemon=True,
                name="auroramic-transcribe",
            )
            worker.start()
            return

        try:
            target_hwnd = (
                self.typer.remember_foreground_window(exclude_hwnd=hwnd)
                or self.typer.get_last_foreground_window()
            )
        except Exception:
            target_hwnd = self.typer.get_last_foreground_window()

        worker = threading.Thread(
            target=self._send_enter_worker,
            args=(target_hwnd,),
            daemon=True,
            name="auroramic-send-enter",
        )
        worker.start()

    def _send_enter_worker(self, target_hwnd: int | None) -> None:
        try:
            self.typer.send_enter(target_hwnd=target_hwnd)
        except Exception as exc:
            self.logger.error("Send enter failed: %s", _sanitize_error(exc))

    def has_api_key(self) -> bool:
        """Return True if a non-empty Groq API key is configured in memory."""
        transcriber_key = (getattr(self.transcriber, "api_key", "") or "").strip()
        config_key = (getattr(config, "GROQ_API_KEY", "") or "").strip()
        return bool(transcriber_key or config_key)

    def on_save_api_key(self, raw_key: str) -> None:
        """Persist the Groq API key to .env and hot-reload runtime config and transcriber."""
        saved = config.save_groq_api_key(raw_key)
        self.transcriber.api_key = saved
        self.logger.info("Groq API key updated")

    def on_quit(self) -> None:
        """Stop periodic timers and release recording stream cleanly on exit."""
        self._stopping = True
        if self._tracker_after_id is not None and self.ui is not None:
            try:
                self.ui.root.after_cancel(self._tracker_after_id)
            except Exception:
                pass
            self._tracker_after_id = None

        try:
            with self._audio_lock:
                if getattr(self.recorder, "is_recording", False):
                    self.recorder.stop()
            with self._state_lock:
                self._is_recording = False
                self._pending_enter_on_finish = False
        except Exception as exc:
            self.logger.debug(
                "Cleanup stop recording failed: %s", _sanitize_error(exc)
            )
        self.logger.info("AuroraMic exiting cleanly")

    def create_ui(self) -> FloatingMicUI:
        """Instantiate FloatingMicUI and ensure the foreground tracker starts."""
        self.ui = self._ui_factory(
            on_start_record=self.on_start_record,
            on_stop_record=self.on_stop_record,
            get_audio_level=self.recorder.get_level,
            on_window_ready=self.on_window_ready,
            on_quit=self.on_quit,
            on_cancel_record=self.on_cancel_record,
            on_send_enter=self.on_send_enter,
            has_api_key=self.has_api_key,
            on_save_api_key=self.on_save_api_key,
        )
        if ICO_PATH.is_file():
            try:
                self.ui.root.iconbitmap(str(ICO_PATH))
            except Exception:
                pass
        if self._ui_hwnd is None:
            maybe_hwnd = getattr(self.ui, "hwnd", None)
            if maybe_hwnd:
                self._ui_hwnd = maybe_hwnd
        if self._ui_hwnd is not None and self._tracker_after_id is None:
            self._start_foreground_tracker()
        return self.ui

    def run(self) -> None:
        """Create the floating UI if needed and enter its main event loop."""
        if self.ui is None:
            self.create_ui()
        assert self.ui is not None
        self.ui.run()


def _get_mutex_kernel32() -> ctypes.WinDLL:
    """Return kernel32 WinDLL configured for Win32 named mutex calls."""
    global _KERNEL32_MUTEX
    if _KERNEL32_MUTEX is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        _KERNEL32_MUTEX = kernel32
    return _KERNEL32_MUTEX


def acquire_single_instance_mutex(name: str = MUTEX_NAME) -> bool:
    """Acquire the named Win32 single-instance mutex; return False if already held."""
    global _INSTANCE_MUTEX_HANDLE
    if sys.platform != "win32":
        return True
    try:
        kernel32 = _get_mutex_kernel32()
        ctypes.set_last_error(0)
        handle = kernel32.CreateMutexW(None, True, name)
        last_err = ctypes.get_last_error()
        if not handle:
            return True
        if last_err == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        _INSTANCE_MUTEX_HANDLE = int(handle)
        return True
    except Exception:
        return True


def release_single_instance_mutex() -> None:
    """Release and close the single-instance Win32 mutex handle on exit."""
    global _INSTANCE_MUTEX_HANDLE
    handle = _INSTANCE_MUTEX_HANDLE
    _INSTANCE_MUTEX_HANDLE = None
    if not handle or sys.platform != "win32":
        return
    try:
        kernel32 = _get_mutex_kernel32()
        kernel32.ReleaseMutex(wintypes.HANDLE(handle))
        kernel32.CloseHandle(wintypes.HANDLE(handle))
    except Exception:
        pass


def main() -> None:
    """Application entry point."""
    logger = setup_logging()
    if not acquire_single_instance_mutex():
        logger.info("AuroraMic is already running; exiting duplicate instance.")
        return

    try:
        if not getattr(config, "GROQ_API_KEY", ""):
            logger.error(
                "GROQ_API_KEY is missing. Set GROQ_API_KEY (or GROQ_API) in %s.",
                Path(__file__).with_name(".env"),
            )
        enable_dpi_awareness()
        app = AuroraMicApp(logger=logger)
        app.run()
    finally:
        release_single_instance_mutex()


if __name__ == "__main__":
    main()
