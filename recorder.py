from __future__ import annotations

import ctypes
from ctypes import wintypes
import io
import sys
import threading
import wave
from typing import Any

import numpy as np

try:
    import sounddevice as sd
except Exception:
    sd = None

_MIN_DURATION_SECONDS = 0.15
_SAMPLE_WIDTH_BYTES = 2  # 16-bit PCM

# Windows winmm constants for fallback waveIn capture
_WAVE_MAPPER = 0xFFFFFFFF
_WAVE_FORMAT_PCM = 1
_CALLBACK_EVENT = 0x00050000
_WHDR_DONE = 0x00000001
_WAIT_OBJECT_0 = 0x00000000
_WINMM_NUM_BUFFERS = 4
_WINMM_BUFFER_SECONDS = 0.04


class _WAVEFORMATEX(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


class _WAVEHDR(ctypes.Structure):
    _fields_ = [
        ("lpData", ctypes.POINTER(ctypes.c_char)),
        ("dwBufferLength", wintypes.DWORD),
        ("dwBytesRecorded", wintypes.DWORD),
        ("dwUser", ctypes.c_size_t),
        ("dwFlags", wintypes.DWORD),
        ("dwLoops", wintypes.DWORD),
        ("lpNext", ctypes.c_void_p),
        ("reserved", ctypes.c_size_t),
    ]


def _load_winmm_apis() -> tuple[Any, Any]:
    """Load isolated kernel32 and winmm WinDLL bindings with explicit 64-bit argtypes."""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    winmm = ctypes.WinDLL("winmm", use_last_error=True)

    kernel32.CreateEventW.argtypes = [
        ctypes.c_void_p,
        wintypes.BOOL,
        wintypes.BOOL,
        wintypes.LPCWSTR,
    ]
    kernel32.CreateEventW.restype = wintypes.HANDLE

    kernel32.SetEvent.argtypes = [wintypes.HANDLE]
    kernel32.SetEvent.restype = wintypes.BOOL

    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    kernel32.WaitForMultipleObjects.argtypes = [
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.BOOL,
        wintypes.DWORD,
    ]
    kernel32.WaitForMultipleObjects.restype = wintypes.DWORD

    winmm.waveInOpen.argtypes = [
        ctypes.POINTER(wintypes.HANDLE),
        wintypes.UINT,
        ctypes.POINTER(_WAVEFORMATEX),
        ctypes.c_size_t,
        ctypes.c_size_t,
        wintypes.DWORD,
    ]
    winmm.waveInOpen.restype = wintypes.UINT

    winmm.waveInPrepareHeader.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_WAVEHDR),
        wintypes.UINT,
    ]
    winmm.waveInPrepareHeader.restype = wintypes.UINT

    winmm.waveInUnprepareHeader.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_WAVEHDR),
        wintypes.UINT,
    ]
    winmm.waveInUnprepareHeader.restype = wintypes.UINT

    winmm.waveInAddBuffer.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_WAVEHDR),
        wintypes.UINT,
    ]
    winmm.waveInAddBuffer.restype = wintypes.UINT

    winmm.waveInStart.argtypes = [wintypes.HANDLE]
    winmm.waveInStart.restype = wintypes.UINT

    winmm.waveInStop.argtypes = [wintypes.HANDLE]
    winmm.waveInStop.restype = wintypes.UINT

    winmm.waveInReset.argtypes = [wintypes.HANDLE]
    winmm.waveInReset.restype = wintypes.UINT

    winmm.waveInClose.argtypes = [wintypes.HANDLE]
    winmm.waveInClose.restype = wintypes.UINT

    return kernel32, winmm


def _compute_normalized_rms(samples_int16: np.ndarray) -> float:
    """Compute a speech-calibrated RMS level clamped to [0.0, 1.0]."""
    if samples_int16.size == 0:
        return 0.0
    float_samples = samples_int16.astype(np.float32) * (1.0 / 32768.0)
    rms = float(np.sqrt(np.mean(float_samples * float_samples)))
    if rms <= 0.002:
        return 0.0
    scaled = (rms * 5.0) ** 0.75
    return max(0.0, min(1.0, scaled))


class AudioRecorder:
    """Thread-safe 16-bit PCM microphone recorder with live RMS level tracking."""

    def __init__(self, sample_rate: int = 16000, channels: int = 1) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if channels <= 0:
            raise ValueError("channels must be positive")

        self.sample_rate = int(sample_rate)
        self.channels = int(channels)

        self._lock = threading.Lock()
        self._recording = False
        self._chunks: list[bytes] = []
        self._level = 0.0

        self._stream: Any = None
        self._winmm_thread: threading.Thread | None = None
        self._winmm_stop_event: Any = None
        self._winmm_accepting = False

    @property
    def is_recording(self) -> bool:
        with self._lock:
            return self._recording

    def start(self) -> None:
        """Clear buffers and begin capturing 16-bit PCM audio in the background."""
        self._stop_active_backend()

        with self._lock:
            self._chunks.clear()
            self._level = 0.0
            self._recording = True
            self._winmm_accepting = False

        sd_error: Exception | None = None
        if sd is not None:
            stream: Any = None
            try:
                stream = sd.InputStream(
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype="int16",
                    callback=self._sd_callback,
                )
                stream.start()
                with self._lock:
                    self._stream = stream
                return
            except Exception as exc:
                sd_error = exc
                self._close_stream_quietly(stream)

        if sys.platform == "win32":
            try:
                self._start_winmm()
                return
            except Exception as winmm_exc:
                with self._lock:
                    self._recording = False
                    self._level = 0.0
                    self._winmm_accepting = False
                raise RuntimeError(
                    f"Failed to start audio capture (sounddevice: {sd_error}; winmm: {winmm_exc})"
                ) from winmm_exc

        with self._lock:
            self._recording = False
            self._level = 0.0
        raise RuntimeError(f"Failed to start audio capture: {sd_error}")

    def stop(self) -> bytes:
        """Stop recording and return complete WAV bytes, or b'' if too short/empty."""
        self._stop_active_backend()

        with self._lock:
            pcm_data = b"".join(self._chunks)
            self._chunks.clear()
            self._level = 0.0
            self._recording = False

        return self._encode_wav(pcm_data)

    def get_level(self) -> float:
        """Return smoothed normalized audio RMS level in [0.0, 1.0] (0.0 when idle)."""
        with self._lock:
            if not self._recording:
                return 0.0
            return self._level

    def _sd_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info: Any,
        status: Any,
    ) -> None:
        if indata is None or indata.size == 0:
            return
        pcm_bytes = np.ascontiguousarray(indata, dtype=np.int16).tobytes()
        self._record_pcm_chunk(pcm_bytes, indata, require_recording=True)

    def _start_winmm(self) -> None:
        kernel32, winmm = _load_winmm_apis()

        frame_bytes = self.channels * _SAMPLE_WIDTH_BYTES
        frames_per_buf = max(256, int(self.sample_rate * _WINMM_BUFFER_SECONDS))
        buf_bytes = frames_per_buf * frame_bytes

        wfx = _WAVEFORMATEX(
            wFormatTag=_WAVE_FORMAT_PCM,
            nChannels=self.channels,
            nSamplesPerSec=self.sample_rate,
            nAvgBytesPerSec=self.sample_rate * frame_bytes,
            nBlockAlign=frame_bytes,
            wBitsPerSample=16,
            cbSize=0,
        )

        audio_event = kernel32.CreateEventW(None, False, False, None)
        stop_event = kernel32.CreateEventW(None, True, False, None)
        if not audio_event or not stop_event:
            if audio_event:
                kernel32.CloseHandle(audio_event)
            if stop_event:
                kernel32.CloseHandle(stop_event)
            raise RuntimeError("CreateEventW failed for winmm fallback")

        h_wave_in = wintypes.HANDLE()
        hdrs: list[_WAVEHDR] = []
        bufs: list[ctypes.Array[ctypes.c_char]] = []
        opened = False

        try:
            err = winmm.waveInOpen(
                ctypes.byref(h_wave_in),
                _WAVE_MAPPER,
                ctypes.byref(wfx),
                ctypes.c_size_t(audio_event),
                0,
                _CALLBACK_EVENT,
            )
            if err != 0:
                raise RuntimeError(f"waveInOpen failed with MMSYSERR {err}")
            opened = True

            for _ in range(_WINMM_NUM_BUFFERS):
                buf = (ctypes.c_char * buf_bytes)()
                hdr = _WAVEHDR()
                hdr.lpData = ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))
                hdr.dwBufferLength = buf_bytes
                err = winmm.waveInPrepareHeader(
                    h_wave_in, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR)
                )
                if err != 0:
                    raise RuntimeError(f"waveInPrepareHeader failed with MMSYSERR {err}")
                hdrs.append(hdr)
                bufs.append(buf)

                err = winmm.waveInAddBuffer(
                    h_wave_in, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR)
                )
                if err != 0:
                    raise RuntimeError(f"waveInAddBuffer failed with MMSYSERR {err}")

            err = winmm.waveInStart(h_wave_in)
            if err != 0:
                raise RuntimeError(f"waveInStart failed with MMSYSERR {err}")
        except Exception:
            if opened:
                winmm.waveInReset(h_wave_in)
                for hdr in hdrs:
                    winmm.waveInUnprepareHeader(
                        h_wave_in, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR)
                    )
                winmm.waveInClose(h_wave_in)
            kernel32.CloseHandle(audio_event)
            kernel32.CloseHandle(stop_event)
            raise

        with self._lock:
            self._winmm_stop_event = stop_event
            self._winmm_accepting = True

        worker = threading.Thread(
            target=self._winmm_loop,
            args=(kernel32, winmm, h_wave_in, audio_event, stop_event, bufs, hdrs),
            name="winmm-audio-recorder",
            daemon=True,
        )
        with self._lock:
            self._winmm_thread = worker
        worker.start()

    def _winmm_loop(
        self,
        kernel32: Any,
        winmm: Any,
        h_wave_in: wintypes.HANDLE,
        audio_event: wintypes.HANDLE,
        stop_event: wintypes.HANDLE,
        bufs: list[ctypes.Array[ctypes.c_char]],
        hdrs: list[_WAVEHDR],
    ) -> None:
        # Keep `bufs` referenced until after waveInUnprepareHeader/waveInClose.
        _keep_alive = bufs
        handles = (wintypes.HANDLE * 2)(audio_event, stop_event)
        try:
            while True:
                wait_res = kernel32.WaitForMultipleObjects(2, handles, False, 100)
                stopping = wait_res == (_WAIT_OBJECT_0 + 1)

                for hdr in hdrs:
                    if hdr.dwFlags & _WHDR_DONE:
                        if hdr.dwBytesRecorded > 0:
                            pcm_bytes = ctypes.string_at(hdr.lpData, hdr.dwBytesRecorded)
                            self._ingest_winmm_chunk(pcm_bytes)
                        hdr.dwBytesRecorded = 0
                        hdr.dwFlags &= ~_WHDR_DONE
                        if not stopping:
                            winmm.waveInAddBuffer(
                                h_wave_in, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR)
                            )

                if stopping:
                    break
        finally:
            winmm.waveInStop(h_wave_in)
            winmm.waveInReset(h_wave_in)
            for hdr in hdrs:
                if (hdr.dwFlags & _WHDR_DONE) and hdr.dwBytesRecorded > 0:
                    pcm_bytes = ctypes.string_at(hdr.lpData, hdr.dwBytesRecorded)
                    self._ingest_winmm_chunk(pcm_bytes)
                winmm.waveInUnprepareHeader(
                    h_wave_in, ctypes.byref(hdr), ctypes.sizeof(_WAVEHDR)
                )
            winmm.waveInClose(h_wave_in)
            kernel32.CloseHandle(audio_event)
            kernel32.CloseHandle(stop_event)

    def _ingest_winmm_chunk(self, pcm_bytes: bytes) -> None:
        if not pcm_bytes:
            return
        usable = len(pcm_bytes) - (len(pcm_bytes) % _SAMPLE_WIDTH_BYTES)
        if usable <= 0:
            return
        trimmed = pcm_bytes[:usable]
        samples = np.frombuffer(trimmed, dtype=np.int16)
        self._record_pcm_chunk(trimmed, samples, require_recording=False)

    def _record_pcm_chunk(
        self,
        pcm_bytes: bytes,
        samples_int16: np.ndarray,
        *,
        require_recording: bool,
    ) -> None:
        target_level = _compute_normalized_rms(samples_int16)
        with self._lock:
            if require_recording and not self._recording:
                return
            if not require_recording and not self._winmm_accepting:
                return
            self._chunks.append(pcm_bytes)
            if self._recording:
                alpha = 0.45 if target_level > self._level else 0.22
                self._level = max(
                    0.0, min(1.0, alpha * target_level + (1.0 - alpha) * self._level)
                )

    def _stop_active_backend(self) -> None:
        with self._lock:
            self._recording = False
            self._level = 0.0
            stream = self._stream
            self._stream = None
            winmm_stop_event = self._winmm_stop_event
            winmm_thread = self._winmm_thread
            self._winmm_stop_event = None
            self._winmm_thread = None

        self._close_stream_quietly(stream)

        if winmm_stop_event is not None:
            kernel32, _ = _load_winmm_apis()
            kernel32.SetEvent(winmm_stop_event)
        if winmm_thread is not None and winmm_thread.is_alive():
            winmm_thread.join(timeout=2.0)

        with self._lock:
            self._winmm_accepting = False

    @staticmethod
    def _close_stream_quietly(stream: Any) -> None:
        if stream is None:
            return
        try:
            stream.stop()
        except Exception:
            pass
        try:
            stream.close()
        except Exception:
            pass

    def _encode_wav(self, pcm_data: bytes) -> bytes:
        frame_bytes = self.channels * _SAMPLE_WIDTH_BYTES
        num_frames = len(pcm_data) // frame_bytes
        if num_frames <= 0:
            return b""
        duration = num_frames / float(self.sample_rate)
        if duration < _MIN_DURATION_SECONDS:
            return b""

        aligned_pcm = pcm_data[: num_frames * frame_bytes]
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(_SAMPLE_WIDTH_BYTES)
            wf.setframerate(self.sample_rate)
            wf.writeframes(aligned_pcm)
        return buf.getvalue()
