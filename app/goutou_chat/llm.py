"""直连大模型：OpenAI 兼容的 /chat/completions + SSE 流式。

为什么要有这条路（ADR-0019 待补）：走 DSH 子进程时，实测一次 2.6~2.9 秒里有
**1.87 秒**花在"起 node 进程 + 装配 profile + 读凭据 + 建会话"上，模型真正往返
只要 0.6~0.9 秒。直连就是把那 1.87 秒整段去掉，并且能拿到流式（DSH headless
不支持流式）。

协议只认一种：POST {base_url}/chat/completions，带 `stream: true` 走 SSE。
本机在用的六家（智谱 / 商汤 / Kimi / 百炼 / OpenRouter / 自建网关）都兼容这个协议。

两条硬规矩：
1. **没拿到 key 或连不上，抛 LlmUnavailable**——调用方据此回退到兜底通道，
   绝不许假装成功、也不许静默降级成"没有建议"。
2. 模型吐出来的字**原样返回**，不在这里做任何改写（判断与话术是军师的事）。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

import requests

# 流式读取时每一行的超时（秒）：模型偶尔会卡住，卡住就认输，别把界面挂死
CHUNK_TIMEOUT = 60
# 建连超时
CONNECT_TIMEOUT = 15


@dataclass
class Provider:
    """一家提供方的预置信息。base_url 只是预填值，设置界面里可以改。"""

    key: str
    label: str
    base_url: str
    models: List[str] = field(default_factory=list)
    note: str = ""


# 预置清单：默认给最常用的几家，每家的模型按"快档在前"排列。
# 这些地址是各家公开的 OpenAI 兼容入口；如果哪天变了，在设置界面里改地址即可，
# 不用等我改代码重新打包（自定义那一条就是给这种情况留的）。
PROVIDERS: List[Provider] = [
    Provider(
        key="zhipu-cn",
        label="智谱 Z.ai（推荐）",
        base_url="https://open.bigmodel.cn/api/paas/v4",
        models=["glm-5.3-flash", "glm-4.7", "glm-5.2"],
        note="模型池默认档；flash 是快档，中文稳。",
    ),
    Provider(
        key="sensenova",
        label="商汤日日新 SenseNova",
        base_url="https://api.sensenova.cn/compatible-mode/v1",
        models=["sensenova-6.8-flash-lite", "glm-5.2", "kimi-k3"],
        note="flash-lite 是候选里最快的；积分制，注意周额度。",
    ),
    Provider(
        key="kimi-cn",
        label="月之暗面 Kimi",
        base_url="https://api.moonshot.cn/v1",
        models=["kimi-k2.6", "kimi-k2.7-code"],
        note="长上下文强，适合很长的聊天记录。",
    ),
    Provider(
        key="bailian",
        label="阿里百炼",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        models=["deepseek-v4.1-flash", "glm-5.3", "qwen3.8-max-0902"],
        note="模型多，可当备胎。",
    ),
    Provider(
        key="openrouter",
        label="OpenRouter（要过外网）",
        base_url="https://openrouter.ai/api/v1",
        models=[
            "nvidia/nemotron-3.5-lightning:free",
            "nvidia/nemotron-3-super-120b-a12b:free",
        ],
        note="免费池，但要走代理、有限速；只适合当备胎。",
    ),
    Provider(
        key="gateway",
        label="自建网关（地址自己填）",
        base_url="",
        models=["auto"],
        note="比如本机的 FreeLLMAPI 网关；地址栏留空时不可用。",
    ),
    Provider(
        key="custom",
        label="自定义（完全自己填）",
        base_url="",
        models=[],
        note="以后出了新模型，不用等我改代码。",
    ),
]

PROVIDER_MAP: Dict[str, Provider] = {p.key: p for p in PROVIDERS}

# 默认档：模型池的默认快档
DEFAULT_PROVIDER = "zhipu-cn"
DEFAULT_MODEL = "glm-5.3-flash"


class LlmError(RuntimeError):
    """直连通道出错。"""


class LlmUnavailable(LlmError):
    """连不上 / 没 key / 被拒——调用方据此回退到兜底通道。"""


@dataclass
class LlmSettings:
    """直连通道的全部可调项。api_key 单独存，不写进 config.json。"""

    provider: str = DEFAULT_PROVIDER
    base_url: str = ""          # 空 = 用 provider 预置地址
    model: str = DEFAULT_MODEL
    api_key: str = ""
    temperature: float = 0.7
    timeout: int = 120
    # 思考档位。实测（2026-10-13，智谱 glm-5.3-flash）：
    #   不传这个参数（服务端默认 max）→ 先想 18537 字，首字 136.7 秒；
    #   传 "low"                        → 想 482 字，首字 8.4 秒。
    # 这是本项目最要命的一个参数，所以默认就设成 low（宁可少想，也别让人干等）。
    # 空串 = 不发这个参数（给不认识它的提供方用）。
    reasoning_effort: str = "low"

    def resolved_base_url(self) -> str:
        if self.base_url.strip():
            return self.base_url.strip().rstrip("/")
        provider = PROVIDER_MAP.get(self.provider)
        return (provider.base_url if provider else "").rstrip("/")

    def ready(self) -> "Tuple[bool, str]":
        """能不能直连。给界面用的自检（不联网）。"""
        if not self.api_key.strip():
            return False, "还没填 API key"
        if not self.resolved_base_url():
            return False, "还没填接口地址"
        if not self.model.strip():
            return False, "还没填模型名"
        return True, "参数齐了"


def build_payload(
    settings: LlmSettings,
    messages: List[Dict[str, str]],
    stream: bool,
    with_effort: bool = True,
) -> Dict[str, object]:
    payload: Dict[str, object] = {
        "model": settings.model,
        "messages": messages,
        "stream": bool(stream),
        "temperature": float(settings.temperature),
    }
    if with_effort and settings.reasoning_effort:
        payload["reasoning_effort"] = settings.reasoning_effort
    return payload


def _headers(settings: LlmSettings) -> Dict[str, str]:
    return {
        "Authorization": "Bearer %s" % settings.api_key.strip(),
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }


def _endpoint(settings: LlmSettings) -> str:
    return settings.resolved_base_url() + "/chat/completions"


def _raise_for_status(status: int, body: str) -> None:
    """把 HTTP 错误翻译成人话。401/403 与网络错误都算"这条通道不可用"。"""
    snippet = (body or "").strip().replace("\n", " ")[:200]
    if status in (401, 403):
        raise LlmUnavailable("API key 被拒绝（HTTP %d）：%s" % (status, snippet))
    if status == 404:
        raise LlmUnavailable("接口地址不对（HTTP 404）：%s" % snippet)
    if status == 429:
        raise LlmUnavailable("被限速或欠费（HTTP 429）：%s" % snippet)
    if status >= 500:
        raise LlmUnavailable("对方服务器出错（HTTP %d）：%s" % (status, snippet))
    raise LlmError("请求被拒绝（HTTP %d）：%s" % (status, snippet))


def _iter_deltas(response: "requests.Response"):
    """把 SSE 里的每一段吐出来，带上类型。

    kind = "content"：正文（要显示给人看的）；
    kind = "reasoning"：思考过程（glm-5.x 这类"始终思考"的模型会先吐一大段）。
    思考过程**不能丢**：丢掉了界面上就是一片空白，看着像卡死（0.3.0 就是这么干的，
    实测 glm-5.3-flash 默认档要空白 136 秒）。
    """
    for raw in response.iter_lines(decode_unicode=False, chunk_size=1):
        if not raw:
            continue
        line = raw.decode("utf-8", "replace").strip()
        if not line or line.startswith(":"):
            continue
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        try:
            event = json.loads(data)
        except ValueError:
            continue
        choices = event.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta") or {}
        thinking = delta.get("reasoning_content")
        if thinking:
            yield "reasoning", thinking
        piece = delta.get("content")
        if piece:
            yield "content", piece


def _stream_once(
    settings: LlmSettings,
    messages: List[Dict[str, str]],
    on_delta: Optional[Callable[[str], None]],
    on_reasoning: Optional[Callable[[str], None]],
    timeout: Optional[int],
    with_effort: bool,
) -> str:
    collected: List[str] = []
    limit = timeout or settings.timeout
    try:
        response = requests.post(
            _endpoint(settings),
            headers=_headers(settings),
            json=build_payload(settings, messages, stream=True, with_effort=with_effort),
            stream=True,
            timeout=(CONNECT_TIMEOUT, limit),
        )
    except requests.RequestException as exc:
        raise LlmUnavailable("连不上 %s：%s" % (settings.resolved_base_url(), exc)) from exc

    with response:
        if response.status_code != 200:
            body = ""
            try:
                body = response.text
            except Exception:  # noqa: BLE001
                pass
            _raise_for_status(response.status_code, body)
        try:
            for kind, piece in _iter_deltas(response):
                if kind == "content":
                    collected.append(piece)
                    if on_delta is not None:
                        on_delta(piece)
                elif on_reasoning is not None:
                    on_reasoning(piece)
        except requests.RequestException as exc:
            if not collected:
                raise LlmUnavailable("读取流失败：%s" % exc) from exc
            collected.append("\n\n（连接中断，以上是已经收到的部分）")
    return "".join(collected).strip()


def chat_stream(
    settings: LlmSettings,
    messages: List[Dict[str, str]],
    on_delta: Optional[Callable[[str], None]] = None,
    on_reasoning: Optional[Callable[[str], None]] = None,
    timeout: Optional[int] = None,
) -> str:
    """流式问一次，边收边回调。返回完整正文。

    收流过程中断了，就把已经收到的部分返回（不丢字），并让调用方知道
    ——宁可给半段真话，也不要装成完整答案。
    """
    ok, why = settings.ready()
    if not ok:
        raise LlmUnavailable(why)

    try:
        return _stream_once(settings, messages, on_delta, on_reasoning, timeout, with_effort=True)
    except LlmError as exc:
        # 有的提供方不认 reasoning_effort（回 400）。这只是个可选参数，
        # 不该把整条通道判死：去掉它再试一次。
        if settings.reasoning_effort and "reasoning_effort" in str(exc):
            return _stream_once(settings, messages, on_delta, on_reasoning, timeout, with_effort=False)
        raise


def test_connection(settings: LlmSettings, timeout: int = 30) -> "Tuple[bool, str, int, int]":
    """真发一个最小请求。返回（通不通，说明，首字毫秒，总毫秒）。

    首字毫秒是这个通道真正的体感指标：它包含建连、鉴权、前缀处理。
    """
    ok, why = settings.ready()
    if not ok:
        return False, why, 0, 0

    started = time.time()
    first_at: List[float] = []

    def _mark(_piece: str) -> None:
        if not first_at:
            first_at.append(time.time())

    minimal = LlmSettings(
        provider=settings.provider,
        base_url=settings.base_url,
        model=settings.model,
        api_key=settings.api_key,
        temperature=0.0,
        timeout=timeout,
    )
    try:
        text = chat_stream(
            minimal,
            [{"role": "user", "content": "只回复两个字：可以"}],
            on_delta=_mark,
            timeout=timeout,
        )
    except LlmError as exc:
        return False, str(exc), 0, int((time.time() - started) * 1000)

    total_ms = int((time.time() - started) * 1000)
    first_ms = int((first_at[0] - started) * 1000) if first_at else total_ms
    return True, "通了，返回：%s" % (text[:20] or "(空)"), first_ms, total_ms
