"""0.4.0 两个新功能的自测：会话标记（标签 + 军师参与）与画像表单。

全在临时目录里跑（靠 GOUTOU_HOME 隔离），**不碰真实的 关系档案**。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goutou_chat import archive, config as cfgmod, rules  # noqa: E402


class TempHomeCase(unittest.TestCase):
    """把数据根指到临时目录，别污染真实档案。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("GOUTOU_HOME")
        os.environ["GOUTOU_HOME"] = self._tmp.name
        cfgmod.ensure_dirs()

    def tearDown(self) -> None:
        if self._old is None:
            os.environ.pop("GOUTOU_HOME", None)
        else:
            os.environ["GOUTOU_HOME"] = self._old
        self._tmp.cleanup()


class TestSessionMarks(TempHomeCase):
    def test_save_and_read_back(self) -> None:
        archive.save_session_mark("wxid_a", tag="flirt", advise=True)
        mark = archive.get_session_mark("wxid_a")
        self.assertEqual(mark.get("tag"), "flirt")
        self.assertIs(mark.get("advise"), True)

    def test_partial_update_keeps_other_field(self) -> None:
        archive.save_session_mark("wxid_b", tag="work")
        archive.save_session_mark("wxid_b", advise=False)     # 只改开关
        mark = archive.get_session_mark("wxid_b")
        self.assertEqual(mark.get("tag"), "work")             # 标签还在
        self.assertIs(mark.get("advise"), False)

    def test_unknown_session_is_empty(self) -> None:
        self.assertEqual(archive.get_session_mark("wxid_没有这个人"), {})

    def test_empty_username_refused(self) -> None:
        with self.assertRaises(ValueError):
            archive.save_session_mark("", tag="friend")

    def test_mark_file_lives_in_config_dir(self) -> None:
        path = archive.save_session_mark("wxid_c", tag="friend")
        self.assertEqual(path.name, "sessions.json")
        self.assertEqual(path.parent, cfgmod.archive_root() / "配置")

    def test_tag_labels(self) -> None:
        self.assertEqual(archive.tag_label("crush"), "恋爱")
        self.assertEqual(archive.tag_label(""), "未标记")
        self.assertEqual(archive.tag_label("不认识"), "未标记")


class TestAdviceAllowed(unittest.TestCase):
    def test_group_is_refused_by_default(self) -> None:
        ok, why = rules.advice_allowed("123@chatroom")
        self.assertFalse(ok)
        self.assertIn("群", why)

    def test_work_tag_is_refused(self) -> None:
        ok, why = rules.advice_allowed("wxid_a", {"tag": "work"})
        self.assertFalse(ok)
        self.assertIn("工作", why)

    def test_explicit_switch_beats_every_rule(self) -> None:
        # 群聊也能被显式打开（使用者说了算）
        ok, _ = rules.advice_allowed("123@chatroom", {"advise": True})
        self.assertTrue(ok)
        # 个人会话也能被显式关掉
        ok, why = rules.advice_allowed("wxid_a", {"advise": False})
        self.assertFalse(ok)
        self.assertIn("不参与", why)

    def test_plain_person_is_allowed(self) -> None:
        ok, why = rules.advice_allowed("wxid_a", {})
        self.assertTrue(ok)
        self.assertEqual(why, "")


class TestProfileForm(TempHomeCase):
    def test_write_then_read_roundtrip(self) -> None:
        fields = {
            "关系": "暧昧",
            "我叫他": "宝",
            "他叫我": "小A",
            "认识多久": "三年",
            "雷区": "不许提他前女友",
            "最近发生的事": "上周看了电影",
            "其他备注": "喜欢被叫宝",
        }
        path = archive.write_profile_form("测试对象", fields)
        self.assertTrue(path.exists())
        again = archive.read_profile_form("测试对象")
        for key, value in fields.items():
            self.assertEqual(again.get(key), value)

    def test_existing_content_is_preserved(self) -> None:
        """上游技能写的正文一个字都不能动。"""
        folder = cfgmod.contacts_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "老对象.md"
        path.write_text("# 联系人：老对象\n\n## 禁忌\n\n- 别提他妈妈\n", encoding="utf-8")

        archive.write_profile_form("老对象", {"关系": "朋友"})
        text = path.read_text(encoding="utf-8")
        self.assertIn("别提他妈妈", text)              # 原文还在
        self.assertIn("| 关系 | 朋友 |", text)         # 新内容也写进去了

    def test_second_save_replaces_block_not_appends(self) -> None:
        archive.write_profile_form("某人", {"关系": "朋友"})
        archive.write_profile_form("某人", {"关系": "恋爱"})
        text = (cfgmod.contacts_dir() / "某人.md").read_text(encoding="utf-8")
        self.assertEqual(text.count(archive.PROFILE_BEGIN), 1)
        self.assertEqual(text.count("| 关系 | 恋爱 |"), 1)
        self.assertNotIn("| 关系 | 朋友 |", text)

    def test_unfilled_returns_empty(self) -> None:
        self.assertEqual(archive.read_profile_form("从来没填过的人"), {})

    def test_contact_profile_sees_the_form(self) -> None:
        """表单写进画像文件后，送模型的那条路（contact_profile）能读到它。"""
        archive.write_profile_form("测试对象", {"关系": "暧昧", "雷区": "不许提工作"})
        text = archive.contact_profile("测试对象", 5000)
        self.assertIn("暧昧", text)
        self.assertIn("不许提工作", text)


class TestGrantStorage(TempHomeCase):
    """全自动授权要能存进 config.json 再读回来（关掉程序授权还在是有意的：
    过期由 expires_at 管，程序退出时也会主动收回）。"""

    def test_config_roundtrip(self) -> None:
        now = time.time()
        grant = rules.AutoGrant(
            mode=rules.Mode.AUTO_ONCE.value,
            username="wxid_a",
            name="某人",
            created_at=now,
            expires_at=now + 600,
            remaining=1,
            armed=True,
            dry_run=True,
        )
        cfg = cfgmod.Config.load()
        cfg.auto_grant = grant.to_dict()
        cfg.save()

        again = cfgmod.Config.load()
        parsed = rules.AutoGrant.from_dict(again.auto_grant)
        self.assertEqual(parsed.username, "wxid_a")
        self.assertEqual(parsed.mode, rules.Mode.AUTO_ONCE.value)
        self.assertTrue(parsed.armed)
        self.assertTrue(parsed.dry_run)
        active, _why = parsed.state()
        self.assertTrue(active)

    def test_missing_key_reads_as_advice(self) -> None:
        cfg = cfgmod.Config.load()
        self.assertEqual(cfg.auto_grant, {})
        parsed = rules.AutoGrant.from_dict(cfg.auto_grant)
        self.assertFalse(parsed.is_auto())
        self.assertFalse(parsed.armed)


if __name__ == "__main__":
    unittest.main(verbosity=2)
