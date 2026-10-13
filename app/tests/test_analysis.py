"""关系体检的自测（1.0.0）：统计口径、样本抽样、结果解析。

统计是纯函数、不联网，所以这些测试跑得飞快，也把"口径"钉死：
回复间隔只算一来一回且不隔夜、新话题按 6 小时切、冷淡占比用同一套短回复标准。
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goutou_chat import analysis  # noqa: E402

NOW = datetime(2026, 10, 13, 20, 0, 0)


def msg(minutes_ago: float, mine: bool, content: str = "你今天晚上有空吗", kind: str = "") -> dict:
    return {
        "createTime": int((NOW - timedelta(minutes=minutes_ago)).timestamp()),
        "isSent": mine,
        "content": content,
        "renderType": kind or None,
    }


class TestChatStats(unittest.TestCase):
    def test_empty(self) -> None:
        stats = analysis.chat_stats([], now=NOW)
        self.assertEqual(stats.total, 0)
        self.assertEqual(stats.lines(), ["消息总数：0（我 0 条 / 对方 0 条）"])

    def test_counts_and_ratio(self) -> None:
        rows = [
            msg(60, False, "在忙吗"),
            msg(59, True, "刚忙完，怎么了"),
            msg(58, False, "没事"),
            msg(57, True, "那就好"),
        ]
        stats = analysis.chat_stats(rows, now=NOW)
        self.assertEqual(stats.total, 4)
        self.assertEqual(stats.mine, 2)
        self.assertEqual(stats.theirs, 2)
        cards = dict(stats.highlights())
        self.assertEqual(cards["消息总数"], "4")
        self.assertEqual(cards["我发的占比"], "50%")

    def test_reply_interval_only_for_back_and_forth(self) -> None:
        # 对方 10 分钟前发，我 8 分钟前回 → 我回对方用了 2 分钟
        rows = [msg(10, False), msg(8, True)]
        stats = analysis.chat_stats(rows, now=NOW)
        self.assertAlmostEqual(stats.mine_reply_median, 120.0, delta=1.0)
        self.assertEqual(stats.theirs_reply_median, 0.0)

    def test_overnight_reply_is_not_counted(self) -> None:
        # 隔了 12 小时才回，不算"回得慢"，也不并进中位数
        rows = [msg(12 * 60, False), msg(1, True)]
        stats = analysis.chat_stats(rows, now=NOW)
        self.assertEqual(stats.mine_reply_median, 0.0)
        # 隔太久 → 算成一次新话题发起
        self.assertEqual(stats.initiations_mine, 1)

    def test_new_topic_split(self) -> None:
        rows = [
            msg(10 * 60, False, "周末去哪"),
            msg(10 * 60 - 1, True, "都行"),
            msg(60, False, "睡了吗"),          # 隔了 9 小时 → 新话题
        ]
        stats = analysis.chat_stats(rows, now=NOW)
        # 对方发起 2 次：**第一条也算一次发起**（总得有人先开口），加上那次隔夜的新话题
        self.assertEqual(stats.initiations_theirs, 2)
        self.assertEqual(stats.initiations_mine, 0)

    def test_short_reply_ratio(self) -> None:
        rows = [
            msg(30, False, "嗯"),
            msg(29, True, "怎么了"),
            msg(28, False, "哦"),
            msg(27, False, "今天下班挺早的"),
        ]
        stats = analysis.chat_stats(rows, now=NOW)
        # 对方 3 条里 2 条是短回复
        self.assertAlmostEqual(stats.short_ratio_theirs, 2 / 3, places=2)

    def test_non_text_counted(self) -> None:
        rows = [msg(20, False, "", kind="图片"), msg(19, True, "看到了")]
        stats = analysis.chat_stats(rows, now=NOW)
        self.assertEqual(stats.non_text_theirs, 1)
        self.assertEqual(stats.non_text_mine, 0)

    def test_recent_vs_previous_week(self) -> None:
        rows = [
            msg(2 * 24 * 60, False),      # 2 天前 → 最近 7 天
            msg(3 * 24 * 60, True),       # 3 天前 → 最近 7 天
            msg(9 * 24 * 60, False),      # 9 天前 → 此前 7 天
            msg(10 * 24 * 60, True),      # 10 天前 → 此前 7 天
        ]
        stats = analysis.chat_stats(rows, now=NOW)
        self.assertEqual(stats.recent_7d, 2)
        self.assertEqual(stats.previous_7d, 2)

    def test_span_and_active_days(self) -> None:
        rows = [msg(5 * 24 * 60, False), msg(1 * 24 * 60, True)]
        stats = analysis.chat_stats(rows, now=NOW)
        self.assertGreaterEqual(stats.days_span, 5)
        self.assertEqual(stats.active_days, 2)

    def test_lines_are_readable(self) -> None:
        rows = [msg(30, False, "在忙吗"), msg(29, True, "刚忙完")]
        stats = analysis.chat_stats(rows, now=NOW)
        joined = "\n".join(stats.lines())
        self.assertIn("消息总数：2", joined)
        self.assertIn("平均单条字数", joined)
        self.assertIn("谁先开口", joined)


class TestSampling(unittest.TestCase):
    def test_small_history_kept_whole(self) -> None:
        rows = [msg(i, i % 2 == 0) for i in range(50)]
        picked = analysis.sample_for_analysis(rows, limit=200)
        self.assertEqual(len(picked), 50)

    def test_large_history_keeps_both_ends(self) -> None:
        rows = [msg(1000 - i, i % 2 == 0, "第%d条" % i) for i in range(1000)]
        picked = analysis.sample_for_analysis(rows, limit=200)
        self.assertLess(len(picked), 1000)
        self.assertGreater(len(picked), 100)
        stamps = [analysis._ts_of(item) for item in picked]
        self.assertEqual(stamps, sorted(stamps))              # 保持时间顺序
        self.assertEqual(stamps[0], analysis._ts_of(rows[0]))  # 最早的还在
        self.assertEqual(stamps[-1], analysis._ts_of(rows[-1]))  # 最新的还在


class TestAnalysisPromptAndParse(unittest.TestCase):
    def setUp(self) -> None:
        rows = [msg(30, False, "在忙吗"), msg(29, True, "刚忙完，怎么了")]
        self.stats = analysis.chat_stats(rows, now=NOW)
        self.prompt = analysis.build_analysis_prompt(
            contact_name="联系人A",
            stats=self.stats,
            sample=rows,
            contact_profile="关系：暧昧",
            style_text="我说话很短",
            total_count=self.stats.total,
        )

    def test_prompt_carries_numbers_and_format(self) -> None:
        self.assertIn("客观统计", self.prompt)
        self.assertIn("消息总数：2", self.prompt)
        self.assertIn("【现状】", self.prompt)
        self.assertIn("【接下来】", self.prompt)
        self.assertIn("不许编造", self.prompt)
        self.assertIn("暧昧", self.prompt)

    def test_parse_sections(self) -> None:
        text = (
            "【现状】还行。\n【投入】我多一点。\n【好信号】1. 她会主动\n"
            "【坏信号】1. 回得慢\n【风险】可能冷下来。\n【接下来】1. 约她吃饭"
        )
        sections = analysis.parse_analysis(text)
        self.assertIn("还行", sections["status"])
        self.assertIn("主动", sections["good"])
        self.assertIn("冷下来", sections["risk"])
        self.assertIn("吃饭", sections["next"])

    def test_parse_unstructured_falls_back_to_raw(self) -> None:
        sections = analysis.parse_analysis("你们挺合适的，继续加油。")
        self.assertEqual(list(sections.keys()), ["raw"])

    def test_parse_empty(self) -> None:
        self.assertEqual(analysis.parse_analysis(""), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
