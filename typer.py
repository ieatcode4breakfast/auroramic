"""Win32 window focus management and Unicode text injection via SendInput."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import threading
import time
import uuid

# Pointer-sized unsigned/signed integers matching x86 (4 bytes) and x64 (8 bytes).
ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong
LONG_PTR = ctypes.c_ssize_t

# Win32 constants
GWL_EXSTYLE = -20
WS_EX_TOPMOST = 0x00000008
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020

INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_LBUTTON = 0x01
VK_BACK = 0x08
VK_RETURN = 0x0D
VK_SPACE = 0x20

GUI_CARETBLINKING = 0x00000001
IDC_IBEAM = 32513

COINIT_APARTMENTTHREADED = 0x2
CLSCTX_INPROC_SERVER = 0x1
RPC_E_CHANGED_MODE = 0x80010106

UIA_VALUE_PATTERN_ID = 10002
UIA_TEXT_PATTERN2_ID = 10024
UIA_TEXT_EDIT_PATTERN_ID = 10032
UIA_EDIT_CONTROL_TYPE = 50004

_HIGH_SURROGATE_MIN = 0xD800
_HIGH_SURROGATE_MAX = 0xDBFF
_SENDINPUT_BATCH_SIZE = 256
_TYPING_VKS = (VK_BACK, VK_RETURN, VK_SPACE, *range(0x30, 0x5B))
_EDITABLE_CLASS_TOKENS = (
    "edit",
    "richedit",
    "scintilla",
    "textbox",
    "consolewindowclass",
    "cascadia_hosting_window_class",
)
_UIA_DEFINITELY_NON_TEXT_TYPES = frozenset(
    {
        50000,  # Button
        50001,  # Calendar
        50002,  # CheckBox
        50005,  # Hyperlink / Link
        50006,  # Image
        50007,  # ListItem
        50008,  # List
        50009,  # Menu
        50010,  # MenuBar
        50011,  # MenuItem
        50012,  # ProgressBar
        50013,  # RadioButton
        50014,  # ScrollBar
        50015,  # Slider
        50017,  # StatusBar
        50018,  # Tab
        50019,  # TabItem
        50020,  # Text
        50021,  # ToolBar
        50022,  # ToolTip
        50023,  # Tree
        50024,  # TreeItem
        50027,  # Thumb
        50031,  # SplitButton
        50034,  # Header
        50035,  # HeaderItem
        50037,  # TitleBar
        50038,  # Separator
    }
)
_UIA_NON_EDITABLE_IF_NO_PATTERN_TYPES = frozenset(
    {
        50003,  # ComboBox
        50016,  # Spinner
        50026,  # Group
        50028,  # DataGrid
        50029,  # DataItem
        50030,  # Document
        50036,  # Table
    }
)


class MOUSEINPUT(ctypes.Structure):
    """Win32 MOUSEINPUT structure."""

    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    """Win32 KEYBDINPUT structure."""

    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    """Win32 HARDWAREINPUT structure."""

    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUT_UNION(ctypes.Union):
    """Union backing Win32 INPUT.DUMMYUNIONNAME."""

    _fields_ = [
        ("mi", MOUSEINPUT),
        ("ki", KEYBDINPUT),
        ("hi", HARDWAREINPUT),
    ]


class INPUT(ctypes.Structure):
    """Win32 INPUT structure (40 bytes on 64-bit, 28 bytes on 32-bit)."""

    _anonymous_ = ("_input",)
    _fields_ = [
        ("type", wintypes.DWORD),
        ("_input", _INPUT_UNION),
    ]


class RECT(ctypes.Structure):
    """Win32 RECT structure."""

    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    """Win32 POINT structure."""

    _fields_ = [
        ("x", wintypes.LONG),
        ("y", wintypes.LONG),
    ]


class GUITHREADINFO(ctypes.Structure):
    """Win32 GUITHREADINFO structure (72 bytes on 64-bit)."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", RECT),
    ]


class CURSORINFO(ctypes.Structure):
    """Win32 CURSORINFO structure (24 bytes on 64-bit)."""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hCursor", wintypes.HANDLE),
        ("ptScreenPos", POINT),
    ]


class GUID(ctypes.Structure):
    """Win32 COM GUID structure (16 bytes)."""

    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def _guid_from_str(guid_str: str) -> GUID:
    """Convert a string GUID into a Win32 mixed-endian GUID struct."""
    return GUID.from_buffer_copy(uuid.UUID(guid_str).bytes_le)


_CLSID_CUIAutomation = _guid_from_str("{ff48dba4-60ef-4201-aa87-54103eef594e}")
_IID_IUIAutomation = _guid_from_str("{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}")
_IID_IUIAutomationValuePattern = _guid_from_str(
    "{a94cd8b1-0844-4cd6-9d2d-640537ab39e9}"
)
_IID_IUIAutomationTextPattern2 = _guid_from_str(
    "{506a921a-fcc9-409f-b23b-37eb74106872}"
)
_IID_IUIAutomationTextEditPattern = _guid_from_str(
    "{17e21576-996c-4870-99d9-bff323380c06}"
)


class TextTyper:
    """Manages non-activating overlay window flags and Win32 SendInput typing."""

    def __init__(self) -> None:
        self.INPUT = INPUT
        self.KEYBDINPUT = KEYBDINPUT
        self.MOUSEINPUT = MOUSEINPUT
        self.HARDWAREINPUT = HARDWAREINPUT
        self.RECT = RECT
        self.POINT = POINT
        self.GUITHREADINFO = GUITHREADINFO
        self.CURSORINFO = CURSORINFO

        self._target_hwnd: int | None = None
        self._lock = threading.Lock()

        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32
        self._ole32 = ctypes.windll.ole32
        self._configure_win32_prototypes()

        self._ibeam_cursor = self._user32.LoadCursorW(
            None, ctypes.cast(IDC_IBEAM, wintypes.LPCWSTR)
        )
        self._input_focused_hwnd: int | None = None
        self._last_polled_fg_hwnd: int | None = None
        self._last_typing_time: float = 0.0
        self._uia_local = threading.local()

    def _configure_win32_prototypes(self) -> None:
        """Configure exact ctypes argtypes/restype for 32-bit and 64-bit safety."""
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

        self._user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        self._user32.SetWindowPos.restype = wintypes.BOOL

        self._user32.GetForegroundWindow.argtypes = []
        self._user32.GetForegroundWindow.restype = wintypes.HWND

        self._user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self._user32.SetForegroundWindow.restype = wintypes.BOOL

        self._user32.BringWindowToTop.argtypes = [wintypes.HWND]
        self._user32.BringWindowToTop.restype = wintypes.BOOL

        self._user32.IsWindow.argtypes = [wintypes.HWND]
        self._user32.IsWindow.restype = wintypes.BOOL

        self._user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD

        self._user32.AttachThreadInput.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.BOOL,
        ]
        self._user32.AttachThreadInput.restype = wintypes.BOOL

        self._user32.SendInput.argtypes = [
            wintypes.UINT,
            ctypes.POINTER(INPUT),
            ctypes.c_int,
        ]
        self._user32.SendInput.restype = wintypes.UINT

        self._user32.GetGUIThreadInfo.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(GUITHREADINFO),
        ]
        self._user32.GetGUIThreadInfo.restype = wintypes.BOOL

        self._user32.GetClassNameW.argtypes = [
            wintypes.HWND,
            wintypes.LPWSTR,
            ctypes.c_int,
        ]
        self._user32.GetClassNameW.restype = ctypes.c_int

        self._user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
        self._user32.GetCursorInfo.restype = wintypes.BOOL

        self._user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        self._user32.LoadCursorW.restype = wintypes.HANDLE

        self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._user32.GetAsyncKeyState.restype = ctypes.c_short

        self._kernel32.GetCurrentThreadId.argtypes = []
        self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        self._ole32.CoInitializeEx.argtypes = [wintypes.LPVOID, wintypes.DWORD]
        self._ole32.CoInitializeEx.restype = ctypes.c_long

        self._ole32.CoCreateInstance.argtypes = [
            ctypes.POINTER(GUID),
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(GUID),
            ctypes.POINTER(ctypes.c_void_p),
        ]
        self._ole32.CoCreateInstance.restype = ctypes.c_long

    def configure_no_activate_window(self, hwnd: int) -> None:
        """Apply WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW to hwnd."""
        if not hwnd or not self._user32.IsWindow(hwnd):
            return

        ex_style = int(self._get_window_long(hwnd, GWL_EXSTYLE))
        new_style = ex_style | WS_EX_NOACTIVATE | WS_EX_TOPMOST | WS_EX_TOOLWINDOW
        self._set_window_long(hwnd, GWL_EXSTYLE, new_style)
        self._user32.SetWindowPos(
            hwnd,
            HWND_TOPMOST,
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        )

    def remember_foreground_window(
        self, exclude_hwnd: int | None = None
    ) -> int | None:
        """Record the active foreground HWND unless invalid or excluded."""
        fg = self._user32.GetForegroundWindow()
        with self._lock:
            if fg and self._user32.IsWindow(fg) and fg != exclude_hwnd:
                self._target_hwnd = int(fg)
            return self._target_hwnd

    def get_last_foreground_window(self) -> int | None:
        """Return the most recently remembered non-excluded target HWND."""
        with self._lock:
            return self._target_hwnd

    @staticmethod
    def _com_call(
        ptr: int, slot: int, restype: type, argtypes: list[type], *args: object
    ) -> object:
        """Invoke a COM vtable slot on a raw interface pointer."""
        vtable = ctypes.cast(
            ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))
        ).contents
        fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[slot])
        return fn(ptr, *args)

    def _com_release(self, ptr: int | None) -> None:
        """Deterministically invoke IUnknown::Release on a non-null COM pointer."""
        if ptr:
            try:
                self._com_call(ptr, 2, wintypes.ULONG, [])
            except Exception:
                pass

    def _get_uia(self) -> int | None:
        """Lazily acquire a thread-local IUIAutomation COM interface pointer."""
        uia = getattr(self._uia_local, "uia", None)
        if uia:
            return uia
        if getattr(self._uia_local, "failed", False):
            return None

        try:
            hr_init = int(
                self._ole32.CoInitializeEx(None, COINIT_APARTMENTTHREADED)
            )
            if hr_init < 0 and (hr_init & 0xFFFFFFFF) != RPC_E_CHANGED_MODE:
                self._uia_local.failed = True
                return None

            uia_ptr = ctypes.c_void_p()
            hr = int(
                self._ole32.CoCreateInstance(
                    ctypes.byref(_CLSID_CUIAutomation),
                    None,
                    CLSCTX_INPROC_SERVER,
                    ctypes.byref(_IID_IUIAutomation),
                    ctypes.byref(uia_ptr),
                )
            )
            if hr < 0 or not uia_ptr.value:
                self._uia_local.failed = True
                return None

            self._uia_local.uia = int(uia_ptr.value)
            return self._uia_local.uia
        except Exception:
            self._uia_local.failed = True
            return None

    def _get_current_pattern(
        self, elem: int, pattern_id: int, iid: GUID
    ) -> int | None:
        """Query a UIA pattern interface on elem via IUIAutomationElement::GetCurrentPatternAs."""
        pat_ptr = ctypes.c_void_p()
        hr = int(
            self._com_call(
                elem,
                14,
                ctypes.c_long,
                [
                    ctypes.c_int,
                    ctypes.POINTER(GUID),
                    ctypes.POINTER(ctypes.c_void_p),
                ],
                pattern_id,
                ctypes.byref(iid),
                ctypes.byref(pat_ptr),
            )
        )
        if hr >= 0 and pat_ptr.value:
            return int(pat_ptr.value)
        return None

    def _probe_uia_input_state(self) -> bool | None:
        """Query Windows UI Automation focused element editability.

        Returns True when editable, False when definitively non-editable/disabled,
        or None when UIA is unavailable or indeterminate.
        """
        uia = self._get_uia()
        if not uia:
            return None

        elem_ptr = ctypes.c_void_p()
        try:
            hr = int(
                self._com_call(
                    uia,
                    8,
                    ctypes.c_long,
                    [ctypes.POINTER(ctypes.c_void_p)],
                    ctypes.byref(elem_ptr),
                )
            )
            if hr < 0 or not elem_ptr.value:
                return None

            elem = int(elem_ptr.value)
            is_enabled = wintypes.BOOL(1)
            hr_en = int(
                self._com_call(
                    elem,
                    28,
                    ctypes.c_long,
                    [ctypes.POINTER(wintypes.BOOL)],
                    ctypes.byref(is_enabled),
                )
            )
            if hr_en >= 0 and not is_enabled.value:
                return False

            ctrl_type = ctypes.c_int(0)
            hr_ct = int(
                self._com_call(
                    elem,
                    21,
                    ctypes.c_long,
                    [ctypes.POINTER(ctypes.c_int)],
                    ctypes.byref(ctrl_type),
                )
            )
            ct = int(ctrl_type.value) if hr_ct >= 0 else 0
            if ct in _UIA_DEFINITELY_NON_TEXT_TYPES:
                return False

            has_value_pattern = False
            value_readonly = False
            val_pat = self._get_current_pattern(
                elem, UIA_VALUE_PATTERN_ID, _IID_IUIAutomationValuePattern
            )
            if val_pat:
                has_value_pattern = True
                try:
                    is_ro = wintypes.BOOL(0)
                    hr_ro = int(
                        self._com_call(
                            val_pat,
                            5,
                            ctypes.c_long,
                            [ctypes.POINTER(wintypes.BOOL)],
                            ctypes.byref(is_ro),
                        )
                    )
                    if hr_ro >= 0:
                        value_readonly = bool(is_ro.value)
                finally:
                    self._com_release(val_pat)

            if has_value_pattern and value_readonly:
                return False
            if ct == UIA_EDIT_CONTROL_TYPE:
                return True
            if has_value_pattern and not value_readonly:
                return True

            te_pat = self._get_current_pattern(
                elem, UIA_TEXT_EDIT_PATTERN_ID, _IID_IUIAutomationTextEditPattern
            )
            if te_pat:
                self._com_release(te_pat)
                return True

            tp2_pat = self._get_current_pattern(
                elem, UIA_TEXT_PATTERN2_ID, _IID_IUIAutomationTextPattern2
            )
            if tp2_pat:
                try:
                    caret_active = wintypes.BOOL(0)
                    caret_range = ctypes.c_void_p()
                    hr_cr = int(
                        self._com_call(
                            tp2_pat,
                            10,
                            ctypes.c_long,
                            [
                                ctypes.POINTER(wintypes.BOOL),
                                ctypes.POINTER(ctypes.c_void_p),
                            ],
                            ctypes.byref(caret_active),
                            ctypes.byref(caret_range),
                        )
                    )
                    if caret_range.value:
                        self._com_release(caret_range.value)
                    if hr_cr >= 0 and caret_active.value:
                        return True
                finally:
                    self._com_release(tp2_pat)

            if ct in _UIA_NON_EDITABLE_IF_NO_PATTERN_TYPES:
                return False
            return None
        except Exception:
            return None
        finally:
            if elem_ptr.value:
                self._com_release(elem_ptr.value)

    def is_input_active(self, exclude_hwnd: int | None = None) -> bool:
        """Return True if a text input field or active typing is detected."""
        fg_hwnd = self._user32.GetForegroundWindow()
        if not fg_hwnd or fg_hwnd == exclude_hwnd or not self._user32.IsWindow(fg_hwnd):
            return False

        fg_int = int(fg_hwnd)
        with self._lock:
            if fg_int != self._last_polled_fg_hwnd:
                self._last_polled_fg_hwnd = fg_int
                self._last_typing_time = 0.0
                self._user32.GetAsyncKeyState(VK_LBUTTON)
                for vk in _TYPING_VKS:
                    self._user32.GetAsyncKeyState(vk)

            fg_tid = int(self._user32.GetWindowThreadProcessId(fg_hwnd, None))
            gti = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
            if self._user32.GetGUIThreadInfo(fg_tid, ctypes.byref(gti)):
                if (
                    (gti.flags & GUI_CARETBLINKING)
                    or gti.hwndCaret
                    or gti.rcCaret.right > gti.rcCaret.left
                    or gti.rcCaret.bottom > gti.rcCaret.top
                ):
                    self._input_focused_hwnd = fg_int
                    return True

            class_hwnd = gti.hwndFocus or fg_hwnd
            class_buf = ctypes.create_unicode_buffer(256)
            if self._user32.GetClassNameW(class_hwnd, class_buf, len(class_buf)) > 0:
                class_name = class_buf.value.lower()
                if any(token in class_name for token in _EDITABLE_CLASS_TOKENS):
                    self._input_focused_hwnd = fg_int
                    return True

            uia_state = self._probe_uia_input_state()
            if uia_state is True:
                self._input_focused_hwnd = fg_int
                return True
            if uia_state is False:
                self._input_focused_hwnd = None
                self._last_typing_time = 0.0
                return False

            clicked = bool(self._user32.GetAsyncKeyState(VK_LBUTTON) & 0x8001)
            ci = CURSORINFO(cbSize=ctypes.sizeof(CURSORINFO))
            is_ibeam = bool(
                self._user32.GetCursorInfo(ctypes.byref(ci))
                and self._ibeam_cursor
                and ci.hCursor == self._ibeam_cursor
            )
            if clicked:
                if is_ibeam:
                    self._input_focused_hwnd = fg_int
                    return True
                self._input_focused_hwnd = None
                self._last_typing_time = 0.0
                for vk in _TYPING_VKS:
                    self._user32.GetAsyncKeyState(vk)
                return False

            if any(self._user32.GetAsyncKeyState(vk) & 0x8001 for vk in _TYPING_VKS):
                self._input_focused_hwnd = fg_int
                self._last_typing_time = time.monotonic()
                return True

            if self._input_focused_hwnd == fg_int:
                return True
            if (
                self._last_typing_time > 0.0
                and (time.monotonic() - self._last_typing_time) < 3.0
            ):
                return True
            return False

    def _focus_window_safely(self, hwnd: int) -> None:
        """Bring target hwnd to the foreground if needed without raising."""
        try:
            current_fg = self._user32.GetForegroundWindow()
            if current_fg == hwnd:
                return

            if not self._user32.SetForegroundWindow(hwnd):
                fg_tid = (
                    int(self._user32.GetWindowThreadProcessId(current_fg, None))
                    if current_fg
                    else 0
                )
                cur_tid = int(self._kernel32.GetCurrentThreadId())
                attached = False
                if fg_tid and fg_tid != cur_tid:
                    attached = bool(
                        self._user32.AttachThreadInput(cur_tid, fg_tid, True)
                    )
                try:
                    self._user32.SetForegroundWindow(hwnd)
                    self._user32.BringWindowToTop(hwnd)
                finally:
                    if attached:
                        self._user32.AttachThreadInput(cur_tid, fg_tid, False)

            time.sleep(0.03)
        except Exception:
            pass

    @staticmethod
    def _make_key_input(
        *, w_vk: int = 0, w_scan: int = 0, flags: int = 0
    ) -> INPUT:
        """Construct a single INPUT_KEYBOARD struct."""
        return INPUT(
            type=INPUT_KEYBOARD,
            ki=KEYBDINPUT(
                wVk=w_vk,
                wScan=w_scan,
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            ),
        )

    def build_input_events(self, text: str) -> list[INPUT]:
        """Build Win32 keyboard INPUT events for normalized multilingual text."""
        if not text:
            return []

        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        events: list[INPUT] = []
        parts = normalized.split("\n")

        for idx, chunk in enumerate(parts):
            if chunk:
                utf16_bytes = chunk.encode("utf-16-le")
                for offset in range(0, len(utf16_bytes), 2):
                    code_unit = int.from_bytes(
                        utf16_bytes[offset : offset + 2], "little"
                    )
                    events.append(
                        self._make_key_input(
                            w_scan=code_unit, flags=KEYEVENTF_UNICODE
                        )
                    )
                    events.append(
                        self._make_key_input(
                            w_scan=code_unit,
                            flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                        )
                    )

            if idx < len(parts) - 1:
                events.append(self._make_key_input(w_vk=VK_RETURN, flags=0))
                events.append(
                    self._make_key_input(w_vk=VK_RETURN, flags=KEYEVENTF_KEYUP)
                )

        return events

    def _prepare_target_focus(self, target_hwnd: int | None = None) -> None:
        """Resolve and safely restore focus to the target HWND when valid."""
        hwnd = target_hwnd or self.get_last_foreground_window()
        if hwnd and self._user32.IsWindow(hwnd):
            current_fg = self._user32.GetForegroundWindow()
            if current_fg != hwnd:
                self._focus_window_safely(hwnd)

    def _dispatch_input_events(self, events: list[INPUT]) -> bool:
        """Send INPUT events via Win32 SendInput without splitting surrogate pairs."""
        if not events:
            return False

        cb_size = ctypes.sizeof(INPUT)
        total_sent = 0
        start = 0
        total_events = len(events)

        while start < total_events:
            end = min(start + _SENDINPUT_BATCH_SIZE, total_events)
            # Keep UTF-16 surrogate pairs together across SendInput batches.
            if end < total_events and end - start >= 4:
                last_unit = events[end - 1].ki.wScan
                if (
                    events[end - 1].ki.dwFlags & KEYEVENTF_UNICODE
                    and _HIGH_SURROGATE_MIN <= last_unit <= _HIGH_SURROGATE_MAX
                ):
                    end -= 2

            batch = events[start:end]
            c_array = (INPUT * len(batch))(*batch)
            sent = int(self._user32.SendInput(len(batch), c_array, cb_size))
            total_sent += sent
            if sent == 0:
                break
            start = end

        return total_sent > 0

    def _dispatch_enter_events(self) -> bool:
        """Dispatch VK_RETURN keydown/keyup via SendInput."""
        enter_events = [
            self._make_key_input(w_vk=VK_RETURN, flags=0),
            self._make_key_input(w_vk=VK_RETURN, flags=KEYEVENTF_KEYUP),
        ]
        return self._dispatch_input_events(enter_events)

    def send_enter(self, target_hwnd: int | None = None) -> bool:
        """Focus target_hwnd safely and dispatch VK_RETURN keydown/keyup."""
        self._prepare_target_focus(target_hwnd)
        return self._dispatch_enter_events()

    def type_text(
        self,
        text: str,
        target_hwnd: int | None = None,
        press_enter: bool = False,
    ) -> bool:
        """Type text at the active cursor using Win32 SendInput."""
        if not text:
            if press_enter:
                return self.send_enter(target_hwnd=target_hwnd)
            return False

        self._prepare_target_focus(target_hwnd)
        events = self.build_input_events(text)
        if not events:
            return False

        dispatched = self._dispatch_input_events(events)
        if dispatched and press_enter:
            self._dispatch_enter_events()
        return dispatched
