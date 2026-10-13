"""自动驾驶的自测（0.6.0）。

用假的读消息 / 切会话 / 大脑 / 发送器，**一次都不碰真窗口**。盯三件事：

1. 演练时**不许调 switch**（演练不该顺手把人的窗口抢到前面去）；
2. 真发时顺序必须是"先 switch 拿哈希 → 再 send 带哈希"；
3. 没新消息 / 最后一条是我发的 / 停手条件命中 → 一律不动手。
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goutou_chat import autopilot, config as cfgmod, rules  # noqa: E402
from goutou_chat.advisor import AdviseResult  # noqa: E402
from goutou_chat.brake import Brake  # noqa: E402
from goutou_chat.sender import AutoSender, consume  # noqa: E402


def msg(minutes_ago: float, sent: bool, content: str = "你今天晚上有空吗") -> dict:
    return {
        "createTime": int(time.time() - minutes_ago * 60),
        "isSent": sent,
        "content": content,
        "senderDisplayName": "我" if sent else "联系人A",
    }


class FakeWda:
    def __init__(self, messages) -> None:
        self._messages = list(messages)          # 旧 → 新

    def ensure_backend(self):
        return True, "后端在跑"

    def messages(self, username, limit=30, order="desc"):
        rows = list(self._messages)
        return list(reversed(rows)) if order == "desc" else rows


class FakePct:
    def __init__(self) -> None:
        self.switched = []
        self.sent = []

    def switch(self, target, to):
        self.switched.append((target, to))
        return {"switched-to": to, "headerImg": "header.png", "hash": "HASH1"}

    def send(self, target, text, expect_hash=None, timeout=150):
        self.sent.append({"target": target, "text": text, "expect_hash": expect_hash})
        return {"sent": "True"}


class FakeAdvisor:
    def __init__(self, text: str = "【判断】她在等你\n【首选】在呢，刚忙完") -> None:
        self.text = text
        self.calls = 0

    def advise(self, kwargs, **rest) -> AdviseResult:
        self.calls += 1
        return AdviseResult(text=self.text, channel="direct", elapsed_ms=10, first_token_ms=5)


def grant(**overrides) -> rules.AutoGrant:
    now = time.time()
    base = rules.AutoGrant(
        mode=rules.Mode.AUTO_ONCE.value,
        username="wxid_contact_a",
        name="联系人A",
        created_at=now,
        expires_at=now + 1800,
        remaining=1,
        armed=True,
        dry_run=True,
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def build(messages, dry_run=True):
    cfg = cfgmod.Config()
    cfg.read_limit = 30
    cfg.max_turns = 15
    cfg.stop_after_short_replies = 2
    cfg.stop_after_minutes = 15
    pct = FakePct()
    sender = AutoSender(cfg, Brake(), pct)
    pilot = autopilot.Autopilot(cfg, FakeWda(messages), pct, FakeAdvisor(), sender)
    return pilot, pct, grant(dry_run=dry_run)


class TestFreshness(unittest.TestCase):
    def test_newest_stamp(self) -> None:
        rows = [{"createTime": 100}, {"createTime": 300}, {"createTime": 200}]
        self.assertEqual(autopilot.newest_stamp(rows), 300)
        self.assertEqual(autopilot.newest_stamp([]), 0)

    def test_new_incoming(self) -> None:
        rows = [msg(5, False), msg(1, False)]
        fresh, _why = autopilot.has_new_incoming(rows, 0)
        self.assertTrue(fresh)

    def test_my_own_last_message_waits(self) -> None:
        rows = [msg(5, False), msg(1, True, "我先说的")]
        fresh, why = autopilot.has_new_incoming(rows, 0)
        self.assertFalse(fresh)
        self.assertIn("我", why)

    def test_nothing_new(self) -> None:
        rows = [msg(5, False)]
        fresh, why = autopilot.has_new_incoming(rows, autopilot.newest_stamp(rows))
        self.assertFalse(fresh)
        self.assertIn("没有新消息", why)


class TestSteps(unittest.TestCase):
    def test_dry_run_does_not_touch_the_screen(self) -> None:
        pilot, pct, g = build([msg(3, False), msg(1, False)], dry_run=True)
        step = pilot.step(g, "wechat")
        self.assertTrue(step.acted)
        self.assertEqual(pct.switched, [])       # 演练绝不切窗口
        self.assertEqual(pct.sent, [])
        self.assertIsNotNone(step.outcome)
        self.assertTrue(step.outcome.dry_run)
        self.assertIn("在呢", step.draft)

    def test_real_send_switches_first_then_sends_with_hash(self) -> None:
        pilot, pct, g = build([msg(3, False), msg(1, False)], dry_run=False)
        # 把"人在用电脑"这条闸打成"没人用"：否则这条测试会随着你动不动鼠标而时绿时红
        with mock.patch.object(autopilot, "user_is_busy", return_value=False):
            step = pilot.step(g, "wechat")
        self.assertEqual(pct.switched, [("wechat", "联系人A")])
        self.assertEqual(len(pct.sent), 1)
        self.assertEqual(pct.sent[0]["expect_hash"], "HASH1")   # 用的是切换时那个哈希
        self.assertEqual(pct.sent[0]["text"], step.draft)
        self.assertTrue(step.outcome.sent)
        self.assertEqual(step.header_image, "header.png")

    def test_no_new_message_means_no_action(self) -> None:
        messages = [msg(3, False)]
        pilot, pct, g = build(messages)
        pilot.seed("wxid_contact_a", messages)          # 已经看过了
        step = pilot.step(g, "wechat")
        self.assertFalse(step.acted)
        self.assertEqual(pct.sent, [])

    def test_force_runs_even_without_new_message(self) -> None:
        messages = [msg(3, False)]
        pilot, _pct, g = build(messages)
        pilot.seed("wxid_contact_a", messages)
        step = pilot.step(g, "wechat", force=True)
        self.assertTrue(step.acted)

    def test_short_replies_stop_the_car(self) -> None:
        messages = [msg(3, False, "嗯"), msg(2, False, "哦"), msg(1, False, "好")]
        pilot, pct, g = build(messages)
        step = pilot.step(g, "wechat")
        self.assertFalse(step.acted)
        self.assertIn("停手", step.reason)
        self.assertEqual(pct.sent, [])

    def test_expired_grant_does_nothing(self) -> None:
        pilot, pct, _g = build([msg(1, False)])
        dead = grant(expires_at=time.time() - 10)
        step = pilot.step(dead, "wechat")
        self.assertFalse(step.acted)
        self.assertIn("收回", step.reason)

    def test_unstructured_answer_still_gives_something(self) -> None:
        cfg = cfgmod.Config()
        pct = FakePct()
        sender = AutoSender(cfg, Brake(), pct)
        pilot = autopilot.Autopilot(cfg, FakeWda([msg(1, False)]), pct, FakeAdvisor("随便写的一句话"), sender)
        step = pilot.step(grant(), "wechat")
        self.assertTrue(step.acted)
        self.assertTrue(step.draft)

    def test_busy_user_defers_real_send(self) -> None:
        """你在用电脑时不许抢窗口（0.6.1 的护栏）——这正是"切出去打字被打断"的修法。"""
        pilot, pct, g = build([msg(3, False), msg(1, False)], dry_run=False)
        with mock.patch.object(autopilot, "user_is_busy", return_value=True):
            step = pilot.step(g, "wechat")
        self.assertFalse(step.acted)
        self.assertIn("正在用电脑", step.reason)
        self.assertEqual(pct.switched, [])
        self.assertEqual(pct.sent, [])

    def test_manual_force_ignores_busy_guard(self) -> None:
        """手动点「立刻接一次」时不受这条限制——是你自己要求现在发的。"""
        pilot, pct, g = build([msg(3, False), msg(1, False)], dry_run=False)
        with mock.patch.object(autopilot, "user_is_busy", return_value=True):
            step = pilot.step(g, "wechat", force=True)
        self.assertTrue(step.acted)
        self.assertEqual(len(pct.sent), 1)

    def test_dry_run_ignores_busy_guard(self) -> None:
        """演练本来就不抢屏幕，你在不在电脑前都无所谓。"""
        pilot, _pct, g = build([msg(3, False), msg(1, False)], dry_run=True)
        with mock.patch.object(autopilot, "user_is_busy", return_value=True):
            step = pilot.step(g, "wechat")
        self.assertTrue(step.acted)


    def test_turn_limit_stops_the_car(self) -> None:
        """轮数上限真的会刹车（0.7.0 之前这里一直传 0，等于没有这道闸）。"""
        pilot, _pct, g = build([msg(1, False, "今晚一起吃饭吗")], dry_run=True)
        pilot._turns["wxid_contact_a"] = int(pilot.cfg.max_turns)
        step = pilot.step(g, "wechat")
        self.assertFalse(step.acted)
        self.assertIn("上限", step.reason)

    def test_turns_count_up_after_send(self) -> None:
        # 只有**真的发出去**才计轮数（演练不算——它没发）
        pilot, _pct, g = build([msg(1, False, "今晚一起吃饭吗")], dry_run=False)
        with mock.patch.object(autopilot, "user_is_busy", return_value=False):
            pilot.step(g, "wechat")
        self.assertEqual(pilot.turns("wxid_contact_a"), 1)

    def test_standing_grant_keeps_going_after_one(self) -> None:
        """常驻档发完一条不该失效（单次档才会）。"""
        pilot, pct, _g = build([msg(1, False, "今晚一起吃饭吗")], dry_run=False)
        standing = grant(mode=rules.Mode.AUTO_STANDING.value, remaining=-1, dry_run=False)
        with mock.patch.object(autopilot, "user_is_busy", return_value=False):
            step = pilot.step(standing, "wechat")
        self.assertTrue(step.outcome.sent)
        after = consume(standing)
        self.assertEqual(after.remaining, -1)                     # 不限条数
        self.assertTrue(after.armed)
        self.assertEqual(after.mode, rules.Mode.AUTO_STANDING.value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
