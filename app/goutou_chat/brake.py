"""紧急制动（ADR-0013）。

两条防线：
1. **制动标志**：任何自动发送之前都必须 `check()`。按下制动后，不光后续不发，
   连"已经拟好但还没发"的内容也一并丢弃。
2. **全局热键**：窗口被别的程序压住时界面按钮点不到（本机有 QQMusic / DSH
   轮流压住微信的历史），热键是唯一可靠的救命手段。默认 `Ctrl+Alt+Esc`。

用 ctypes 直接调 Win32（`RegisterHotKey`），不引入任何新依赖，也不用 AutoHotkey
（本机根本没装）。
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Callable, Optional, Tuple

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

_NAMED_KEYS = {
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "SPACE": 0x20,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "HOME": 0x24,
    "END": 0x23,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
    "BACKSPACE": 0x08,
    "PAUSE": 0x13,
}


class BrakeEngaged(RuntimeError):
    """已按下紧急制动：任何自动发送都必须停。"""


def parse_hotkey(text: str) -> Tuple[int, int]:
    """把 'Ctrl+Alt+Esc' 解析成 (修饰键, 虚拟键码)。"""
    mods = 0
    vk = 0
    for raw in (text or "").replace("+", " ").split():
        part = raw.strip().upper()
        if not part:
            continue
        if part in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif part == "ALT":
            mods |= MOD_ALT
        elif part == "SHIFT":
            mods |= MOD_SHIFT
        elif part in ("WIN", "META", "SUPER"):
            mods |= MOD_WIN
        elif part in _NAMED_KEYS:
            vk = _NAMED_KEYS[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part)
        elif part.startswith("F") and part[1:].isdigit():
            index = int(part[1:])
            if 1 <= index <= 24:
                vk = 0x70 + index - 1
    if not vk:
        raise ValueError("认不出这个热键：%s" % text)
    return mods, vk


class _HotkeyThread(threading.Thread):
    """在独立线程里注册热键并跑消息循环。"""

    def __init__(self, mods: int, vk: int, on_fire: Callable[[], None]) -> None:
        super().__init__(name="goutou-hotkey", daemon=True)
        self.mods = mods
        self.vk = vk
        self.on_fire = on_fire
        self.error: Optional[str] = None
        self.ready = threading.Event()
        # 注意：属性名不能叫 _stop —— 那是 threading.Thread 自己的内部方法，
        # 覆盖它会让 join() 抛 TypeError（实测踩过）。
        self._stop_event = threading.Event()
        self.win32_tid = 0

    def run(self) -> None:  # pragma: no cover - 需要真实桌面环境
        try:
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
        except Exception as exc:  # noqa: BLE001
            self.error = "拿不到 user32：%s" % exc
            self.ready.set()
            return
        # PostThreadMessage 要的是 Win32 线程 id，不是 Python 的 ident
        try:
            self.win32_tid = int(kernel32.GetCurrentThreadId())
        except Exception:  # noqa: BLE001
            self.win32_tid = 0
        if not user32.RegisterHotKey(None, 1, self.mods, self.vk):
            self.error = "注册失败（多半是热键已被别的程序占用）"
            self.ready.set()
            return
        self.ready.set()
        msg = wintypes.MSG()
        while not self._stop_event.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            if msg.message == WM_HOTKEY:
                try:
                    self.on_fire()
                except Exception:  # noqa: BLE001 - 热键回调绝不能把线程弄死
                    pass
        user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        self._stop_event.set()
        if self.win32_tid:
            try:
                ctypes.windll.user32.PostThreadMessageW(self.win32_tid, WM_QUIT, 0, 0)
            except Exception:  # noqa: BLE001
                pass


class Brake:
    """一个能被任何线程安全使用的制动开关。"""

    def __init__(self, hotkey: str = "Ctrl+Alt+Esc", auto_only: bool = True) -> None:
        self.hotkey = hotkey
        self.auto_only = auto_only
        self._lock = threading.Lock()
        self._engaged = False
        self._reason = ""
        self._thread: Optional[_HotkeyThread] = None
        self._hotkey_note = "未注册"
        self.on_change: Optional[Callable[[bool, str], None]] = None

    # ── 状态 ────────────────────────────────────────────────────────────
    @property
    def engaged(self) -> bool:
        with self._lock:
            return self._engaged

    @property
    def reason(self) -> str:
        with self._lock:
            return self._reason

    @property
    def hotkey_note(self) -> str:
        return self._hotkey_note

    def engage(self, reason: str = "手动") -> None:
        with self._lock:
            if self._engaged:
                return
            self._engaged = True
            self._reason = reason
        if self.on_change:
            self.on_change(True, reason)

    def release(self) -> None:
        with self._lock:
            if not self._engaged:
                return
            self._engaged = False
            self._reason = ""
        if self.on_change:
            self.on_change(False, "")

    def check(self, action: str = "发送") -> None:
        """自动动作前调用。已制动就抛错——调用方不许吞掉它。"""
        if self.engaged:
            raise BrakeEngaged("紧急制动已按下（%s），已拦下这次%s" % (self.reason or "手动", action))

    # ── 全局热键 ────────────────────────────────────────────────────────
    def register(self) -> Tuple[bool, str]:
        try:
            mods, vk = parse_hotkey(self.hotkey)
        except ValueError as exc:
            self._hotkey_note = str(exc)
            return False, self._hotkey_note
        thread = _HotkeyThread(mods, vk, lambda: self.engage("全局热键"))
        thread.start()
        thread.ready.wait(timeout=3)
        if thread.error:
            self._hotkey_note = thread.error
            return False, thread.error
        self._thread = thread
        self._hotkey_note = "已注册：%s（仅全自动态生效）" % self.hotkey
        return True, self._hotkey_note

    def unregister(self) -> None:
        if self._thread:
            self._thread.stop()
            # 必须等它把消息循环收干净：线程还活着就退出，进程会在收尾阶段崩掉
            self._thread.join(timeout=3)
            self._thread = None
        self._hotkey_note = "已注销"
