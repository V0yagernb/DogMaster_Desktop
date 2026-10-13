"""直连通道的自测：起一个假的 OpenAI 兼容端点，把协议这一层钉死。

为什么不用真接口测：真接口要 key、要花钱、还受网络波动影响，跑不绿的时候分不清
是代码错还是网络错。这里用本机假端点，能精确验四件事：
1. SSE 里的多段正文能按顺序拼起来，且每段都回调出去（流式的基础）；
2. 401/404/429 这些都会变成 LlmUnavailable（调用方据此回退兜底）；
3. 没 key / 没地址时直接拒绝，不发请求；
4. 「边收边解析」——收到的片段能立刻被拆成【判断】【首选】这些块。
"""

from __future__ import annotations

import json
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goutou_chat import llm  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    """只认 POST /chat/completions。行为由 server.mode 决定。"""

    protocol_version = "HTTP/1.0"

    def log_message(self, *args: Any) -> None:  # 别把测试输出弄脏
        return

    def do_POST(self) -> None:  # noqa: N802 - http.server 的命名
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body: Dict[str, Any] = json.loads(raw.decode("utf-8"))
        except ValueError:
            body = {}

        server = self.server  # type: ignore[assignment]
        mode = getattr(server, "mode", "stream")

        if mode == "unauthorized":
            payload = b'{"error":"bad key"}'
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(payload)
            return
        if mode == "missing":
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"not found")
            return

        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for piece in getattr(server, "pieces", ["可以"]):
                event = {"choices": [{"delta": {"content": piece}}]}
                self.wfile.write(("data: %s\n\n" % json.dumps(event, ensure_ascii=False)).encode("utf-8"))
                self.wfile.flush()
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
            return

        payload = json.dumps(
            {"choices": [{"message": {"content": "".join(getattr(server, "pieces", ["可以"]))}}]},
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class FakeLlm:
    """本机假端点。用 with 语句起停。"""

    def __init__(self, pieces: List[str], mode: str = "stream") -> None:
        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.server.pieces = pieces          # type: ignore[attr-defined]
        self.server.mode = mode              # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> "FakeLlm":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return "http://%s:%d" % (host, port)

    def settings(self, api_key: str = "test-key", model: str = "fake-model") -> llm.LlmSettings:
        return llm.LlmSettings(
            provider="custom", base_url=self.base_url, model=model, api_key=api_key, timeout=20
        )


class TestStreaming(unittest.TestCase):
    def test_pieces_are_collected_in_order(self) -> None:
        pieces = ["【判断】", "她在等你接情绪。", "\n【首选】", "那别硬撑了"]
        with FakeLlm(pieces) as fake:
            seen: List[str] = []
            text = llm.chat_stream(fake.settings(), [{"role": "user", "content": "x"}], on_delta=seen.append)
        self.assertEqual(text, "".join(pieces))
        self.assertEqual(seen, pieces)          # 每一段都被回调出去（流式的基础）

    def test_first_token_is_measured(self) -> None:
        with FakeLlm(["可", "以"]) as fake:
            ok, note, first_ms, total_ms = llm.test_connection(fake.settings(), timeout=20)
        self.assertTrue(ok, note)
        self.assertGreaterEqual(first_ms, 0)
        self.assertGreaterEqual(total_ms, first_ms)

    def test_unauthorized_becomes_unavailable(self) -> None:
        with FakeLlm(["x"], mode="unauthorized") as fake:
            with self.assertRaises(llm.LlmUnavailable):
                llm.chat_stream(fake.settings(api_key="bad"), [{"role": "user", "content": "x"}])

    def test_wrong_url_becomes_unavailable(self) -> None:
        with FakeLlm(["x"], mode="missing") as fake:
            with self.assertRaises(llm.LlmUnavailable):
                llm.chat_stream(fake.settings(), [{"role": "user", "content": "x"}])

    def test_connection_refused_is_unavailable(self) -> None:
        settings = llm.LlmSettings(provider="custom", base_url="http://127.0.0.1:1", model="m", api_key="k", timeout=5)
        with self.assertRaises(llm.LlmUnavailable):
            llm.chat_stream(settings, [{"role": "user", "content": "x"}])


class TestSettingsGuards(unittest.TestCase):
    def test_no_key_refuses_before_request(self) -> None:
        settings = llm.LlmSettings(provider="custom", base_url="http://127.0.0.1:1", model="m", api_key="")
        ok, why = settings.ready()
        self.assertFalse(ok)
        self.assertIn("key", why)
        with self.assertRaises(llm.LlmUnavailable):
            llm.chat_stream(settings, [])

    def test_preset_provider_fills_base_url(self) -> None:
        settings = llm.LlmSettings(provider="zhipu-cn", base_url="", model="glm-5.3-flash", api_key="k")
        self.assertTrue(settings.resolved_base_url().startswith("https://"))
        self.assertTrue(settings.resolved_base_url().endswith("/v4"))

    def test_custom_base_url_wins(self) -> None:
        settings = llm.LlmSettings(provider="zhipu-cn", base_url="http://127.0.0.1:9/v1/", model="m", api_key="k")
        self.assertEqual(settings.resolved_base_url(), "http://127.0.0.1:9/v1")


class TestStreamParsing(unittest.TestCase):
    """边收边解析：这是"【首选】一出现就能复制"的前提。"""

    def test_partial_text_parses_progressively(self) -> None:
        from goutou_chat import brain

        buffer = ""
        snapshots: List[Any] = []
        # 真实流式就是这样：开头几个字还没有任何分块标记，之后才逐个出现
        for piece in ["她在等你接情绪。", "\n【首选】那别硬撑了", "\n【备选】1. 过来坐会儿", "\n【风险】别追问"]:
            buffer += piece
            snapshots.append(brain.parse_advice(buffer))

        self.assertFalse(snapshots[0].structured)       # 还没有【】标记，不能硬拆
        self.assertEqual(snapshots[0].raw, "她在等你接情绪。")
        self.assertTrue(snapshots[1].structured)
        self.assertEqual(snapshots[1].first, "那别硬撑了")
        self.assertEqual(snapshots[1].judge, "")        # 【判断】还没出现，就是空的
        self.assertEqual(snapshots[-1].first, "那别硬撑了")
        self.assertIn("别追问", snapshots[-1].risk)


if __name__ == "__main__":
    unittest.main(verbosity=2)
