"""关系体检面板（1.0.0）：先摆数字，再摆军师的解读。

顺序是刻意的：**统计在前、解读在后**。数字是本地算的，赖不掉；解读是模型给的，
可能偏。这样即使你不信模型那几句，也能自己从数字里看出个大概。

面板自己不读记录、不调模型——那些由主窗在后台做完再填进来（跟建议面板一个路子）。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..analysis import ANALYSIS_SECTIONS, ChatStats
from . import theme

# 界面上六块的标题（跟解析出来的 key 对应）
SECTION_TITLES = {
    "status": "【现状】",
    "balance": "【投入】",
    "good": "【好信号】",
    "bad": "【坏信号】",
    "risk": "【风险】",
    "next": "【接下来】",
}


class StatCard(QFrame):
    """一张小数字卡。"""

    def __init__(self, label: str, value: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("adviceCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 6, 10, 8)
        layout.setSpacing(2)
        self.value_label = QLabel(value)
        self.value_label.setStyleSheet("font-size:15px;font-weight:bold;")
        layout.addWidget(self.value_label)
        name = QLabel(label)
        name.setObjectName("hint")
        layout.addWidget(name)


class AnalysisPanel(QWidget):
    """整段聊天的关系体检。"""

    analyze_requested = pyqtSignal(int)      # 参数：读多少条记录

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._section_labels: Dict[str, QLabel] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.run_button = QPushButton("分析整段聊天")
        self.run_button.setObjectName("primary")
        self.run_button.setToolTip("把与这个人的历史记录拉出来做一次关系体检（只读，不发任何消息）")
        self.run_button.clicked.connect(self._on_clicked)
        tools.addWidget(self.run_button)

        tools.addWidget(QLabel("看最近"))
        self.limit_spin = QSpinBox()
        self.limit_spin.setRange(50, 5000)
        self.limit_spin.setSingleStep(100)
        self.limit_spin.setValue(800)
        self.limit_spin.setSuffix(" 条")
        self.limit_spin.setToolTip("统计用全部读到的记录；送去分析的是一份抽样（最近+最早+中间）")
        tools.addWidget(self.limit_spin)
        tools.addStretch(1)
        outer.addLayout(tools)

        self.note = QLabel("选一个人，然后点左边那个按钮。统计在本地算，不会发消息。")
        self.note.setObjectName("hint")
        self.note.setWordWrap(True)
        outer.addWidget(self.note)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        body = QVBoxLayout(holder)
        body.setContentsMargins(0, 0, 6, 0)
        body.setSpacing(10)

        self.cards_holder = QWidget()
        self.cards = QGridLayout(self.cards_holder)
        self.cards.setContentsMargins(0, 0, 0, 0)
        self.cards.setSpacing(6)
        body.addWidget(self.cards_holder)

        for _label, key in ANALYSIS_SECTIONS:
            card = QFrame()
            card.setObjectName("adviceCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 12)
            card_layout.setSpacing(6)
            title = QLabel(SECTION_TITLES.get(key, key))
            title.setObjectName("cardTitle")
            card_layout.addWidget(title)
            text = QLabel("")
            text.setWordWrap(True)
            text.setTextInteractionFlags(Qt.TextSelectableByMouse)
            card_layout.addWidget(text)
            self._section_labels[key] = text
            body.addWidget(card)

        body.addStretch(1)
        scroll.setWidget(holder)
        outer.addWidget(scroll, 1)
        self.clear()

    # ── 对外 ────────────────────────────────────────────────────────────
    def set_busy(self, busy: bool, note: str = "") -> None:
        self.run_button.setEnabled(not busy)
        self.run_button.setText("正在体检…" if busy else "分析整段聊天")
        if note:
            self.note.setText(note)

    def clear(self) -> None:
        self._fill_cards([])
        for text in self._section_labels.values():
            text.setText("")

    def set_stats(self, stats: ChatStats) -> None:
        self._fill_cards(stats.highlights())

    def set_sections(self, sections: Dict[str, str], note: str = "") -> None:
        raw = sections.get("raw", "")
        for key, label in self._section_labels.items():
            if raw:
                # 模型没按格式写：整段放在"现状"里，其余留空并说明，不假装拆好了
                label.setText(raw if key == "status" else "")
            else:
                label.setText(sections.get(key, ""))
        if note:
            self.note.setText(note)

    def limit(self) -> int:
        return int(self.limit_spin.value())

    # ── 内部 ────────────────────────────────────────────────────────────
    def _on_clicked(self) -> None:
        self.analyze_requested.emit(self.limit())

    def _fill_cards(self, pairs: List[Tuple[str, str]]) -> None:
        while self.cards.count():
            item = self.cards.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
        for index, (label, value) in enumerate(pairs or []):
            row, column = divmod(index, 3)
            self.cards.addWidget(StatCard(label, value), row, column)
        self.cards_holder.setVisible(bool(pairs))
