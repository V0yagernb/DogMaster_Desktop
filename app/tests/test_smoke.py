"""自测：不碰真实聊天、不发任何消息。

跑法（在 app\ 目录下）：
    python -m unittest discover -s tests -v
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goutou_chat import archive, brain, config as cfgmod, health, rules  # noqa: E402
from goutou_chat.brake import MOD_ALT, MOD_CONTROL, Brake, BrakeEngaged, parse_hotkey  # noqa: E402
from goutou_chat.pct import Pct  # noqa: E402
from goutou_chat.wda import WdaClient, WdaError  # noqa: E402


class TestRules(unittest.TestCase):
    def test_group_detection(self) -> None:
        self.assertTrue(rules.is_group("359359974@chatroom"))
        self.assertFalse(rules.is_group("wxid_abc"))

    def test_red_lines(self) -> None:
        self.assertIn("工资", rules.needs_human_decision("这个月工资还没发"))
        self.assertIn("来接", rules.needs_human_decision("你几点来接我"))
        self.assertEqual([], rules.needs_human_decision("今天天气不错"))

    def test_short_reply(self) -> None:
        self.assertTrue(rules.is_short_reply("哈哈"))
        self.assertTrue(rules.is_short_reply(""))
        self.assertFalse(rules.is_short_reply("明天下午三点见"))

    def test_may_auto_send_matrix(self) -> None:
        # 建议档：永远不发
        ok, _ = rules.may_auto_send(rules.Mode.ADVICE, "联系人A", "wxid_a", ["联系人A"], [])
        self.assertFalse(ok)
        # 群聊：进了白名单也不发（ADR-0004）
        ok, why = rules.may_auto_send(
            rules.Mode.AUTO_STANDING, "群聊A", "359@chatroom", ["群聊A"], []
        )
        self.assertFalse(ok)
        self.assertIn("群", why)
        # 单次档：点名即授权
        ok, _ = rules.may_auto_send(rules.Mode.AUTO_ONCE, "联系人B", "wxid_b", [], [])
        self.assertTrue(ok)
        # 常驻档 + 不在白名单：不发
        ok, _ = rules.may_auto_send(rules.Mode.AUTO_STANDING, "某人", "wxid_c", [], [])
        self.assertFalse(ok)
        # 常驻档 + 常驻白名单：发
        ok, _ = rules.may_auto_send(rules.Mode.AUTO_STANDING, "联系人B", "wxid_b", ["联系人B"], [])
        self.assertTrue(ok)


class TestBrake(unittest.TestCase):
    def test_engage_blocks_and_release(self) -> None:
        brake = Brake()
        brake.check("发送")  # 没制动时不抛
        brake.engage("测试")
        self.assertTrue(brake.engaged)
        with self.assertRaises(BrakeEngaged):
            brake.check("发送")
        brake.release()
        self.assertFalse(brake.engaged)
        brake.check("发送")

    def test_hotkey_parsing(self) -> None:
        mods, vk = parse_hotkey("Ctrl+Alt+Esc")
        self.assertEqual(mods, MOD_CONTROL | MOD_ALT)
        self.assertEqual(vk, 0x1B)
        mods2, vk2 = parse_hotkey("Ctrl+Shift+F12")
        self.assertEqual(vk2, 0x7B)


class TestPctParsing(unittest.TestCase):
    def test_parse_kv(self) -> None:
        kv = Pct.parse_kv("sent=True\nhash=abc123\nsize=879x613\nNOTE 别管我")
        self.assertEqual(kv.get("sent"), "True")
        self.assertEqual(kv.get("hash"), "abc123")
        self.assertNotIn("NOTE 别管我", kv)

    def test_vendor_copy_missing_is_reported(self) -> None:
        pct = Pct(script=Path("F:/goutoujunshi/vendor-frozen/scripts/不存在的.ps1"))
        ok, detail = pct.available()
        self.assertFalse(ok)
        self.assertIn("不存在", detail)


class TestBrainPrompt(unittest.TestCase):
    def test_transcript_format(self) -> None:
        rows = [
            {"createTime": 1757000000, "isSent": False, "senderDisplayName": "联系人A", "content": "吃了吗"},
            {"createTime": 1757000600, "isSent": True, "content": "刚吃完"},
        ]
        text = brain.format_transcript(rows)
        self.assertIn("联系人A：吃了吗", text)
        self.assertIn("我：刚吃完", text)

    def test_prompt_carries_hard_rules(self) -> None:
        prompt = brain.build_advice_prompt(
            contact_name="联系人A",
            transcript="我：在忙\n联系人A：哦",
            red_hits=["来接"],
        )
        for token in ("不许编造", "不许替本人承诺", "不许", "【首选】", "热键" if False else "【风险】"):
            self.assertIn(token, prompt)
        self.assertIn("来接", prompt)


class TestWdaGuards(unittest.TestCase):
    def test_banned_endpoint_is_blocked_locally(self) -> None:
        client = WdaClient(port=1, auto_start=False)
        with self.assertRaises(WdaError):
            client.get("/api/get_image_key")
        with self.assertRaises(WdaError):
            client.get("/api/cdn/whatever")

    def test_health_probe_never_raises(self) -> None:
        client = WdaClient(port=1, auto_start=False)
        self.assertIsNone(client.health(timeout=1))


class TestConfigAndArchive(unittest.TestCase):
    """写档案的测试一律在临时目录里跑。

    不隔离的话，跑一次自测就往真实的 `关系档案\复盘\` 里追加一段"自测复盘"，
    几次之后那个文件就没法看了（0.1.0 时代就是这么留下一篇"可删"的脏文件）。
    隔离靠 GOUTOU_HOME：config.project_home() 每次都读它。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old_home = os.environ.get("GOUTOU_HOME")
        os.environ["GOUTOU_HOME"] = self._tmp.name

    def tearDown(self) -> None:
        if self._old_home is None:
            os.environ.pop("GOUTOU_HOME", None)
        else:
            os.environ["GOUTOU_HOME"] = self._old_home
        self._tmp.cleanup()

    def test_config_roundtrip(self) -> None:
        cfg = cfgmod.Config.load()
        cfg.auto_exit_minutes = 15
        path = cfg.save()
        self.assertTrue(path.exists())
        again = cfgmod.Config.load()
        self.assertEqual(again.auto_exit_minutes, 15)

    def test_archive_paths_exist(self) -> None:
        cfgmod.ensure_dirs()
        for folder in (
            cfgmod.archive_root(),
            cfgmod.profile_dir(),
            cfgmod.contacts_dir(),
            cfgmod.memory_dir(),
            cfgmod.review_dir(),
        ):
            self.assertTrue(folder.exists(), str(folder))

    def test_review_written(self) -> None:
        path = archive.write_review("_自测_", "这是一条自测复盘，可删。")
        self.assertTrue(path.exists())
        self.assertIn("_自测_", path.name)


class TestHealth(unittest.TestCase):
    def test_health_runs_without_crashing(self) -> None:
        checks = health.run_health(cfgmod.Config.load())
        self.assertGreater(len(checks), 5)
        self.assertTrue(all(c.title for c in checks))
        report = health.format_health(checks)
        self.assertIn("体检结果", report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
