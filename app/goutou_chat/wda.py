"""读取后端（本地解密库）的 HTTP 客户端。

要点（见 docs/决策记录.md）：
- ADR-0011：读消息走 HTTP 直连 127.0.0.1:10392，不经过 wc.ps1。
- ADR-0015：后端起不来不等于微信不在；调用方必须处理"后端没起"这一常态。

安全红线：`/api/get_image_key` 与 `/api/cdn/*` 会把账号与配置 POST 到第三方
（c3o.re），本客户端在代码层直接封死这两个路径，任何调用都抛错。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

from . import config as cfgmod

BANNED_PATH_PARTS = ("/api/get_image_key", "/api/cdn")

SETTINGS_FILE = (
    Path(os.environ.get("APPDATA", ""))
    / "wechat-data-analysis-desktop"
    / "desktop-settings.json"
)


class WdaError(RuntimeError):
    """读取后端相关的错误。"""


class WdaClient:
    """只读客户端。写动作（解密、建索引）不在这里做，避免误触发。"""

    def __init__(
        self,
        port: Optional[int] = None,
        auto_start: Optional[bool] = None,
        wait_seconds: Optional[int] = None,
    ) -> None:
        cfg = cfgmod.Config.load()
        self.port = int(port or cfg.backend_port)
        self.auto_start = cfg.backend_auto_start if auto_start is None else auto_start
        self.wait_seconds = int(wait_seconds or cfg.backend_wait_seconds)
        self._session = requests.Session()

    # ── 基础 ────────────────────────────────────────────────────────────
    @property
    def base(self) -> str:
        return "http://127.0.0.1:%d" % self.port

    def get(self, path: str, params: Optional[Dict[str, Any]] = None, timeout: float = 15.0) -> Any:
        for bad in BANNED_PATH_PARTS:
            if bad in path:
                raise WdaError("这个接口会把账号信息外传，已在本程序里封死：%s" % path)
        url = self.base + path
        try:
            resp = self._session.get(url, params=params, timeout=timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise WdaError("请求失败 %s：%s" % (url, exc)) from exc
        # 必须显式按 UTF-8 解码，否则中文会乱码（实测踩过）
        resp.encoding = "utf-8"
        try:
            return resp.json()
        except ValueError:
            return resp.text

    def health(self, timeout: float = 4.0) -> Optional[Dict[str, Any]]:
        """后端活着就返回健康信息，否则 None（不抛错，体检要用）。"""
        try:
            data = self.get("/api/health", timeout=timeout)
        except WdaError:
            return None
        return data if isinstance(data, dict) else {"raw": data}

    def is_up(self) -> bool:
        return self.health() is not None

    # ── 端口发现与启动 ───────────────────────────────────────────────────
    def discover_port(self) -> int:
        """三种发现方式：环境变量 > 配置文件 > 默认值（ADR-0011）。"""
        env = os.environ.get("WECHAT_TOOL_PORT")
        if env:
            try:
                return int(env)
            except ValueError:
                pass
        try:
            if SETTINGS_FILE.exists():
                raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
                port = raw.get("backendPort")
                if port:
                    return int(port)
        except Exception:
            pass
        return cfgmod.DEFAULT_BACKEND_PORT

    def start_backend(self) -> Tuple[bool, str]:
        """按需启动读取后端（ADR：允许自动启动，但要如实告知）。"""
        exe = cfgmod.WDA_EXE
        if not exe.exists():
            return False, "找不到读取后端程序：%s" % exe
        try:
            subprocess.Popen(
                [str(exe)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess, "DETACHED_PROCESS", 0),
            )
        except OSError as exc:
            return False, "启动失败：%s" % exc
        return True, "已启动读取后端，等它就绪"

    def ensure_backend(self) -> Tuple[bool, str]:
        """确保后端可用。返回（可用吗，说明）。"""
        if self.is_up():
            return True, "读取后端在跑"
        if not self.auto_start:
            return False, "读取后端没在跑，且配置里禁止自动启动"
        ok, message = self.start_backend()
        if not ok:
            return False, message
        deadline = time.time() + self.wait_seconds
        while time.time() < deadline:
            if self.is_up():
                return True, "读取后端已就绪（刚由本程序拉起）"
            time.sleep(3)
        return False, "等了 %d 秒后端仍没就绪" % self.wait_seconds

    # ── 只读数据 ────────────────────────────────────────────────────────
    def accounts(self, limit: int = 20) -> List[Dict[str, Any]]:
        data = self.get("/api/chat/accounts", params={"limit": limit})
        return _as_list(data)

    def sessions(self, limit: int = 300) -> List[Dict[str, Any]]:
        data = self.get("/api/chat/sessions", params={"limit": limit})
        return _as_list(data)

    def messages(self, username: str, limit: int = 30, order: str = "desc") -> List[Dict[str, Any]]:
        data = self.get(
            "/api/chat/messages",
            params={"username": username, "limit": limit, "order": order},
        )
        return _as_list(data)

    def contacts(self, limit: int = 2000, keyword: Optional[str] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"limit": limit}
        if keyword:
            params["keyword"] = keyword
        data = self.get("/api/chat/contacts", params=params)
        return _as_list(data)

    def find_session(self, name: str) -> Optional[Dict[str, Any]]:
        """按名字找会话：先精确，再包含。找不到返回 None（不猜）。"""
        want = (name or "").strip()
        if not want:
            return None
        items = self.sessions()
        for item in items:
            if (item.get("name") or "").strip() == want:
                return item
        for item in items:
            if want in (item.get("name") or ""):
                return item
        return None


def _as_list(data: Any) -> List[Dict[str, Any]]:
    """后端有时直接给数组，有时包一层。两种都接住。"""
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("data", "items", "list", "sessions", "messages", "contacts"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []
