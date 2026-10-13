"""自动驾驶（0.6.0）：收到新消息 → 拟稿 → 切会话 → 发送。

"全自动档"真正落地的地方。它只做一件事，但每一步都可能出错，所以规矩是：
**每一步不过就停，并把原因说出来**（不说"没发"就完了，要说清是哪一步没过）。

一次 `step()` 的顺序：
    授权有效？ → 读记录 → 有新消息吗（而且是对方发的）？ → 停手条件？
    → 拟稿 → 切到目标会话（拿到上游给的 pane 哈希）
    → `sender.dispatch()`（判定 → 制动 → 演练或真发带 expect_hash）→ 记账

两条与安全直接相关的设计：

1. **演练不碰屏幕**：dry_run 时连 `switch` 都不调用——演练是给你看"本来会发什么"，
   不该顺手把你正开着的窗口抢到前面去。
2. **真发一定先 switch**：切会话由上游零件完成（它会截 header 图 + 返回 pane 哈希），
   header 图会交回界面——**第一次对某个人真发，你必须亲眼看一下那张图**（见 ADR-0006
   "发错人比晚回消息严重得多"）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import brain as brain_mod
from . import rules
from .pct import Pct, PctError
from .sender import AutoSender, SendOutcome
from .wda import WdaClient


def newest_stamp(messages: List[Dict[str, Any]]) -> int:
    """一批消息里最新的时间戳（拿不到就是 0）。"""
    stamps = []
    for item in messages or []:
        try:
            stamps.append(int(item.get("createTime") or item.get("create_time") or 0))
        except (TypeError, ValueError):
            continue
    return max(stamps) if stamps else 0


def idle_seconds() -> float:
    """距离上一次键鼠活动过了多少秒（拿不到就给一个大数，当作"人在忙"处理）。

    为什么要它：真发送必须把目标窗口抢到前台（Windows 的键盘注入就是这个规矩），
    所以程序一动手，你正在别的窗口打的东西就断了。实测反馈就是这个现象。
    这条闸的意思是：**你正在用电脑的时候，它不许抢**。
    """
    try:
        import ctypes
        from ctypes import wintypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(LASTINPUTINFO)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        elapsed_ms = int(ctypes.windll.kernel32.GetTickCount()) - int(info.dwTime)
        return max(0.0, elapsed_ms / 1000.0)
    except Exception:  # noqa: BLE001
        return 0.0


def user_is_busy(threshold_seconds: float = 5.0) -> bool:
    """用户是不是正在敲键盘/动鼠标。拿不准就当作"在忙"（宁可不发，也别打断人）。"""
    try:
        return idle_seconds() < float(threshold_seconds)
    except Exception:  # noqa: BLE001
        return True


def has_new_incoming(messages: List[Dict[str, Any]], last_seen: int) -> "tuple[bool, str]":
    """有没有"值得接"的新消息：比上次新，而且最后一条不是我发的。

    只看最后一条的原因：如果最后一条是我发的，说明球在对方那边，不该追问。
    """
    if not messages:
        return False, "读不到消息"
    newest = newest_stamp(messages)
    if newest <= int(last_seen or 0):
        return False, "没有新消息"
    tail = messages[-1]
    if bool(tail.get("isSent")):
        return False, "最后一条是我自己发的，等他回"
    body = (tail.get("content") or "").strip()
    if not body and not tail.get("renderType"):
        return False, "最后一条是空消息"
    return True, ""


@dataclass
class AutoStep:
    """一次自动检查的结果。无论做没做事，都要能说清为什么。"""

    acted: bool = False
    draft: str = ""
    outcome: Optional[SendOutcome] = None
    reason: str = ""
    header_image: str = ""
    pane_hash: str = ""
    switched_to: str = ""

    def describe(self) -> str:
        if self.outcome is not None:
            return self.outcome.describe()
        return self.reason or "什么都没做"


class Autopilot:
    """把"盯着 → 接话"这件事跑起来。每个被授权对象一份记忆（上次处理到哪条消息）。"""

    def __init__(
        self,
        cfg: Any,
        wda: Optional[WdaClient] = None,
        pct: Optional[Pct] = None,
        advisor: Any = None,
        sender: Optional[AutoSender] = None,
    ) -> None:
        self.cfg = cfg
        self.wda = wda or WdaClient(port=cfg.backend_port)
        self.pct = pct or Pct()
        self.advisor = advisor
        self.sender = sender
        self._last_seen: Dict[str, int] = {}
        self._last_action_at: Dict[str, float] = {}
        # 这个对象已经自动回过几轮：停手条件里的"轮数上限"要用它（以前一直传 0，等于没有）
        self._turns: Dict[str, int] = {}

    # ── 状态记忆 ────────────────────────────────────────────────────────
    def forget(self, username: str = "") -> None:
        if username:
            self._last_seen.pop(username, None)
            self._last_action_at.pop(username, None)
            self._turns.pop(username, None)
        else:
            self._last_seen.clear()
            self._last_action_at.clear()
            self._turns.clear()

    def turns(self, username: str) -> int:
        return int(self._turns.get(username, 0))

    def seed(self, username: str, messages: List[Dict[str, Any]]) -> None:
        """把"当前已经看到的最后一条"记下来，避免一开授权就把老消息当成新的去回。"""
        self._last_seen[username] = newest_stamp(messages)

    def idle_minutes(self, username: str) -> float:
        last = self._last_action_at.get(username)
        if not last:
            return 0.0
        return (time.time() - last) / 60.0

    # ── 主流程 ──────────────────────────────────────────────────────────
    def step(self, grant: rules.AutoGrant, target: str, force: bool = False) -> AutoStep:
        """检查一次。`force=True` 是不管有没有新消息都跑一遍（界面上的"立刻试一次"）。"""
        active, why = grant.state()
        if not active:
            return AutoStep(reason=why)
        username = grant.username
        name = grant.name or username

        ready, note = self.wda.ensure_backend()
        if not ready:
            return AutoStep(reason="读不到记录：%s" % note)
        try:
            raw = self.wda.messages(username, limit=int(self.cfg.read_limit), order="desc")
        except Exception as exc:  # noqa: BLE001
            return AutoStep(reason="读记录出错：%s" % exc)
        messages = list(reversed(raw))

        fresh, fresh_why = has_new_incoming(messages, self._last_seen.get(username, 0))
        if not fresh and not force:
            return AutoStep(reason=fresh_why)
        self._last_seen[username] = newest_stamp(messages)

        # 停手条件（判定函数在 rules 里，这里只负责喂给它事实）
        incoming = [
            (item.get("content") or "") for item in messages[-6:] if not item.get("isSent")
        ]
        short_count = 0
        for text in reversed(incoming):
            if rules.is_short_reply(text):
                short_count += 1
            else:
                break
        keep_going = rules.stop_reason(
            recent_incoming=incoming,
            turns_done=self.turns(username),
            max_turns=int(getattr(self.cfg, "max_turns", 15)),
            short_replies=short_count,
            stop_after_short_replies=int(getattr(self.cfg, "stop_after_short_replies", 2)),
            silent_minutes=self.idle_minutes(username),
            stop_after_minutes=int(getattr(self.cfg, "stop_after_minutes", 15)),
        )
        if keep_going and not force:
            return AutoStep(reason="停手条件命中：%s" % keep_going)

        # 真发之前再过一道"你正在用电脑吗"的闸（0.6.1 加的护栏）：
        # 键盘注入必须抢前台，所以你正在别的窗口打字时不许动手。
        # 手动点「立刻接一次」（force）不受这条限制——那时候是你自己要求现在发的。
        if not grant.dry_run and not force:
            guard = float(getattr(self.cfg, "auto_idle_guard_seconds", 5) or 0)
            respect = bool(getattr(self.cfg, "auto_respect_busy", True))
            if respect and guard > 0 and user_is_busy(guard):
                return AutoStep(
                    reason="你正在用电脑（%.0f 秒内有键鼠操作），我不抢窗口，等你停手再发"
                    % idle_seconds()
                )

        if self.advisor is None or self.sender is None:
            return AutoStep(reason="自动驾驶没接上大脑或发送器")

        # 拟稿：自动发送用一次性完整版（要"一句话"就够，两段式在这里没有意义）
        transcript = brain_mod.format_transcript(messages, limit=int(self.cfg.read_limit))
        kwargs = {
            "contact_name": name,
            "transcript": transcript,
            "junshi_text": "",
            "style_text": "",
            "contact_profile": "",
            "memory_text": "",
            "red_hits": rules.needs_human_decision(transcript),
        }
        try:
            from . import archive  # 局部导入：只有真要拟稿时才读盘

            kwargs["style_text"] = archive.user_style(1200)
            kwargs["contact_profile"] = archive.contact_profile(name, 1500)
            answer = self.advisor.advise(kwargs)
        except Exception as exc:  # noqa: BLE001
            return AutoStep(reason="拟稿失败：%s" % exc)
        advice = brain_mod.parse_advice(answer.text)
        draft = (advice.first or "").strip() or answer.text.strip()[:60]
        if not draft:
            return AutoStep(reason="拟稿是空的，没东西可发")

        step = AutoStep(acted=True, draft=draft)

        # 切会话：**只有真发才切**（演练不该抢你的屏幕）
        expect_hash = ""
        if not grant.dry_run:
            try:
                info = self.pct.switch(target, name)
            except PctError as exc:
                step.reason = "切不到这个会话：%s" % exc
                return step
            step.switched_to = info.get("switched-to", "")
            step.header_image = info.get("headerImg", "")
            step.pane_hash = info.get("hash", "")
            expect_hash = info.get("hash", "")

        outcome = self.sender.dispatch(
            grant,
            username,
            name,
            draft,
            expect_hash=expect_hash,
            target=target,
        )
        step.outcome = outcome
        step.reason = outcome.reason
        if outcome.sent or outcome.dry_run:
            self._last_action_at[username] = time.time()
        if outcome.sent:
            self._turns[username] = self.turns(username) + 1
        return step
