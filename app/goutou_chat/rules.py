"""档位、红线与停手条件。

这里是**硬规矩**：写在代码里，配置文件改不动。
理由见 docs/决策记录.md：
- ADR-0002：只有建议档与全自动档，全自动档分单次/常驻/限时三个子档。
- ADR-0003：启动权永远在本人手里，后台盯未读绝不自动发送。
- ADR-0004：白名单制，群聊永不进全自动。
- ADR-0012：安全与法律 > 发送红线 > 军师推进建议 > 一般闲聊策略。
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class Mode(str, Enum):
    """档位。建议档只出草稿；其余三个子档都会真的把消息发出去。"""

    ADVICE = "advice"
    AUTO_ONCE = "auto_once"
    AUTO_STANDING = "auto_standing"
    AUTO_TIMED = "auto_timed"

    @property
    def label(self) -> str:
        return MODE_LABELS[self]

    @property
    def is_auto(self) -> bool:
        return self is not Mode.ADVICE


MODE_LABELS = {
    Mode.ADVICE: "建议档（只给草稿，你自己发）",
    Mode.AUTO_ONCE: "全自动 · 单次（发完即失效）",
    Mode.AUTO_STANDING: "全自动 · 常驻（持续到你叫停）",
    Mode.AUTO_TIMED: "全自动 · 限时（到点自动收回）",
}

# 群会话在微信本地库里的用户名后缀（ADR-0004）
GROUP_SUFFIX = "@chatroom"

# 优先级顺序，只用于给使用者看与排障（ADR-0012）
PRIORITY = ["安全与法律", "发送红线", "军师推进建议", "一般闲聊策略"]

# 命中即"不许自动发、必须先问本人"的话题（Q20 (b)）
HARD_STOP_TOPICS: List[str] = [
    # 钱
    "借钱", "还钱", "转账", "红包", "多少钱", "付款", "付钱", "付一下", "买单",
    "请客", "报销", "工资", "贷款", "银行卡", "支付宝", "微信支付",
    # 健康
    "医院", "看病", "吃药", "手术", "生病", "发烧", "体检", "心理咨询",
    # 出行与时间承诺
    "机票", "车票", "几点到", "几点回", "订票", "改签", "接机", "送我", "来接",
    # 承诺与背书
    "保证", "一定", "答应我", "帮我做", "帮我写", "替我", "担保", "签字",
    # 关系定性（军师自己要求：这类要本人拍板）
    "表白", "在一起", "分手", "复合", "喜欢你",
]

# 只允许文本：永远不发图片与表情包（使用者自己定下的规矩）
FORBIDDEN_ACTIONS: List[str] = [
    "发送图片", "发送表情包", "点击表情面板", "发送文件", "发送语音",
]

# 缩短回复的判定阈值（用于"对方明显冷淡就停手"）
SHORT_REPLY_MAX_CHARS = 3
LAUGH_ONLY = {"哈哈", "哈哈哈", "嗯", "嗯嗯", "哦", "哦哦", "好", "好的", "行", "收到", "?"}


# ── 判定函数 ────────────────────────────────────────────────────────────
def is_group(username: str) -> bool:
    """群会话？群一律不进全自动（ADR-0004）。"""
    return bool(username) and username.endswith(GROUP_SUFFIX)


def advice_allowed(username: str, mark: "Optional[Dict[str, Any]]" = None) -> "tuple[bool, str]":
    """军师能不能给这个会话出主意？

    判定顺序（0.4.0 起）：
    1. **使用者显式设过开关 → 永远听使用者的**（聊天区顶部那个「军师参与」勾选框）；
    2. 没设过：群聊不给（ADR-0020）；标成「工作」的不给；
    3. 其余都给。

    为什么群聊要单独一条：群里人多、语境无法验证，发错一句就是公开事故；
    而且群聊的聊天记录也不该被送去第三方模型分析。
    """
    info = mark or {}
    explicit = info.get("advise")
    if explicit is True:
        return True, ""
    if explicit is False:
        return False, "这个会话被你设成「军师不参与」了（在聊天区顶部可以改回来）。"
    if is_group(username):
        return False, "群聊不给建议：群里人多、语境无法验证，记录也不送去分析。"
    if str(info.get("tag") or "") == "work":
        return False, "这个会话标成了「工作」——狗头军师管恋爱和关系，不管工作。"
    return True, ""


def needs_human_decision(text: str) -> List[str]:
    """返回命中的红线词。非空表示这件事必须本人拍板，程序不许自动发。"""
    body = text or ""
    return [word for word in HARD_STOP_TOPICS if word in body]


def is_short_reply(text: str) -> bool:
    """对方是不是只回了一两个字（冷淡信号）。"""
    body = (text or "").strip()
    if not body:
        return True
    return len(body) <= SHORT_REPLY_MAX_CHARS or body in LAUGH_ONLY


def mode_from_label(label: str) -> Optional[Mode]:
    for mode, text in MODE_LABELS.items():
        if label == text or label.startswith(mode.value):
            return mode
    return None


def may_auto_send(
    mode: Mode,
    contact_name: str,
    username: str,
    whitelist_standing: List[str],
    whitelist_timed: List[str],
    never_auto_groups: bool = True,
) -> "tuple[bool, str]":
    """程序能不能替这个人自动发消息？返回（能不能，原因）。

    这是所有自动发送动作的唯一入口，任何绕过它的发送都算 bug。
    """
    if mode is Mode.ADVICE:
        return False, "当前是建议档，只出草稿"
    if never_auto_groups and is_group(username):
        return False, "群聊永不进全自动"
    if contact_name in whitelist_standing:
        return True, "常驻白名单"
    if contact_name in whitelist_timed:
        if mode is Mode.AUTO_ONCE:
            return True, "限时白名单 + 单次授权"
        return True, "限时白名单"
    if mode is Mode.AUTO_ONCE:
        return True, "本次点名单次授权"
    return False, "不在白名单里，常驻档不给授权"


# ── 全自动授权：状态机与所有闸门（0.5.0） ──────────────────────────────
# 这是整个项目最危险的一段代码，所以规矩写在这里、写死在代码里：
# 1. 没有"武装过"的授权 → 不发（避免手滑一下就把它交出去）；
# 2. 群聊 → 永远不发（ADR-0004，哪怕显式授权也不发）；
# 3. 命中硬红线词（钱、承诺、见面）→ 不发，降级成草稿（ADR-0012）；
# 4. 紧急制动按下 → 不发；
# 5. 授权过期 / 额度用完 → 不发。
@dataclass
class AutoGrant:
    """一次全自动授权。存在 config.json 的 auto_grant 里。

    字段刻意都用朴素类型（str/int/float/bool），这样以后想加字段、想手改配置文件
    都不会因为格式变化而读不出来。
    """

    mode: str = Mode.ADVICE.value
    username: str = ""          # 被授权的会话（用 username：会话改名不影响）
    name: str = ""              # 显示名，只用来写在界面上
    created_at: float = 0.0
    expires_at: float = 0.0     # 0 = 不按时间过期（常驻档）
    remaining: int = 0          # 还能自动发几条；-1 = 不限
    armed: bool = False         # 是否走完了"勾选 + 输确认词"的仪式
    dry_run: bool = True        # 演练：跑完整链路但不真按键盘
    trigger: str = "on_reply"   # on_reply = 收到新消息就接；after_me = 我开聊后才接

    # ── 状态 ────────────────────────────────────────────────────────────
    def is_auto(self) -> bool:
        return self.mode in {
            Mode.AUTO_ONCE.value,
            Mode.AUTO_STANDING.value,
            Mode.AUTO_TIMED.value,
        }

    def state(self, now: Optional[float] = None) -> "tuple[bool, str]":
        """这张授权现在能不能用。"""
        stamp = float(now if now is not None else time.time())
        if not self.armed:
            return False, "授权没走完确认（没勾选或没输确认词）"
        if not self.is_auto():
            return False, "当前是建议档"
        if self.expires_at and stamp >= float(self.expires_at):
            return False, "授权到点了，已经自动收回"
        if self.remaining == 0:
            return False, "这次授权的额度用完了"
        return True, ""

    def left_seconds(self, now: Optional[float] = None) -> int:
        """还剩多少秒。**向上取整**：剩 0.6 秒要显示"还剩 1 秒"，不能显示"还剩 0 秒"。"""
        if not self.expires_at:
            return -1
        stamp = float(now if now is not None else time.time())
        remaining = float(self.expires_at) - stamp
        if remaining <= 0:
            return 0
        return int(math.ceil(remaining))

    def relative_expiry(self) -> str:
        if not self.expires_at:
            return "不按时间过期"
        left = self.left_seconds()
        if left <= 0:
            return "已过期"
        if left < 60:
            return "还剩 %d 秒" % left
        return "还剩 %d 分钟" % (left // 60)

    def describe(self) -> str:
        quota = "不限条数" if self.remaining < 0 else "还能自动发 %d 条" % self.remaining
        target = self.name or self.username or "（没指定对象）"
        return "%s｜%s｜%s" % (target, self.relative_expiry(), quota)

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "AutoGrant":
        """容错读取：字段缺了、类型不对都不许炸，退成安全值。"""
        data = raw if isinstance(raw, dict) else {}
        grant = cls()
        for key in ("mode", "username", "name", "trigger"):
            value = data.get(key)
            if isinstance(value, str):
                setattr(grant, key, value)
        for key in ("created_at", "expires_at"):
            try:
                setattr(grant, key, float(data.get(key) or 0.0))
            except (TypeError, ValueError):
                setattr(grant, key, 0.0)
        try:
            grant.remaining = int(data.get("remaining") or 0)
        except (TypeError, ValueError):
            grant.remaining = 0
        grant.armed = bool(data.get("armed", False))
        # 演练开关默认是**开**：读不清楚就当演练，宁可不发
        grant.dry_run = bool(data.get("dry_run", True))
        return grant

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode,
            "username": self.username,
            "name": self.name,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "remaining": self.remaining,
            "armed": self.armed,
            "dry_run": self.dry_run,
            "trigger": self.trigger,
        }


def auto_send_decision(
    grant: Optional[AutoGrant],
    username: str,
    name: str,
    text: str,
    *,
    brake_engaged: bool = False,
    now: Optional[float] = None,
) -> "tuple[bool, str]":
    """要自动发一条之前，所有闸门一次问完。返回（能不能发，原因）。

    这是自动发送的**唯一入口**：任何绕过它的发送都算 bug（与 `may_auto_send()` 同级）。
    顺序刻意从"最不可能被绕过"的排到"最可能变化的"：制动 → 授权 → 群聊 → 额度 → 红线。
    """
    if brake_engaged:
        return False, "紧急制动已按下，全部停下"
    if grant is None:
        return False, "没有授权，只能给建议"
    active, why = grant.state(now)
    if not active:
        return False, why
    if grant.username and username != grant.username:
        return False, "这张授权只对「%s」有效" % (grant.name or grant.username)
    if is_group(username):
        return False, "群聊永不进全自动（ADR-0004），哪怕手动授权也不发"
    hits = needs_human_decision(text)
    if hits:
        return False, "要发的这句话里有必须你本人拍板的事（%s），已降级成草稿" % "、".join(hits)
    return True, ""


def stop_reason(
    recent_incoming: List[str],
    turns_done: int,
    max_turns: int,
    short_replies: int,
    stop_after_short_replies: int,
    silent_minutes: float,
    stop_after_minutes: int,
) -> str:
    """停手条件（ADR-0002 的 Q20）：该停下了就返回原因，否则空串。

    判定函数早就写在这了，0.5.0 才真正接进循环。
    """
    if max_turns and turns_done >= max_turns:
        return "已经自动回了 %d 轮，到上限了" % turns_done
    if stop_after_short_replies and short_replies >= stop_after_short_replies:
        return "对方连着回了 %d 次很短的回复，多半不想聊了" % short_replies
    if stop_after_minutes and silent_minutes >= stop_after_minutes:
        return "已经 %d 分钟没有实际动作，按静默退出收回" % int(silent_minutes)
    if recent_incoming and all(is_short_reply(item) for item in recent_incoming[-stop_after_short_replies:]):
        return "对方这几次都没什么实质内容，先停一下"
    return ""
