"""军师建议面板：把一次输出拆成 判断 / 首选 / 备选 / 风险 四块展示。

第一版把军师的整段文字倒进一个 QTextEdit，看着像日志。这里拆成卡片：
- 【首选】单独高亮，因为那行是唯一会被复制走的东西；
- 【备选】每条给一个复制按钮，省得在长文本里手动选；
- 命中红线时，最上面插一条醒目提示（"这件事要你自己拍板"）。

0.3.1 起支持**两段式**：
1. 极速版先回来（实测 1.35 秒）→ 立刻把【判断】【首选】显示出来，其余两块写"正在补…"；
2. 补充版接着回来（带军师知识，慢一些）→ 流式填【备选】【风险】。

还有一条**思考流**：glm-5.x 这类"始终思考"的模型会先吐一大段思考内容。这一路以前被丢掉了，
结果模型想的那 130 秒里界面一片空白、看着像卡死。现在把它显示的"正在想…（已 N 字）"。

面板自己不起后台任务、更不发送。生成由主窗负责，这里只负责显示。
"""

from __future__ import annotations

from typing import List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from .. import brain as brain_mod
from ..brain import Advice
from . import theme

# 思考流只显示尾巴这么多个字（看个动静就够了，不必把整段内心独白铺出来）
REASONING_TAIL = 90


class AdviceCard(QFrame):
    """一张建议卡片。highlight=True 的用于【首选】。"""

    def __init__(self, title: str, highlight: bool = False, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("firstCard" if highlight else "adviceCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("cardTitle")
        head.addWidget(self.title_label)
        head.addStretch(1)
        self.action_button = QPushButton("复制")
        self.action_button.setObjectName("link")
        self.action_button.setVisible(False)
        head.addWidget(self.action_button)
        layout.addLayout(head)

        self.body = QLabel("")
        self.body.setWordWrap(True)
        self.body.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if highlight:
            self.body.setObjectName("firstText")
        layout.addWidget(self.body)

    def set_text(self, text: str) -> None:
        self.body.setText(text or "")
        self.body.setVisible(bool(text))

    def text(self) -> str:
        return self.body.text()


class AdvicePanel(QWidget):
    """整个右侧「军师」页。"""

    advice_requested = pyqtSignal()
    copied = pyqtSignal(str)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._advice = Advice()
        self._stage1 = ""        # 极速版的原文
        self._stage2 = ""        # 补充版的原文（流式累积）
        self._reasoning = ""     # 模型思考过程（只留尾巴显示）

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        # 工具行
        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.generate_button = QPushButton("让军师看一眼")
        self.generate_button.setObjectName("primary")
        self.generate_button.clicked.connect(self.advice_requested.emit)
        tools.addWidget(self.generate_button)

        self.copy_first_button = QPushButton("复制首选话术")
        self.copy_first_button.clicked.connect(self._copy_first)
        tools.addWidget(self.copy_first_button)

        self.copy_all_button = QPushButton("复制全文")
        self.copy_all_button.clicked.connect(self._copy_all)
        tools.addWidget(self.copy_all_button)
        tools.addStretch(1)
        outer.addLayout(tools)

        # 状态 / 红线提示
        self.note_label = QLabel("军师只出字，不替你发送。选一个人，再点左边那个按钮。")
        self.note_label.setObjectName("hint")
        self.note_label.setWordWrap(True)
        outer.addWidget(self.note_label)

        # 思考流（模型"想"的过程，默认藏着，一动起来才显示）
        self.reasoning_label = QLabel("")
        self.reasoning_label.setObjectName("hint")
        self.reasoning_label.setWordWrap(True)
        self.reasoning_label.setVisible(False)
        outer.addWidget(self.reasoning_label)

        # 卡片区（可滚动）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        cards = QVBoxLayout(holder)
        cards.setContentsMargins(0, 0, 6, 0)
        cards.setSpacing(10)

        self.judge_card = AdviceCard("【判断】")
        self.first_card = AdviceCard("【首选】（复制走，你自己发）", highlight=True)
        self.first_card.action_button.setVisible(True)
        self.first_card.action_button.clicked.connect(self._copy_first)
        self.options_card = AdviceCard("【备选】")
        self.risk_card = AdviceCard("【风险】")

        for card in (self.judge_card, self.first_card, self.options_card, self.risk_card):
            cards.addWidget(card)
        cards.addStretch(1)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)

        self.set_advice(Advice())

    # ── 对外接口 ────────────────────────────────────────────────────────
    def set_busy(self, busy: bool, note: str = "") -> None:
        self.generate_button.setEnabled(not busy)
        self.generate_button.setText("军师正在想…" if busy else "让军师看一眼")
        if busy:
            self.note_label.setText(note or "正在问军师。")
        elif note:
            self.note_label.setText(note)

    def set_note(self, text: str) -> None:
        self.note_label.setText(text)

    def set_available(self, available: bool, reason: str = "") -> None:
        """这个会话归不归军师管。不归就灰掉按钮并写明为什么（不藏、不假装）。"""
        self.generate_button.setEnabled(bool(available))
        if not available:
            self.note_label.setText(reason or "这个会话不给建议。")

    def set_advice(self, advice: Advice, note: str = "", streaming: bool = False) -> None:
        """一次性放一份结果（非流式路径与终态用它）。"""
        self._advice = advice
        if not advice.raw and not advice.structured:
            self.judge_card.set_text("正在想…" if streaming else "还没有建议。")
            self.first_card.set_text("")
            self.options_card.set_text("")
            self.risk_card.set_text("")
            return

        if advice.structured:
            self.judge_card.set_text(advice.judge or ("正在写…" if streaming else "（这次没写判断）"))
            self.first_card.set_text(advice.first or ("" if streaming else "（这次没给首选）"))
            if advice.options:
                self.options_card.set_text(
                    "\n".join("%d. %s" % (i + 1, text) for i, text in enumerate(advice.options))
                )
            else:
                self.options_card.set_text("" if streaming else "（这次没给备选）")
            self.risk_card.set_text(advice.risk or ("" if streaming else "（这次没写风险）"))
        elif streaming:
            self.judge_card.set_text(advice.raw)
            self.first_card.set_text("")
            self.options_card.set_text("")
            self.risk_card.set_text("")
        else:
            self.judge_card.set_text("军师这次没按格式返回，原文如下：\n\n" + advice.raw)
            self.first_card.set_text("")
            self.options_card.set_text("")
            self.risk_card.set_text("")
        if note:
            self.note_label.setText(note)

    def current_advice(self) -> Advice:
        return self._advice

    # ── 两段式 ──────────────────────────────────────────────────────────
    def begin_stream(self, note: str = "军师正在想…（直连，边想边出）") -> None:
        self._stage1 = ""
        self._stage2 = ""
        self._reasoning = ""
        self.reasoning_label.setVisible(False)
        self.reasoning_label.setText("")
        self.set_busy(True, note)
        self.set_advice(Advice(), note=note, streaming=True)

    def push_reasoning(self, piece: str) -> None:
        """模型思考中：显示"正在想…（已 N 字）+ 尾巴"，让等待看得见。"""
        if not piece:
            return
        self._reasoning += piece
        tail = self._reasoning[-REASONING_TAIL:].replace("\n", " ")
        self.reasoning_label.setVisible(True)
        self.reasoning_label.setText(
            "军师正在想…（已 %d 字）%s" % (len(self._reasoning), tail)
        )

    def set_quick(self, advice: Advice, note: str = "") -> None:
        """第一段（极速版）回来了：立刻把判断与首选定格，其余两块标成"正在补"。"""
        self._stage1 = advice.raw
        self._stage2 = ""
        self._advice = advice
        self.judge_card.set_text(advice.judge or "（这次没写判断）")
        self.first_card.set_text(advice.first or "（这次没给首选）")
        self.options_card.set_text("正在补…")
        self.risk_card.set_text("正在补…")
        self.reasoning_label.setVisible(False)
        if note:
            self.note_label.setText(note)

    def push_delta(self, piece: str) -> None:
        """第二段（补充版）的流式增量：只更新备选与风险，不去动首选。"""
        if not piece:
            return
        self._stage2 += piece
        supplement = brain_mod.parse_advice(self._stage2)
        if supplement.options:
            self.options_card.set_text(
                "\n".join("%d. %s" % (i + 1, t) for i, t in enumerate(supplement.options))
            )
        else:
            self.options_card.set_text("正在补…")
        self.risk_card.set_text(supplement.risk or "正在补…")

    def end_stream(self, advice: Advice, note: str = "") -> None:
        """全部完成：按合并后的结果铺满四块。"""
        self._reasoning = ""
        self.reasoning_label.setVisible(False)
        self.set_busy(False, note)
        self.set_advice(advice, note=note)

    def streamed_text(self) -> str:
        return self._stage1 + ("\n" + self._stage2 if self._stage2 else "")

    # ── 复制 ────────────────────────────────────────────────────────────
    def _copy_text(self, text: str, what: str) -> None:
        text = (text or "").strip()
        if not text:
            self.copied.emit("没有可复制的内容")
            return
        QApplication.clipboard().setText(text)
        self.copied.emit("已复制%s到剪贴板（自己发，程序不替你发）" % what)

    def _copy_first(self) -> None:
        self._copy_text(self.first_card.text(), "首选话术")

    def _copy_all(self) -> None:
        text = self._advice.as_transcript() or self.streamed_text()
        self._copy_text(text, "全文")
