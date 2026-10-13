"""自动发送链路的自测（0.5.0）。

这一段是整个项目最危险的代码（它真的会替人发消息），所以每一道闸门都单独测：
演练不发、没武装不发、群聊不发、红线不发、制动按下不发、额度/时间到了不发、
上下文过期不重发。用的是假的 Pct——**测试永远不会真的碰键盘**。
"""

from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goutou_chat import rules, sender  # noqa: E402
from goutou_chat.brake import Brake  # noqa: E402
from goutou_chat.pct import PctError, StaleSendError  # noqa: E402


class FakePct:
    """假的发送零件：只记下被要求发什么，绝不碰真窗口。"""

    def __init__(self, error: Exception = None, info=None) -> None:
        self.calls = []
        self.error = error
        self.info = info if info is not None else {"sent": "True", "hash": "abc"}

    def send(self, target, text, expect_hash=None, timeout=150):
        self.calls.append({"target": target, "text": text, "expect_hash": expect_hash})
        if self.error is not None:
            raise self.error
        return dict(self.info)


def armed_once(**overrides) -> rules.AutoGrant:
    # 用相对时间：绝对时间戳会随着"今天是几号"过期（第一版就踩了这个）
    now = time.time()
    grant = rules.AutoGrant(
        mode=rules.Mode.AUTO_ONCE.value,
        username="wxid_contact_a",
        name="联系人A",
        armed=True,
        remaining=1,
        dry_run=False,
        created_at=now,
        expires_at=now + 3600.0,
    )
    for key, value in overrides.items():
        setattr(grant, key, value)
    return grant


class TestGrantState(unittest.TestCase):
    def test_unarmed_is_refused(self) -> None:
        grant = armed_once(armed=False)
        ok, why = grant.state(now=1500.0)
        self.assertFalse(ok)
        self.assertIn("确认", why)

    def test_expired_is_refused(self) -> None:
        grant = armed_once(expires_at=1200.0)
        ok, why = grant.state(now=1500.0)
        self.assertFalse(ok)
        self.assertIn("到点", why)

    def test_quota_exhausted(self) -> None:
        grant = armed_once(remaining=0)
        ok, why = grant.state(now=1500.0)
        self.assertFalse(ok)
        self.assertIn("额度", why)

    def test_standing_has_no_expiry(self) -> None:
        grant = armed_once(mode=rules.Mode.AUTO_STANDING.value, expires_at=0.0, remaining=-1)
        ok, _ = grant.state(now=999999.0)
        self.assertTrue(ok)
        self.assertEqual(grant.left_seconds(now=999999.0), -1)
        self.assertIn("不按时间过期", grant.relative_expiry())

    def test_timed_grant_expires_on_the_dot(self) -> None:
        """限时档到点就失效——用 1 秒版验，等价于界面上的"限时 1 分钟"。"""
        now = time.time()
        grant = rules.AutoGrant(
            mode=rules.Mode.AUTO_TIMED.value,
            username="wxid_a",
            name="某人",
            created_at=now,
            expires_at=now + 1.0,
            remaining=-1,
            armed=True,
        )
        ok, _ = grant.state(now=now + 0.4)
        self.assertTrue(ok)
        self.assertGreater(grant.left_seconds(now=now + 0.4), 0)
        ok, why = grant.state(now=now + 1.05)
        self.assertFalse(ok)
        self.assertIn("到点", why)
        self.assertEqual(grant.left_seconds(now=now + 1.05), 0)

    def test_expiry_message_has_seconds_when_close(self) -> None:
        now = time.time()
        grant = armed_once(expires_at=now + 42)
        self.assertIn("秒", grant.relative_expiry())

    def test_dict_roundtrip_and_fault_tolerance(self) -> None:
        grant = armed_once(dry_run=True, trigger="after_me")
        again = rules.AutoGrant.from_dict(grant.to_dict())
        self.assertEqual(again.username, "wxid_contact_a")
        self.assertTrue(again.dry_run)
        self.assertEqual(again.trigger, "after_me")
        # 烂数据不许炸，而且读不清就当"演练 + 未武装"
        broken = rules.AutoGrant.from_dict({"remaining": "不是数字", "armed": "yes"})
        self.assertEqual(broken.remaining, 0)
        self.assertTrue(broken.dry_run)


class TestDecisions(unittest.TestCase):
    def test_brake_blocks_everything(self) -> None:
        ok, why = rules.auto_send_decision(
            armed_once(), "wxid_contact_a", "联系人A", "在呢", brake_engaged=True, now=1500.0
        )
        self.assertFalse(ok)
        self.assertIn("制动", why)

    def test_group_never_auto(self) -> None:
        grant = armed_once(username="123@chatroom", name="某群")
        ok, why = rules.auto_send_decision(
            grant, "123@chatroom", "某群", "好的", now=1500.0
        )
        self.assertFalse(ok)
        self.assertIn("群", why)

    def test_red_line_downgrades_to_draft(self) -> None:
        ok, why = rules.auto_send_decision(
            armed_once(), "wxid_contact_a", "联系人A", "我明天一定来接你", now=1500.0
        )
        self.assertFalse(ok)
        self.assertIn("拍板", why)

    def test_wrong_target_is_refused(self) -> None:
        ok, why = rules.auto_send_decision(
            armed_once(), "wxid_other", "别人", "在呢", now=1500.0
        )
        self.assertFalse(ok)
        self.assertIn("只对", why)

    def test_happy_path_allowed(self) -> None:
        ok, why = rules.auto_send_decision(
            armed_once(), "wxid_contact_a", "联系人A", "刚忙完，怎么了", now=1500.0
        )
        self.assertTrue(ok)
        self.assertEqual(why, "")


class TestSender(unittest.TestCase):
    def setUp(self) -> None:
        self.brake = Brake()

    def test_dry_run_never_touches_keyboard(self) -> None:
        pct = FakePct()
        out = sender.AutoSender(object(), self.brake, pct).dispatch(
            armed_once(dry_run=True), "wxid_contact_a", "联系人A", "在呢"
        )
        self.assertTrue(out.allowed)
        self.assertFalse(out.sent)
        self.assertTrue(out.dry_run)
        self.assertEqual(pct.calls, [])          # 一次都没调用发送零件

    def test_real_send_passes_context_hash(self) -> None:
        pct = FakePct()
        out = sender.AutoSender(object(), self.brake, pct).dispatch(
            armed_once(), "wxid_contact_a", "联系人A", "在呢", expect_hash="HASH123", target="wechat"
        )
        self.assertTrue(out.sent)
        self.assertEqual(len(pct.calls), 1)
        self.assertEqual(pct.calls[0]["expect_hash"], "HASH123")
        self.assertEqual(pct.calls[0]["target"], "wechat")

    def test_no_target_means_no_send(self) -> None:
        pct = FakePct()
        out = sender.AutoSender(object(), self.brake, pct).dispatch(
            armed_once(), "wxid_contact_a", "联系人A", "在呢"
        )
        self.assertFalse(out.sent)
        self.assertIn("目标", out.reason)
        self.assertEqual(pct.calls, [])

    def test_stale_context_is_not_resent(self) -> None:
        pct = FakePct(error=StaleSendError("对话变了"))
        out = sender.AutoSender(object(), self.brake, pct).dispatch(
            armed_once(), "wxid_contact_a", "联系人A", "在呢", expect_hash="OLD", target="wechat"
        )
        self.assertFalse(out.sent)
        self.assertIn("拒发", out.reason)
        self.assertEqual(len(pct.calls), 1)      # 只试了一次，不许重发

    def test_send_error_is_reported(self) -> None:
        pct = FakePct(error=PctError("找不到窗口", 2))
        out = sender.AutoSender(object(), self.brake, pct).dispatch(
            armed_once(), "wxid_contact_a", "联系人A", "在呢", target="wechat"
        )
        self.assertFalse(out.sent)
        self.assertIn("发送失败", out.reason)

    def test_engaged_brake_stops_send(self) -> None:
        self.brake.engage("测试")
        pct = FakePct()
        out = sender.AutoSender(object(), self.brake, pct).dispatch(
            armed_once(), "wxid_contact_a", "联系人A", "在呢", target="wechat"
        )
        self.assertFalse(out.sent)
        self.assertIn("制动", out.reason)
        self.assertEqual(pct.calls, [])

    def test_empty_text_is_refused(self) -> None:
        out = sender.AutoSender(object(), self.brake, FakePct()).dispatch(
            armed_once(), "wxid_contact_a", "联系人A", "   "
        )
        self.assertFalse(out.allowed)
        self.assertIn("没有内容", out.reason)


class TestConsume(unittest.TestCase):
    def test_once_grant_dies_after_one_send(self) -> None:
        grant = armed_once()
        after = sender.consume(grant)
        self.assertEqual(after.remaining, 0)
        self.assertFalse(after.armed)
        self.assertEqual(after.mode, rules.Mode.ADVICE.value)
        # 再想发就发不出去了
        ok, _ = rules.auto_send_decision(after, "wxid_contact_a", "联系人A", "再说一句")
        self.assertFalse(ok)

    def test_standing_grant_just_counts_down(self) -> None:
        grant = armed_once(mode=rules.Mode.AUTO_STANDING.value, remaining=3, expires_at=0.0)
        after = sender.consume(grant)
        self.assertEqual(after.remaining, 2)
        self.assertTrue(after.armed)
        self.assertEqual(after.mode, rules.Mode.AUTO_STANDING.value)

    def test_unlimited_stays_unlimited(self) -> None:
        grant = armed_once(mode=rules.Mode.AUTO_STANDING.value, remaining=-1, expires_at=0.0)
        after = sender.consume(grant)
        self.assertEqual(after.remaining, -1)


class TestStopConditions(unittest.TestCase):
    def test_turn_limit(self) -> None:
        why = rules.stop_reason([], 15, 15, 0, 2, 0.0, 15)
        self.assertIn("上限", why)

    def test_short_replies(self) -> None:
        why = rules.stop_reason([], 3, 15, 2, 2, 0.0, 15)
        self.assertIn("很短", why)

    def test_silence(self) -> None:
        why = rules.stop_reason([], 1, 15, 0, 2, 20.0, 15)
        self.assertIn("静默", why)

    def test_all_short_incoming(self) -> None:
        why = rules.stop_reason(["嗯", "哦"], 1, 15, 0, 2, 0.0, 15)
        self.assertIn("实质", why)

    def test_keep_going(self) -> None:
        why = rules.stop_reason(["明天几点见"], 2, 15, 0, 2, 1.0, 15)
        self.assertEqual(why, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
