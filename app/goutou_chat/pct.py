"""上游零件调用：窗口探测、切会话、发送消息。

只调 `vendor-frozen\\` 里的冻结副本（ADR-0010），不依赖另一个工作区的文件在不在。

必须知道的副作用（上游脚本的行为，不是我们加的）：
- `send` 会抢前台、并把文本写进剪贴板；
- `shot` / `switch` 开头会取消最小化（会动到你的屏幕）；
- `send` 在检测到上下文过期时返回 exit 4 并**拒绝发送**——这是我们最想要的那道闸。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config as cfgmod

EXIT_MISSING_TARGET = 2
EXIT_NO_FOREGROUND = 3
EXIT_STALE = 4


class PctError(RuntimeError):
    def __init__(self, message: str, exit_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.exit_code = exit_code


class StaleSendError(PctError):
    """上下文过期（exit 4）：上游拒绝发送。这不是错误，是保护。"""


class Pct:
    def __init__(
        self,
        script: Optional[Path] = None,
        pwsh: str = "pwsh",
        timeout: int = 180,
    ) -> None:
        self.script = Path(script) if script else cfgmod.pct_script()
        self.pwsh = pwsh
        self.timeout = timeout

    # ── 基础 ────────────────────────────────────────────────────────────
    def available(self) -> Tuple[bool, str]:
        if not self.script.exists():
            return False, "冻结副本不存在：%s" % self.script
        return True, "上游零件就位"

    def _run(self, args: List[str], timeout: Optional[int] = None) -> Tuple[int, str]:
        cmd = [
            self.pwsh,
            "-NoLogo",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script),
        ] + args
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                timeout=timeout or self.timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise PctError("上游零件超时：%s" % exc) from exc
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace").strip()
        text = out if not err else out + "\n" + err
        return proc.returncode, text

    @staticmethod
    def parse_kv(text: str) -> Dict[str, str]:
        """解析上游脚本输出的 key=value 行。"""
        result: Dict[str, str] = {}
        for line in (text or "").splitlines():
            line = line.strip()
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key and " " not in key:
                result[key] = value.strip()
        return result

    # ── 探测类（不截图、不动屏幕） ───────────────────────────────────────
    def list_windows(self, include_hidden: bool = False) -> List[Dict[str, str]]:
        args = ["-Action", "list"]
        if include_hidden:
            args.append("-IncludeHidden")
        code, text = self._run(args, timeout=60)
        if code != 0:
            raise PctError("列窗口失败（exit %d）：%s" % (code, text.strip()), code)
        rows: List[Dict[str, str]] = []
        header = ["hwnd", "pid", "proc", "class", "min", "size", "title"]
        for line in text.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) < 7:
                continue
            if parts[0].lower() == "hwnd":
                continue
            rows.append(dict(zip(header, parts[:7])))
        return rows

    def status(self, target: str) -> Dict[str, str]:
        code, text = self._run(["-Action", "status", "-Target", target], timeout=60)
        if code != 0:
            raise PctError("目标不可用（exit %d）：%s" % (code, text.strip()), code)
        return self.parse_kv(text)

    def targets(self) -> str:
        code, text = self._run(["-Action", "targets"], timeout=60)
        if code != 0:
            raise PctError("列出 profile 失败（exit %d）" % code, code)
        return text

    # ── 会动屏幕的动作 ──────────────────────────────────────────────────
    def shot(self, target: str, region: str) -> Dict[str, str]:
        """截区域图。注意：会取消目标窗口的最小化。"""
        code, text = self._run(["-Action", "shot", "-Target", target, "-Region", region])
        if code != 0:
            raise PctError("截图失败（exit %d）：%s" % (code, text.strip()), code)
        return self.parse_kv(text)

    def switch(self, target: str, to: str) -> Dict[str, str]:
        code, text = self._run(["-Action", "switch", "-Target", target, "-To", to])
        if code != 0:
            raise PctError("切会话失败（exit %d）：%s" % (code, text.strip()), code)
        return self.parse_kv(text)

    def send(
        self,
        target: str,
        text: str,
        expect_hash: Optional[str] = None,
        timeout: int = 150,
    ) -> Dict[str, str]:
        """发送一条文本。

        强烈建议永远带上 expect_hash：上下文一变，上游会拒发（exit 4），
        宁可按一次不动，也不要发出一句已经过期的话。
        """
        args = ["-Action", "send", "-Target", target, "-Text", text]
        if expect_hash:
            args += ["-ExpectHash", expect_hash]
        code, out = self._run(args, timeout=timeout)
        if code == EXIT_STALE:
            raise StaleSendError("上下文已变，上游拒绝发送（exit 4）", code)
        if code != 0:
            raise PctError("发送失败（exit %d）：%s" % (code, out.strip()), code)
        kv = self.parse_kv(out)
        if kv.get("sent", "").lower() == "false":
            raise PctError("上游报告未发出：%s" % out.strip())
        return kv
