"""军师大脑的调度（ADR-0019）：直连优先，连不上自动回退到 DSH 子进程兜底。

两件事：

1. **通道**：直连（llm.py）快，能省掉 DSH 每次 1.87 秒的起进程开销，还能流式；
   直连依赖 key 与网络，一旦欠费/断网/地址变了就不能用，那时回退到 DSH 兜底。
   **回退必须如实说**（状态栏写明"这次走的是兜底通道"），不许让人以为它变快了。

2. **两段式**（0.3.1）：直连一次要等多久，几乎完全由**输入长度**决定——实测完整任务书
   （带 6651 字军师知识）首字 3.0 秒，瘦到 3349 字的极速任务书首字只要 1.35 秒。
   所以先跑"极速版"（只要【判断】【首选】）让人马上拿到一句能发的话，
   再跑"补充版"（要【备选】【风险】，这时才带军师知识）把退路补齐。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from . import brain as brain_mod
from . import config as cfgmod
from . import llm

DeltaFn = Optional[Callable[[str], None]]


@dataclass
class AdviseResult:
    """一次建议的产生过程与结果。"""

    text: str
    channel: str = "direct"          # direct = 直连；dsh = 兜底通道
    stage: str = "full"              # quick = 极速版（只有判断+首选）；full = 完整/补充
    elapsed_ms: int = 0
    first_token_ms: int = 0          # 首字用时；兜底通道拿不到，记 0
    fallback_reason: str = ""        # 非空表示这次是回退来的
    reasoning_chars: int = 0         # 这次模型先"想"了多少字（glm-5.x 会想很多）
    error: str = ""

    def describe(self, show_elapsed: bool = True) -> str:
        parts: List[str] = []
        if self.channel == "direct":
            parts.append("直连")
            if show_elapsed and self.first_token_ms:
                parts.append("首字 %.1f 秒" % (self.first_token_ms / 1000.0))
        else:
            parts.append("兜底通道（DSH）")
            if self.fallback_reason:
                parts.append("回退原因：%s" % self.fallback_reason)
        if show_elapsed and self.elapsed_ms:
            parts.append("共 %.1f 秒" % (self.elapsed_ms / 1000.0))
        return "｜".join(parts)


def make_settings(cfg: Optional[cfgmod.Config] = None) -> llm.LlmSettings:
    """把程序配置 + 单独存的 key 拼成直连设置。"""
    cfg = cfg or cfgmod.Config.load()
    return llm.LlmSettings(
        provider=str(getattr(cfg, "llm_provider", llm.DEFAULT_PROVIDER)),
        base_url=str(getattr(cfg, "llm_base_url", "") or ""),
        model=str(getattr(cfg, "llm_model", llm.DEFAULT_MODEL)),
        api_key=cfgmod.load_api_key(),
        temperature=float(getattr(cfg, "llm_temperature", 0.7)),
        timeout=int(getattr(cfg, "llm_timeout_seconds", 120)),
        reasoning_effort=str(getattr(cfg, "llm_reasoning_effort", "low") or ""),
    )


class Advisor:
    """一次建议的调度器。界面只跟它打交道，不用自己判断走哪条通道。"""

    def __init__(self, cfg: Optional[cfgmod.Config] = None) -> None:
        self.cfg = cfg or cfgmod.Config.load()
        self.settings = make_settings(self.cfg)

    # ── 通道自检 ────────────────────────────────────────────────────────
    def direct_ready(self) -> "tuple[bool, str]":
        return self.settings.ready()

    # ── 直连 ────────────────────────────────────────────────────────────
    def _direct(self, prompt: str, stage: str, on_delta: DeltaFn, on_reasoning: DeltaFn) -> AdviseResult:
        started = time.time()
        first_at: List[float] = []
        think = [0]

        def _delta(piece: str) -> None:
            if not first_at:
                first_at.append(time.time())
            if on_delta is not None:
                on_delta(piece)

        def _reasoning(piece: str) -> None:
            think[0] += len(piece)
            if on_reasoning is not None:
                on_reasoning(piece)

        text = llm.chat_stream(
            self.settings,
            [{"role": "user", "content": prompt}],
            on_delta=_delta,
            on_reasoning=_reasoning,
        )
        return AdviseResult(
            text=text,
            channel="direct",
            stage=stage,
            elapsed_ms=int((time.time() - started) * 1000),
            first_token_ms=int((first_at[0] - started) * 1000) if first_at else 0,
            reasoning_chars=think[0],
        )

    # ── 兜底 ────────────────────────────────────────────────────────────
    def _fallback(self, prompt_kwargs: Dict[str, Any], reason: str, on_note: DeltaFn) -> AdviseResult:
        if not bool(getattr(self.cfg, "fallback_to_dsh", True)):
            raise llm.LlmUnavailable(reason)
        if on_note is not None:
            on_note("直连不可用（%s）——已回退到兜底通道，这次会慢一些" % reason)
        started = time.time()
        engine = brain_mod.DshBrain(timeout=int(getattr(self.cfg, "dsh_timeout_seconds", 240)))
        text = engine.advice(**prompt_kwargs)
        return AdviseResult(
            text=text,
            channel="dsh",
            stage="full",
            elapsed_ms=int((time.time() - started) * 1000),
            fallback_reason=reason,
        )

    # ── 对外：直接送一段任务书（关系体检这种非"拟稿"任务用） ─────────────
    def ask_prompt(
        self,
        prompt: str,
        on_delta: DeltaFn = None,
        on_note: DeltaFn = None,
        on_reasoning: DeltaFn = None,
    ) -> AdviseResult:
        """跟 `advise()` 一样只是换了一份任务书——通道选择、兜底、如实说明全一致。"""
        body = (prompt or "").strip()
        if not body:
            raise llm.LlmUnavailable("任务书是空的")

        ready, why = self.direct_ready()
        if ready:
            try:
                result = self._direct(body, "full", on_delta, on_reasoning)
                if result.text.strip():
                    return result
                why = "直连返回了空内容"
            except llm.LlmError as exc:
                why = str(exc)

        if not bool(getattr(self.cfg, "fallback_to_dsh", True)):
            raise llm.LlmUnavailable(why)
        if on_note is not None:
            on_note("直连不可用（%s）——已回退到兜底通道，这次会慢一些" % why)
        started = time.time()
        engine = brain_mod.DshBrain(timeout=int(getattr(self.cfg, "dsh_timeout_seconds", 240)))
        text = engine.ask(body)
        return AdviseResult(
            text=text,
            channel="dsh",
            stage="full",
            elapsed_ms=int((time.time() - started) * 1000),
            fallback_reason=why,
        )

    # ── 对外：一次问全（不拆段） ────────────────────────────────────────
    def advise(
        self,
        prompt_kwargs: Dict[str, Any],
        on_delta: DeltaFn = None,
        on_note: DeltaFn = None,
        on_reasoning: DeltaFn = None,
    ) -> AdviseResult:
        ready, why = self.direct_ready()
        if ready:
            try:
                result = self._direct(
                    brain_mod.build_advice_prompt(**prompt_kwargs), "full", on_delta, on_reasoning
                )
                if result.text.strip():
                    return result
                why = "直连返回了空内容"
            except llm.LlmError as exc:
                why = str(exc)
        return self._fallback(prompt_kwargs, why, on_note)

    # ── 对外：两段式（推荐用法） ────────────────────────────────────────
    def advise_two_stage(
        self,
        quick_kwargs: Dict[str, Any],
        followup_kwargs: Dict[str, Any],
        fallback_kwargs: Dict[str, Any],
        on_quick: Optional[Callable[[AdviseResult], None]] = None,
        on_delta: DeltaFn = None,
        on_note: DeltaFn = None,
        on_reasoning: DeltaFn = None,
    ) -> AdviseResult:
        """第一段极速（判断+首选），第二段补充（备选+风险）。

        - `on_quick(result)`：第一段一完成就回调，界面据此马上把首选显示出来；
        - `on_delta`：只转发**第二段**的正文增量（第一段的结果走 on_quick，
          避免界面把两段文字混在一个缓冲区里）；
        - 兜底情形（直连不可用，或第一段就失败）：退回一次 DSH 出全部，并调一次 on_quick。
        """
        ready, why = self.direct_ready()
        if not ready:
            result = self._fallback(fallback_kwargs, why, on_note)
            if on_quick is not None:
                on_quick(result)
            return result

        try:
            quick = self._direct(brain_mod.build_quick_prompt(**quick_kwargs), "quick", None, on_reasoning)
            if not quick.text.strip():
                raise llm.LlmError("极速版返回了空内容")
        except llm.LlmError as exc:
            result = self._fallback(fallback_kwargs, str(exc), on_note)
            if on_quick is not None:
                on_quick(result)
            return result

        if on_quick is not None:
            on_quick(quick)
        if on_note is not None:
            on_note("首选已经出来了，正在补备选与风险…")

        first_choice = brain_mod.parse_advice(quick.text).first
        followup = dict(followup_kwargs)
        followup["first_choice"] = first_choice
        try:
            supplement = self._direct(
                brain_mod.build_followup_prompt(**followup), "full", on_delta, on_reasoning
            )
        except llm.LlmError as exc:
            # 第二段失败不算灾难：首选已经在人手里了，如实说一声就好
            if on_note is not None:
                on_note("补充分析没跑成（%s）；刚才那句首选仍然可用" % exc)
            quick.error = str(exc)
            return quick

        # 把两段合成一份：判断/首选来自第一段，备选/风险来自第二段
        parsed_quick = brain_mod.parse_advice(quick.text)
        parsed_supp = brain_mod.parse_advice(supplement.text)
        merged = "\n".join(
            part
            for part in (
                "【判断】" + parsed_quick.judge if parsed_quick.judge else "",
                "【首选】" + parsed_quick.first if parsed_quick.first else "",
                supplement.text if parsed_supp.structured else "",
            )
            if part
        )
        return AdviseResult(
            text=merged or (quick.text + "\n" + supplement.text),
            channel="direct",
            stage="full",
            elapsed_ms=quick.elapsed_ms + supplement.elapsed_ms,
            first_token_ms=quick.first_token_ms,
            reasoning_chars=quick.reasoning_chars + supplement.reasoning_chars,
        )
