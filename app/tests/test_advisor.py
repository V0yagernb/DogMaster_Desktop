"""调度层的自测：直连优先、连不上自动兜底、兜底时必须如实说。

用 test_llm 里那个本机假端点当"直连"，用打桩的 DshBrain 当"兜底通道"——
这样一次都不发真请求、也不起 node 进程。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goutou_chat import advisor as advisor_mod  # noqa: E402
from goutou_chat import brain as brain_mod  # noqa: E402
from goutou_chat import config as cfgmod  # noqa: E402
from goutou_chat import llm  # noqa: E402
from test_llm import FakeLlm  # noqa: E402

PROMPT = {"contact_name": "测试对象", "transcript": "我：在忙\n对方：哦"}


def _config(**overrides):
    cfg = cfgmod.Config()
    cfg.fallback_to_dsh = True
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


class TestAdvisorDirect(unittest.TestCase):
    def test_direct_wins_and_records_timings(self) -> None:
        with FakeLlm(["【判断】", "她在等你。", "\n【首选】", "那别硬撑了"]) as fake:
            adv = advisor_mod.Advisor(_config())
            adv.settings = fake.settings()
            chunks = []
            notes = []
            result = adv.advise(PROMPT, on_delta=chunks.append, on_note=notes.append)

        self.assertEqual(result.channel, "direct")
        self.assertIn("那别硬撑了", result.text)
        self.assertEqual(len(chunks), 4)          # 流式：四段都发出来了
        self.assertEqual(notes, [])               # 直连成功就不该有回退提示
        self.assertEqual(result.fallback_reason, "")

    def test_result_describe_mentions_channel(self) -> None:
        direct = advisor_mod.AdviseResult(text="x", channel="direct", elapsed_ms=1200, first_token_ms=800)
        self.assertIn("直连", direct.describe())
        self.assertIn("首字 0.8 秒", direct.describe())
        fallback = advisor_mod.AdviseResult(text="x", channel="dsh", elapsed_ms=3000, fallback_reason="没填 key")
        self.assertIn("兜底", fallback.describe())
        self.assertIn("没填 key", fallback.describe())


class TestAdvisorFallback(unittest.TestCase):
    def test_direct_unavailable_falls_back_and_says_so(self) -> None:
        adv = advisor_mod.Advisor(_config())
        adv.settings = llm.LlmSettings(
            provider="custom", base_url="http://127.0.0.1:1", model="m", api_key="k", timeout=3
        )
        notes = []
        with mock.patch.object(brain_mod.DshBrain, "advice", return_value="【判断】兜底出来的"):
            result = adv.advise(PROMPT, on_delta=lambda _p: None, on_note=notes.append)

        self.assertEqual(result.channel, "dsh")
        self.assertTrue(result.fallback_reason)
        self.assertIn("兜底出来的", result.text)
        self.assertTrue(notes, "回退必须被说出来，不许悄悄降级")
        self.assertIn("兜底", notes[0])

    def test_no_key_also_falls_back(self) -> None:
        adv = advisor_mod.Advisor(_config())
        adv.settings = llm.LlmSettings(provider="custom", base_url="http://x", model="m", api_key="")
        with mock.patch.object(brain_mod.DshBrain, "advice", return_value="兜底"):
            result = adv.advise(PROMPT)
        self.assertEqual(result.channel, "dsh")
        self.assertIn("key", result.fallback_reason)

    def test_fallback_disabled_raises(self) -> None:
        adv = advisor_mod.Advisor(_config(fallback_to_dsh=False))
        adv.settings = llm.LlmSettings(provider="custom", base_url="http://x", model="m", api_key="")
        with self.assertRaises(llm.LlmUnavailable):
            adv.advise(PROMPT)


if __name__ == "__main__":
    unittest.main(verbosity=2)
