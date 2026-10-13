"""关系体检（1.0.0）：把整段聊天算成客观数字，再让军师基于数字解读。

为什么要先算统计、而不是把聊天记录整段丢给模型：
1. **模型会编**。"你们最近聊得少了"这种话它张口就来，但"最近 7 天 12 条、
   前 7 天 61 条"是算出来的，赖不掉；
2. **便宜**。几千条记录本地算完只要几十毫秒，送模型的只有统计 + 一份抽样；
3. **可解释**。界面上先把数字摆出来，人自己能看出一半结论。

统计口径（都在这里写清，免得以后自己都记不清）：
- **回复间隔**：对方发完、我下一条发出之间的秒数（只算 6 小时以内的，隔夜不算"回得慢"）；
- **新对话发起**：与上一条间隔超过 6 小时的那条消息，算作一次新的话题发起；
- **冷淡占比**：对方回复里字数 ≤3 或纯语气词的比例（`rules.is_short_reply()` 同一套标准）；
- **活跃度变化**：最近 7 天 vs 此前 7 天的消息条数。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from . import brain as brain_mod
from . import rules

# 隔多久算"开了一个新话题"
NEW_TOPIC_GAP_SECONDS = 6 * 3600
# 隔多久就不算"回复慢"（隔夜那种）
REPLY_WINDOW_SECONDS = 6 * 3600
# 送模型的聊天样本：最近这么多条 + 最早这么多条 + 中间均匀抽若干
SAMPLE_RECENT = 120
SAMPLE_OLDEST = 30
SAMPLE_MIDDLE = 50


def _ts_of(message: Dict[str, Any]) -> int:
    try:
        return int(message.get("createTime") or message.get("create_time") or 0)
    except (TypeError, ValueError):
        return 0


def _body_of(message: Dict[str, Any]) -> str:
    return str(message.get("content") or "").strip()


def _is_mine(message: Dict[str, Any]) -> bool:
    return bool(message.get("isSent"))


@dataclass
class ChatStats:
    """一段聊天的客观画像。字段全是能算出来的，不带判断。"""

    total: int = 0
    mine: int = 0
    theirs: int = 0
    first_ts: int = 0
    last_ts: int = 0
    days_span: int = 0
    active_days: int = 0
    avg_mine_chars: float = 0.0
    avg_theirs_chars: float = 0.0
    mine_reply_median: float = 0.0        # 我回对方的中位间隔（秒）
    theirs_reply_median: float = 0.0      # 对方回我的中位间隔（秒）
    initiations_mine: int = 0
    initiations_theirs: int = 0
    recent_7d: int = 0
    previous_7d: int = 0
    non_text_mine: int = 0
    non_text_theirs: int = 0
    short_ratio_theirs: float = 0.0
    longest_silence_days: int = 0
    last_message_from_them_hours: float = -1.0
    top_topics: List[Tuple[str, int]] = field(default_factory=list)

    # ── 给人看的几行 ────────────────────────────────────────────────────
    def lines(self) -> List[str]:
        rows: List[str] = []
        rows.append("消息总数：%d（我 %d 条 / 对方 %d 条）" % (self.total, self.mine, self.theirs))
        if self.first_ts and self.last_ts:
            rows.append(
                "时间跨度：%s 到 %s，共 %d 天（其中 %d 天有聊天）"
                % (
                    datetime.fromtimestamp(self.first_ts).strftime("%Y-%m-%d"),
                    datetime.fromtimestamp(self.last_ts).strftime("%Y-%m-%d"),
                    self.days_span,
                    self.active_days,
                )
            )
        if self.total:
            rows.append(
                "平均单条字数：我 %.1f 字 / 对方 %.1f 字" % (self.avg_mine_chars, self.avg_theirs_chars)
            )
        if self.mine_reply_median or self.theirs_reply_median:
            rows.append(
                "回复速度（中位）：我回对方 %s / 对方回我 %s"
                % (_human_seconds(self.mine_reply_median), _human_seconds(self.theirs_reply_median))
            )
        if self.total:
            rows.append(
                "谁先开口：我发起 %d 次 / 对方发起 %d 次"
                % (self.initiations_mine, self.initiations_theirs)
            )
            rows.append("最近 7 天 %d 条；此前 7 天 %d 条" % (self.recent_7d, self.previous_7d))
        if self.short_ratio_theirs:
            rows.append("对方短回复（≤3 字或纯语气词）占比：%.0f%%" % (self.short_ratio_theirs * 100))
        if self.non_text_mine or self.non_text_theirs:
            rows.append(
                "非文本消息：我 %d 条 / 对方 %d 条" % (self.non_text_mine, self.non_text_theirs)
            )
        if self.longest_silence_days:
            rows.append("最长一次断聊：%d 天" % self.longest_silence_days)
        if self.last_message_from_them_hours >= 0:
            rows.append("距离对方上一条消息：%.1f 小时" % self.last_message_from_them_hours)
        if self.top_topics:
            rows.append(
                "出现最多的词：%s"
                % "、".join("%s(%d)" % (word, count) for word, count in self.top_topics[:8])
            )
        return rows

    def highlights(self) -> List[Tuple[str, str]]:
        """界面上的数字卡片。"""
        cards: List[Tuple[str, str]] = [("消息总数", str(self.total))]
        if self.mine or self.theirs:
            ratio = (self.mine / max(1, self.mine + self.theirs)) * 100
            cards.append(("我发的占比", "%.0f%%" % ratio))
        if self.days_span:
            cards.append(("时间跨度", "%d 天" % self.days_span))
        if self.theirs_reply_median:
            cards.append(("对方回我的中位速度", _human_seconds(self.theirs_reply_median)))
        if self.initiations_mine or self.initiations_theirs:
            cards.append(
                ("谁先开口", "我 %d / 对方 %d" % (self.initiations_mine, self.initiations_theirs))
            )
        cards.append(("最近7天", "%d 条" % self.recent_7d))
        if self.short_ratio_theirs:
            cards.append(("对方短回复占比", "%.0f%%" % (self.short_ratio_theirs * 100)))
        return cards


def _human_seconds(seconds: float) -> str:
    if seconds <= 0:
        return "—"
    if seconds < 60:
        return "%.0f 秒" % seconds
    if seconds < 3600:
        return "%.0f 分钟" % (seconds / 60.0)
    return "%.1f 小时" % (seconds / 3600.0)


def _median(values: List[float]) -> float:
    clean = [v for v in values if v > 0]
    if not clean:
        return 0.0
    return float(statistics.median(clean))


def chat_stats(messages: List[Dict[str, Any]], now: Optional[datetime] = None) -> ChatStats:
    """把一堆消息算成客观统计。纯函数，不联网、不问模型。"""
    rows = [m for m in (messages or []) if isinstance(m, dict)]
    stats = ChatStats()
    if not rows:
        return stats
    rows.sort(key=_ts_of)
    moment = now or datetime.now()

    stats.total = len(rows)
    stats.first_ts = _ts_of(rows[0])
    stats.last_ts = _ts_of(rows[-1])

    mine_chars: List[int] = []
    theirs_chars: List[int] = []
    mine_replies: List[float] = []
    theirs_replies: List[float] = []
    theirs_short = 0
    theirs_text_count = 0
    days = set()
    gaps: List[float] = []

    previous_ts = 0
    previous_mine: Optional[bool] = None

    for message in rows:
        stamp = _ts_of(message)
        mine = _is_mine(message)
        body = _body_of(message)
        non_text = not body

        if mine:
            stats.mine += 1
            if non_text:
                stats.non_text_mine += 1
            else:
                mine_chars.append(len(body))
        else:
            stats.theirs += 1
            if non_text:
                stats.non_text_theirs += 1
            else:
                theirs_chars.append(len(body))
                theirs_text_count += 1
                if rules.is_short_reply(body):
                    theirs_short += 1

        if stamp:
            days.add(datetime.fromtimestamp(stamp).strftime("%Y-%m-%d"))
        if previous_ts and stamp:
            gap = float(stamp - previous_ts)
            gaps.append(gap)
            if gap > NEW_TOPIC_GAP_SECONDS:
                # 隔了很久，这条算新话题的发起
                if mine:
                    stats.initiations_mine += 1
                else:
                    stats.initiations_theirs += 1
            elif gap <= REPLY_WINDOW_SECONDS and previous_mine is not None and previous_mine != mine:
                # 一来一回：谁后发，谁的"回复间隔"就是这段
                if mine:
                    mine_replies.append(gap)
                else:
                    theirs_replies.append(gap)
        elif stamp:
            # 第一条也算一次发起（不知道是谁先，按发信人算）
            if mine:
                stats.initiations_mine += 1
            else:
                stats.initiations_theirs += 1

        previous_ts = stamp
        previous_mine = mine

    if stats.first_ts:
        stats.days_span = max(1, (moment.date() - datetime.fromtimestamp(stats.first_ts).date()).days + 1)
        stats.longest_silence_days = int(max(gaps, default=0) // 86400)

    stats.active_days = len(days)
    stats.avg_mine_chars = (sum(mine_chars) / len(mine_chars)) if mine_chars else 0.0
    stats.avg_theirs_chars = (sum(theirs_chars) / len(theirs_chars)) if theirs_chars else 0.0
    stats.mine_reply_median = _median(mine_replies)
    stats.theirs_reply_median = _median(theirs_replies)
    stats.short_ratio_theirs = (theirs_short / theirs_text_count) if theirs_text_count else 0.0

    recent_line = moment - timedelta(days=7)
    previous_line = moment - timedelta(days=14)
    for message in rows:
        stamp = _ts_of(message)
        if not stamp:
            continue
        when = datetime.fromtimestamp(stamp)
        if when >= recent_line:
            stats.recent_7d += 1
        elif when >= previous_line:
            stats.previous_7d += 1

    for message in reversed(rows):
        if not _is_mine(message):
            stamp = _ts_of(message)
            if stamp:
                stats.last_message_from_them_hours = (
                    moment - datetime.fromtimestamp(stamp)
                ).total_seconds() / 3600.0
            break

    stats.top_topics = _top_words(rows)
    return stats


# 中文里抓"词"没法做得准（没有分词器、也不想引依赖），所以只抓两类：
# 2-4 字的常见话题词（靠出现次数筛），以及被反复提到的具体东西。
_STOPWORDS = {
    "哈哈", "哈哈哈", "呵呵", "嗯嗯", "哦哦", "好的", "可以", "不是", "什么", "怎么",
    "这个", "那个", "现在", "今天", "明天", "昨天", "我们", "你们", "他们", "自己",
    "知道", "觉得", "可能", "应该", "真的", "就是", "还是", "但是", "因为", "所以",
    "如果", "然后", "时候", "一起", "一个", "没有", "有点", "一下", "已经", "出来",
}


def _top_words(rows: List[Dict[str, Any]], limit: int = 12) -> List[Tuple[str, int]]:
    """粗略的"话题词"：把每条消息切成 2-4 字的连续汉字片段再数频次。

    不是正经分词（本项目不引依赖），但足够看出"最近老在聊什么"。
    """
    import re

    counter: Dict[str, int] = {}
    pattern = re.compile(r"[\u4e00-\u9fa5]{2,4}")
    for message in rows:
        body = _body_of(message)
        if not body:
            continue
        for piece in pattern.findall(body):
            if piece in _STOPWORDS or len(piece) < 2:
                continue
            counter[piece] = counter.get(piece, 0) + 1
    ranked = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return [(word, count) for word, count in ranked[:limit] if count >= 3]


def sample_for_analysis(messages: List[Dict[str, Any]], limit: int = 200) -> List[Dict[str, Any]]:
    """挑一份给模型看的样本：**最近的 + 最早的 + 中间均匀抽的**。

    为什么不只给最近的：只看最近 100 条，模型分不清"一直这样"还是"最近变了"。
    给一点最早的记录，它才能说"开头是那样、现在是这样"。
    """
    rows = list(messages or [])
    if len(rows) <= limit:
        return rows
    recent = rows[-SAMPLE_RECENT:]
    oldest = rows[:SAMPLE_OLDEST]
    middle_all = rows[SAMPLE_OLDEST:len(rows) - SAMPLE_RECENT]
    middle: List[Dict[str, Any]] = []
    if middle_all and SAMPLE_MIDDLE > 0:
        step = max(1, len(middle_all) // SAMPLE_MIDDLE)
        middle = middle_all[::step][:SAMPLE_MIDDLE]
    picked = oldest + middle + recent
    # 去重（按时间戳 + 内容），并保持时间顺序
    seen = set()
    unique: List[Dict[str, Any]] = []
    for message in picked:
        key = (_ts_of(message), _body_of(message)[:20], _is_mine(message))
        if key in seen:
            continue
        seen.add(key)
        unique.append(message)
    unique.sort(key=_ts_of)
    return unique[: limit + SAMPLE_OLDEST + SAMPLE_MIDDLE]


def build_analysis_prompt(
    contact_name: str,
    stats: ChatStats,
    sample: List[Dict[str, Any]],
    contact_profile: str = "",
    style_text: str = "",
    total_count: int = 0,
    red_hits: Optional[List[str]] = None,
) -> str:
    """拼"关系体检"的任务书：先摆数字，再给样本，最后要它按格式下判断。"""
    parts: List[str] = []
    parts.append(
        "你是「狗头军师」。现在不要拟任何一句要发的话，只做一件事：\n"
        "**看这段关系的客观数据与聊天样本，判断这两个人现在是什么状况**。\n"
        "硬规矩：\n"
        "1. **数字优先于感觉**：能用上面统计里的数字支撑的判断才写，不许编造没给的数字。\n"
        "2. **不许安慰人**：这是给自己看的体检，讲实话比讲好听的更有用。\n"
        "3. 分不清的地方就说「数据不够，看不出来」，不要硬凑结论。\n"
        "4. 不许用 emoji。"
    )
    parts.append("── 客观统计（本地算的，共 %d 条记录）──\n%s" % (total_count or stats.total, "\n".join(stats.lines())))
    if contact_profile:
        parts.append("── 这个人的档案（含本人填的背景）──\n" + contact_profile)
    if style_text:
        parts.append("── 本人平时怎么说话（用于判断语气差异）──\n" + style_text)
    parts.append(
        "── 聊天样本（早→晚，共 %d 条；中间是抽样，不是全部）──\n%s"
        % (len(sample), brain_mod.format_transcript(sample, limit=len(sample) + 10))
    )
    if red_hits:
        parts.append("── 注意 ──\n样本里出现了这些敏感词：%s。" % "、".join(red_hits))
    parts.append(
        "── 输出格式（严格照这个写，不要额外解释）──\n"
        "【现状】3-5 句：这段关系现在处在什么状态。至少引用两个统计数字。\n"
        "【投入】2-4 句：谁更主动、谁回得更快、投入是不是对等（拿数字说）。\n"
        "【好信号】最多 3 条，每条一句话。\n"
        "【坏信号】最多 3 条，每条一句话。\n"
        "【风险】1-2 句：最可能出问题的地方。\n"
        "【接下来】2-3 条具体能做的事（不要写「多沟通」这种废话）。"
    )
    return "\n\n".join(parts)


# 与军师建议的解析同一套路：认这几个标记，认不出就老实说认不出
ANALYSIS_SECTIONS: List[Tuple[str, str]] = [
    ("【现状】", "status"),
    ("【投入】", "balance"),
    ("【好信号】", "good"),
    ("【坏信号】", "bad"),
    ("【风险】", "risk"),
    ("【接下来】", "next"),
]


def parse_analysis(text: str) -> Dict[str, str]:
    """把体检结果拆成六块。拆不出来就返回 {"raw": 原文}。"""
    body = (text or "").strip()
    if not body:
        return {}
    marks: List[Tuple[int, str, str]] = []
    for label, key in ANALYSIS_SECTIONS:
        index = body.find(label)
        if index >= 0:
            marks.append((index, label, key))
    if not marks:
        return {"raw": body}
    marks.sort(key=lambda item: item[0])
    result: Dict[str, str] = {}
    for position, (index, label, key) in enumerate(marks):
        start = index + len(label)
        end = marks[position + 1][0] if position + 1 < len(marks) else len(body)
        result[key] = body[start:end].strip()
    return result
