"""界面层自测：纯函数为主，加一个主窗冒烟。

跑法（在 app\\ 目录下）：
    python -m unittest discover -s tests -v

两点注意：
1. 界面测试需要一个真实的 Qt 平台插件（本机的 offscreen 插件字体库是空的，
   用它画出来的界面一个字都没有），所以这里用 windows 平台 + WA_DontShowOnScreen：
   排版与渲染都是真的，但窗口不上屏，不打扰正在用电脑的人。
2. 起不了 QApplication 就跳过界面部分，不让整套测试红掉——环境问题不该算在代码头上。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goutou_chat import brain  # noqa: E402
from goutou_chat.ui.chat_view import newest_stamp_of, prepare_messages  # noqa: E402
from goutou_chat.ui.session_list import _format_time, prepare_sessions  # noqa: E402


class TestAdviceParsing(unittest.TestCase):
    def test_standard_output(self) -> None:
        text = (
            "【判断】她在等你接情绪。\n"
            "【首选】那别硬撑了，跟我说说\n"
            "【备选】1. 过来坐会儿\n- 先吃点东西\n"
            "【风险】别追问原因。"
        )
        advice = brain.parse_advice(text)
        self.assertTrue(advice.structured)
        self.assertIn("等你接情绪", advice.judge)
        self.assertEqual(advice.first, "那别硬撑了，跟我说说")
        self.assertEqual(advice.options, ["过来坐会儿", "先吃点东西"])
        self.assertIn("别追问", advice.risk)

    def test_unstructured_is_not_faked(self) -> None:
        """模型没按格式写：不许假装拆好了，全文进 raw。"""
        advice = brain.parse_advice("我建议你直接问她。")
        self.assertFalse(advice.structured)
        self.assertEqual(advice.raw, "我建议你直接问她。")
        self.assertEqual(advice.first, "")

    def test_empty(self) -> None:
        advice = brain.parse_advice("")
        self.assertFalse(advice.structured)
        self.assertEqual(advice.raw, "")

    def test_roundtrip(self) -> None:
        text = "【判断】A\n【首选】B\n【备选】C\n【风险】D"
        advice = brain.parse_advice(text)
        restored = advice.as_transcript()
        self.assertIn("【首选】B", restored)
        self.assertIn("【风险】D", restored)


class TestMessageRows(unittest.TestCase):
    def test_time_separator_and_sides(self) -> None:
        rows = prepare_messages(
            [
                {"createTime": 1757000000, "isSent": False, "senderDisplayName": "联系人A", "content": "在吗"},
                {"createTime": 1757000060, "isSent": True, "content": "在"},
                {"createTime": 1757000400, "isSent": False, "senderDisplayName": "联系人A", "content": "好"},
            ]
        )
        self.assertTrue(rows[0]["_show_time"])          # 第一条一定有
        self.assertFalse(rows[1]["_show_time"])         # 一分钟内不重复标
        self.assertTrue(rows[2]["_show_time"])          # 超过 5 分钟再标一次
        self.assertFalse(rows[0]["_is_me"])
        self.assertTrue(rows[1]["_is_me"])
        self.assertEqual(rows[0]["_who"], "联系人A")
        self.assertEqual(rows[1]["_who"], "我")

    def test_non_text_is_placeholder(self) -> None:
        rows = prepare_messages([{"createTime": 1757000000, "isSent": False, "content": "", "renderType": "图片"}])
        self.assertTrue(rows[0]["_placeholder"])
        self.assertEqual(rows[0]["_text"], "[图片]")

    def test_missing_time_does_not_crash(self) -> None:
        rows = prepare_messages([{"isSent": True, "content": "没时间戳"}])
        self.assertEqual(rows[0]["_ts"], 0)
        self.assertFalse(rows[0]["_show_time"])


class TestSessionRows(unittest.TestCase):
    def test_unread_first_then_time(self) -> None:
        rows = prepare_sessions(
            [
                {"name": "旧", "username": "wxid_1", "lastTime": 1000},
                {"name": "新", "username": "wxid_2", "lastTime": 2000},
                {"name": "有人找", "username": "wxid_3", "unreadCount": 2, "lastTime": 1},
            ]
        )
        self.assertEqual(rows[0]["_name"], "有人找")   # 有未读的排最前
        self.assertEqual(rows[1]["_name"], "新")
        self.assertEqual(rows[2]["_name"], "旧")
        self.assertEqual(rows[0]["_unread"], 2)

    def test_group_flag_from_username(self) -> None:
        rows = prepare_sessions([{"name": "群聊A", "username": "123@chatroom"}])
        self.assertTrue(rows[0]["_group"])

    def test_preview_newlines_flattened(self) -> None:
        rows = prepare_sessions([{"name": "A", "username": "wxid_a", "lastMessage": "第一行\n第二行"}])
        self.assertEqual(rows[0]["_preview"], "第一行 第二行")

    def test_time_label_rules(self) -> None:
        from datetime import datetime, timedelta

        now = datetime(2026, 10, 13, 20, 0, 0)
        today = int(now.replace(hour=9, minute=5).timestamp())
        yesterday = int((now - timedelta(days=1)).replace(hour=22).timestamp())
        older = int((now - timedelta(days=5)).timestamp())
        self.assertEqual(_format_time(today), "09:05")
        self.assertEqual(_format_time(yesterday), "昨天")
        self.assertTrue(_format_time(older).startswith("10-"))


class TestAutoRefreshProbe(unittest.TestCase):
    """每秒自动刷新靠"最新时间戳变没变"来决定要不要重载。"""

    def test_newest_stamp_prefers_ts(self) -> None:
        rows = prepare_messages(
            [
                {"createTime": 1757000000, "isSent": False, "content": "早"},
                {"createTime": 1757000200, "isSent": True, "content": "晚"},
            ]
        )
        self.assertEqual(newest_stamp_of(rows), 1757000200)

    def test_newest_stamp_empty(self) -> None:
        self.assertEqual(newest_stamp_of([]), 0)
        self.assertEqual(newest_stamp_of(None), 0)

    def test_newest_stamp_tolerates_junk(self) -> None:
        self.assertEqual(newest_stamp_of([{"createTime": "不是数字"}, None, {}]), 0)

    def test_detects_a_new_message(self) -> None:
        old = prepare_messages([{"createTime": 1000, "isSent": False, "content": "在吗"}])
        new = prepare_messages(
            [
                {"createTime": 1000, "isSent": False, "content": "在吗"},
                {"createTime": 1060, "isSent": False, "content": "在忙吗"},
            ]
        )
        self.assertGreater(newest_stamp_of(new), newest_stamp_of(old))

    def test_same_messages_do_not_trigger_reload(self) -> None:
        rows = prepare_messages([{"createTime": 1000, "isSent": False, "content": "在吗"}])
        self.assertEqual(newest_stamp_of(rows), newest_stamp_of(rows))


class TestMainWindowSmoke(unittest.TestCase):
    """主窗能不能建起来、能不能干净收尾（线程收不干净会崩，退出码 0xC0000409）。"""

    app = None
    skipped_reason = ""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            from PyQt5.QtCore import Qt
            from PyQt5.QtWidgets import QApplication, QSystemTrayIcon

            cls.app = QApplication.instance() or QApplication([])
            cls.Qt = Qt
            cls.QSystemTrayIcon = QSystemTrayIcon
        except Exception as exc:  # noqa: BLE001
            cls.skipped_reason = "起不了 QApplication：%s" % exc

    def setUp(self) -> None:
        if self.app is None:
            self.skipTest(self.skipped_reason)

    def test_demo_window_builds_and_closes(self) -> None:
        from goutou_chat.ui import theme
        from goutou_chat.ui.main_window import MainWindow

        # 不碰真实托盘，也不把窗口显示到屏幕上
        original = self.QSystemTrayIcon.isSystemTrayAvailable
        self.QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: False)
        try:
            self.app.setStyleSheet(theme.app_style())
            window = MainWindow(demo=True)
            window.setAttribute(self.Qt.WA_DontShowOnScreen, True)
            window.show()
            for _ in range(30):
                self.app.processEvents()
            window._on_sessions_done([])
            window.generate_advice()
            for _ in range(60):
                self.app.processEvents()
            self.assertIn("演示数据", window.windowTitle())
            window.shutdown()
            window.close()
        finally:
            self.QSystemTrayIcon.isSystemTrayAvailable = original  # type: ignore[assignment]


if __name__ == "__main__":
    unittest.main(verbosity=2)
