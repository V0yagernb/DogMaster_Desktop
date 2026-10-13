"""自动发送的编排（0.5.0）。

一条消息从"拟好"到"真的进键盘"，中间必须过这几关，**顺序不能变**：

    auto_send_decision()  →  brake.check()  →  （演练？到此为止）
                                              ↓ 否
                                        pct.send(expect_hash=…)
                                              ↓
                                    记账（扣额度 / 单次档失效）

设计上的三条硬要求：

1. **演练模式**：走完全部判定，只差"真的按键盘"，把"本来会发什么"写给人看。
   没有它，测试就等于真的去骚扰别人。
2. **上下文校验**：`pct.send(expect_hash=…)` 带着"发送前那一刻对话的指纹"，
   对话一变上游就拒发（exit 4）。这时**宁可什么都不发，也绝不重发一句已经过期的话**。
3. **发送前再过一次制动**：ADR-0013 的检查点。窗口被别的程序压住时界面点不到，
   那一刻唯一能救场的就是热键——所以进键盘之前必须重新问一次。

真发消息是**唯一**的写动作，所以这个文件里的每个分支都要能被单独测。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from . import rules
from .brake import Brake, BrakeEngaged
from .pct import Pct, PctError, StaleSendError


@dataclass
class SendOutcome:
    """一次自动发送的结果（无论发没发，都要能说清为什么）。"""

    allowed: bool = False          # 判定放行了吗
    sent: bool = False             # 真的发出去了吗
    dry_run: bool = False          # 这是演练（没真发）
    text: str = ""
    reason: str = ""
    target: str = ""
    details: Dict[str, str] = field(default_factory=dict)

    def describe(self) -> str:
        if self.sent:
            return "已发出：%s" % self.text
        if self.dry_run:
            return "演练（没有真发）：%s" % self.text
        return "没发：%s" % (self.reason or "未知原因")


class AutoSender:
    """把"判定 → 制动 → 发送"串起来。持有 brake 与 pct 各一份。"""

    def __init__(self, cfg: Any, brake: Optional[Brake] = None, pct: Optional[Pct] = None) -> None:
        self.cfg = cfg
        self.brake = brake or Brake()
        self.pct = pct or Pct()

    def dispatch(
        self,
        grant: Optional[rules.AutoGrant],
        username: str,
        name: str,
        text: str,
        expect_hash: str = "",
        target: str = "",
    ) -> SendOutcome:
        """试着自动发一条。任何一步不过，都返回"没发 + 原因"。"""
        content = (text or "").strip()
        if not content:
            return SendOutcome(text=text, reason="没有内容可发")

        allowed, why = rules.auto_send_decision(
            grant, username, name, content, brake_engaged=self.brake.engaged
        )
        if not allowed:
            return SendOutcome(text=content, reason=why)

        # 进键盘之前再过一次制动（ADR-0013）：热键按下之后，这一条也不许再发
        try:
            self.brake.check("自动发送")
        except BrakeEngaged as exc:
            return SendOutcome(text=content, reason=str(exc))

        if grant is not None and grant.dry_run:
            return SendOutcome(
                allowed=True,
                sent=False,
                dry_run=True,
                text=content,
                reason="演练模式：本来会发这一句，没有真的发出去",
            )

        if not target:
            return SendOutcome(
                allowed=True,
                sent=False,
                text=content,
                reason="没有可用的发送目标（先跑一次体检，确认上游零件与窗口都在）",
            )

        try:
            info = self.pct.send(target, content, expect_hash=expect_hash)
        except StaleSendError as exc:
            # 上下文过期：上游的保护生效了。这一条不算发出去，也绝不重发旧话。
            return SendOutcome(
                allowed=True,
                sent=False,
                text=content,
                target=target,
                reason="对话已经变了，上游拒发（exit 4）——没发出去，也不重发旧话",
                details={"exit_code": "4", "error": str(exc)},
            )
        except PctError as exc:
            return SendOutcome(
                allowed=True,
                sent=False,
                text=content,
                target=target,
                reason="发送失败：%s" % exc,
                details={"exit_code": str(getattr(exc, "exit_code", ""))},
            )
        return SendOutcome(
            allowed=True, sent=True, text=content, target=target, details=dict(info or {})
        )


def consume(grant: rules.AutoGrant) -> rules.AutoGrant:
    """发出去了之后记账，返回更新后的授权。

    - 单次档：**发完即失效**（arming 也一起撤掉，等于回到建议档）；
    - 常驻/限时：扣一条（remaining = -1 表示不限）。
    """
    updated = rules.AutoGrant.from_dict(grant.to_dict())
    if updated.remaining > 0:
        updated.remaining -= 1
    if updated.mode == rules.Mode.AUTO_ONCE.value:
        updated.remaining = 0
        updated.armed = False
        updated.mode = rules.Mode.ADVICE.value
    return updated
