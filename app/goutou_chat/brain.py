"""大脑通路：把「判断 + 拟稿」交给本机 DSH（ADR-0007）。

走 `node <DSH>/lib/bin.js headless -`，任务从 **stdin** 送进去——这样中文、
引号、长文本都不会被命令行解析弄坏。实测一次调用约 3 秒。

不采用"复用 3080 并自签 cookie"那条路：那需要读本机凭据库的签名密钥，
等于把主会话钥匙交给一个会自动发消息的程序，代价不该付。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import config as cfgmod


class BrainError(RuntimeError):
    pass


def format_transcript(messages: Sequence[Dict[str, Any]], limit: int = 20) -> str:
    """把结构化消息排成人能读的对话。createTime 是 Unix 秒。"""
    rows: List[str] = []
    items = list(messages)[-limit:]
    for msg in items:
        ts = msg.get("createTime") or msg.get("create_time") or 0
        try:
            when = datetime.fromtimestamp(int(ts)).strftime("%m-%d %H:%M")
        except Exception:
            when = "--"
        who = "我" if msg.get("isSent") else (msg.get("senderDisplayName") or "对方")
        body = (msg.get("content") or "").replace("\n", " ").strip()
        if not body:
            body = "[%s]" % (msg.get("renderType") or "非文本")
        rows.append("%s %s：%s" % (when, who, body))
    return "\n".join(rows)


def build_advice_prompt(
    contact_name: str,
    transcript: str,
    junshi_text: str = "",
    style_text: str = "",
    contact_profile: str = "",
    memory_text: str = "",
    red_hits: Optional[List[str]] = None,
    goal: str = "",
) -> str:
    """拼出一次「军师分析 + 拟稿」的任务书。

    这里写死的规矩与代码里的 rules.py 一致：不编造事实、不替本人承诺、
    不发图片表情包、对方明确拒绝就停。
    """
    parts: List[str] = []
    parts.append(
        "你是「狗头军师」，替本人分析一段聊天并给出可直接发送的回复。\n"
        "硬规矩：\n"
        "1. 只根据下面给出的对话与背景判断，**不许编造**本人没说过的事实（在哪、吃了啥、见了谁、几点回）。\n"
        "2. **不许替本人承诺**钱、时间、出行、见面；这类事只能给「我先问一下」「回头说」这种留余地的表达。\n"
        "3. 只输出文本，**不许**使用 emoji 或表情包。（本人立过规矩：会发表情包的一律是他本人。）\n"
        "4. 对方明确拒绝、表示不想聊、或明显冷淡时，不许硬推。\n"
        "5. 先看事实再看感受：把「已发生的行为」与「你的推测」分开写。"
    )
    if junshi_text:
        parts.append("── 军师知识（原文节选，按需参考）──\n" + junshi_text)
    if style_text:
        parts.append("── 本人说话风格（照这个来）──\n" + style_text)
    if contact_profile:
        parts.append("── 这个人的档案 ──\n" + contact_profile)
    if memory_text:
        parts.append("── 长期记忆（稳定事实）──\n" + memory_text)
    parts.append("── 对象 ──\n%s" % contact_name)
    if goal:
        parts.append("── 本次目标 ──\n%s" % goal)
    parts.append("── 最近对话（早→晚）──\n" + (transcript or "（没有可读记录）"))
    if red_hits:
        parts.append(
            "── 注意 ──\n对方的话里出现了这些敏感词：%s。"
            "凡涉及这些，回复必须是留余地的话，不能替本人定下任何事。" % "、".join(red_hits)
        )
    parts.append(
        "── 输出格式（严格照这个写，不要额外解释）──\n"
        "【判断】2-4 句：现在是什么局面、对方的信号是什么、最要紧的一件事。\n"
        "【首选】一条可以直接复制发送的话（不超过 30 字，符合本人说话风格）。\n"
        "【备选】最多两条，标明各自适用于什么情况。\n"
        "【风险】1-2 句：这条发出去可能有什么代价；什么时候该停手。"
    )
    return "\n\n".join(parts)


# ── 两段式（0.3.1）：先极速给一句能发的话，再补完整分析 ─────────────────
# 实测（2026-10-13，智谱 glm-5.3-flash + reasoning_effort=low）：
#   完整任务书 10223 字符 → 首字 3.0 秒、总 6.6 秒；
#   极速任务书  3349 字符 → 首字 1.35 秒、总 5.0 秒。
# 首字延迟几乎完全由**输入长度**决定，所以第一段要瘦到只剩必需品。
_HARD_RULES = (
    "硬规矩：\n"
    "1. 只根据下面给出的内容判断，**不许编造**本人没说过的事实。\n"
    "2. **不许替本人承诺**钱、时间、出行、见面；这类只能给留余地的话。\n"
    "3. 只输出文本，**不许**用 emoji 或表情包。\n"
    "4. 对方明确拒绝或明显冷淡时，不许硬推。\n"
    "5. 照本人说话风格写，不要写成客套话。"
)


def build_quick_prompt(
    contact_name: str,
    transcript: str,
    style_text: str = "",
    contact_profile: str = "",
    red_hits: Optional[List[str]] = None,
) -> str:
    """极速版任务书：只要【判断】和【首选】。

    刻意**不带**军师知识（那一块 6651 字符，是首字延迟的大头）。方法论让位给速度：
    先给人一句能立刻发的话，完整分析由上一步的补充请求接着做。
    """
    parts: List[str] = [
        "你是「狗头军师」，替本人看一段聊天，然后给一句**现在就能发出去**的话。\n" + _HARD_RULES
    ]
    if style_text:
        parts.append("── 本人说话风格（照这个来）──\n" + style_text)
    if contact_profile:
        parts.append("── 这个人是谁 ──\n" + contact_profile)
    parts.append("── 对象 ──\n%s" % contact_name)
    parts.append("── 最近对话（早→晚）──\n" + (transcript or "（没有可读记录）"))
    if red_hits:
        parts.append(
            "── 注意 ──\n对方话里有这些敏感词：%s。回复必须留余地，不能替本人定下任何事。"
            % "、".join(red_hits)
        )
    parts.append(
        "── 输出格式（严格照这个写，不要额外解释，不要写备选和风险）──\n"
        "【判断】2-3 句：现在是什么局面、最要紧的一件事。\n"
        "【首选】一条可以直接复制发送的话（不超过 30 字）。"
    )
    return "\n\n".join(parts)


def build_followup_prompt(
    contact_name: str,
    transcript: str,
    junshi_text: str = "",
    style_text: str = "",
    contact_profile: str = "",
    red_hits: Optional[List[str]] = None,
    first_choice: str = "",
) -> str:
    """补充版任务书：只要【备选】和【风险】（判断与首选上面已经给过了）。

    这里才带上军师知识——它慢，但这一步是在人已经拿到首选之后跑的，等得起。
    """
    parts: List[str] = [
        "你是「狗头军师」。本人已经拿到一条可直接发送的话，现在要的是**退路与风险**。\n"
        + _HARD_RULES
    ]
    if junshi_text:
        parts.append("── 军师知识（原文节选，按需参考）──\n" + junshi_text)
    if style_text:
        parts.append("── 本人说话风格 ──\n" + style_text)
    if contact_profile:
        parts.append("── 这个人的档案 ──\n" + contact_profile)
    parts.append("── 对象 ──\n%s" % contact_name)
    parts.append("── 最近对话（早→晚）──\n" + (transcript or "（没有可读记录）"))
    if first_choice:
        parts.append("── 已经给出的首选（不要重复它）──\n" + first_choice)
    if red_hits:
        parts.append("── 注意 ──\n对方话里有这些敏感词：%s。" % "、".join(red_hits))
    parts.append(
        "── 输出格式（严格照这个写，不要重复判断和首选）──\n"
        "【备选】最多两条，各标明适用什么情况。\n"
        "【风险】1-2 句：这条发出去可能有什么代价；什么时候该停手。"
    )
    return "\n\n".join(parts)


class DshBrain:
    """把任务丢给本机 DSH，拿回文本答案。"""

    def __init__(
        self,
        bin_path: Optional[Path] = None,
        cwd: Optional[Path] = None,
        model: str = "",
        timeout: int = 240,
        node: str = "node",
    ) -> None:
        self.bin_path = Path(bin_path) if bin_path else cfgmod.DSH_BIN
        self.cwd = Path(cwd) if cwd else cfgmod.project_home()
        self.model = model
        self.timeout = timeout
        self.node = node

    def available(self) -> "tuple[bool, str]":
        if not self.bin_path.exists():
            return False, "找不到 DSH CLI：%s" % self.bin_path
        return True, "DSH CLI 就位"

    def _base_cmd(self) -> List[str]:
        """拼兜底通道的命令行。

        注意：**headless 不认 `--model`**（实测 `headless --help` 只有 `--json`、
        `--session-id`、`-h` 三个选项）。以前这里会在 model 非空时拼上 `--model`，
        一旦有人填了值命令就会失败——当前配置里是空串，所以一直没炸。
        现在模型选择交给 DSH 自己的 profile；本项目要指定模型就走直连通道（llm.py）。
        """
        return [self.node, str(self.bin_path), "headless"]

    def ask(self, prompt: str, timeout: Optional[int] = None) -> str:
        """问一次。默认走 stdin（`-`），失败再退回命令行参数。"""
        if not prompt.strip():
            raise BrainError("任务为空")
        limit = timeout or self.timeout

        cmd = self._base_cmd() + ["-"]
        try:
            proc = subprocess.run(
                cmd,
                input=prompt.encode("utf-8"),
                capture_output=True,
                cwd=str(self.cwd),
                timeout=limit,
            )
            answer = proc.stdout.decode("utf-8", "replace").strip()
            if proc.returncode == 0 and answer:
                return answer
        except subprocess.TimeoutExpired as exc:
            raise BrainError("DSH 超时（%d 秒）" % limit) from exc
        except OSError as exc:
            raise BrainError("起不了 DSH 进程：%s" % exc) from exc

        # 退回：把任务当参数传（老版本 CLI 不吃 stdin 时用）
        try:
            proc = subprocess.run(
                self._base_cmd() + [prompt],
                capture_output=True,
                cwd=str(self.cwd),
                timeout=limit,
            )
        except Exception as exc:  # noqa: BLE001 - 兜底要如实报出来
            raise BrainError("DSH 调用失败：%s" % exc) from exc
        answer = proc.stdout.decode("utf-8", "replace").strip()
        if not answer:
            err = proc.stderr.decode("utf-8", "replace").strip()
            raise BrainError("DSH 没返回内容（exit %d）：%s" % (proc.returncode, err[:500]))
        return answer

    def advice(self, **kwargs: Any) -> str:
        return self.ask(build_advice_prompt(**kwargs))


# ── 把军师输出拆成四块（界面要用） ──────────────────────────────────────
# 与 build_advice_prompt() 里的输出格式一一对应；改格式要两处一起改。
SECTION_LABELS = [
    ("【判断】", "judge"),
    ("【首选】", "first"),
    ("【备选】", "options"),
    ("【风险】", "risk"),
]

# 备选行前面常见的列举符号，展示时去掉
_BULLET_PREFIXES = ("-", "－", "•", "*", "·")


@dataclass
class Advice:
    """一次军师输出的结构化结果。

    structured=False 表示模型没按格式写（或者根本没返回），这时四个块可能
    是空的，全文放在 raw 里——界面必须如实显示"没按格式返回"，不许假装拆好了。
    """

    judge: str = ""
    first: str = ""
    options: List[str] = field(default_factory=list)
    risk: str = ""
    raw: str = ""
    structured: bool = False

    def as_transcript(self) -> str:
        """还原成文本（写复盘、复制整段时用）。"""
        if not self.structured:
            return self.raw
        rows = []
        if self.judge:
            rows.append("【判断】" + self.judge)
        if self.first:
            rows.append("【首选】" + self.first)
        if self.options:
            rows.append("【备选】" + "\n".join(self.options))
        if self.risk:
            rows.append("【风险】" + self.risk)
        return "\n".join(rows)


def _strip_bullet(line: str) -> str:
    text = line.strip()
    for prefix in _BULLET_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):].strip()
    # "1. xxx" / "1）xxx" 这种编号也去掉
    head, sep, tail = text.partition(".")
    if sep and head.strip().isdigit():
        return tail.strip()
    head, sep, tail = text.partition("）")
    if sep and head.strip().isdigit():
        return tail.strip()
    return text


def parse_advice(text: str) -> Advice:
    """把军师的输出拆成 判断 / 首选 / 备选 / 风险。

    纯函数，可测；拆不出来就老实退化成 raw（界面会照原样显示）。
    """
    body = (text or "").strip()
    if not body:
        return Advice()

    marks = []
    for label, key in SECTION_LABELS:
        index = body.find(label)
        if index >= 0:
            marks.append((index, label, key))
    if not marks:
        return Advice(raw=body, structured=False)
    marks.sort(key=lambda item: item[0])

    buckets: Dict[str, str] = {}
    for position, (index, label, key) in enumerate(marks):
        start = index + len(label)
        end = marks[position + 1][0] if position + 1 < len(marks) else len(body)
        buckets[key] = body[start:end].strip()

    options: List[str] = []
    for line in buckets.get("options", "").splitlines():
        cleaned = _strip_bullet(line)
        if cleaned:
            options.append(cleaned)

    return Advice(
        judge=buckets.get("judge", ""),
        first=buckets.get("first", ""),
        options=options,
        risk=buckets.get("risk", ""),
        raw=body,
        structured=True,
    )
