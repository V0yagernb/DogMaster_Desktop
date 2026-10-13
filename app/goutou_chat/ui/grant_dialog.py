"""开启全自动的授权对话框（0.5.1，ADR-0024）。

刻意做得**麻烦一点**：选子档 → 勾「我知道它会替我发消息」→ 输入确认词「我知道」。
这层摩擦不是多余的：它防的是"手滑一下就把自己的嘴交出去"。

两个默认值也是有意选的：
- **默认演练**（dry_run=True）：先跑一遍看它本来会发什么，你确认没问题再真发；
- **默认 30 分钟自动收回**：授权挂着没人管的时候不能一直有效。
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import rules

CONFIRM_WORD = "我知道"


class GrantDialog(QDialog):
    """全自动授权。点「开启」之后用 `result_grant()` 取那张授权。"""

    def __init__(
        self,
        idle_minutes: int = 30,
        contact_name: str = "",
        username: str = "",
        demo: bool = False,
        preselected: str = "",
        grant_minutes: int = 60,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._name = contact_name
        self._username = username
        self._demo = demo
        self._grant: Optional[rules.AutoGrant] = None

        self.setWindowTitle("开启全自动（它会替你发消息）")
        self.setMinimumWidth(560)

        outer = QVBoxLayout(self)
        outer.setSpacing(10)

        who = QLabel(
            "对象：%s" % (contact_name or "（先在左边选一个人）")
            if (contact_name or username)
            else "对象：还没选人——先在左边点一个会话"
        )
        who.setObjectName("cardTitle")
        outer.addWidget(who)

        if demo:
            note = QLabel("演示模式：这里开的授权只在演示里有效，不会碰任何真实窗口。")
            note.setObjectName("hint")
            note.setWordWrap(True)
            outer.addWidget(note)

        form_box = QGroupBox("授权范围")
        form = QFormLayout(form_box)
        form.setLabelAlignment(Qt.AlignRight)

        self.mode_box = QComboBox()
        for mode in (rules.Mode.AUTO_ONCE, rules.Mode.AUTO_STANDING, rules.Mode.AUTO_TIMED):
            self.mode_box.addItem(mode.label, mode.value)
        if preselected:                       # 从设置里选了某个子档，这里预选上
            position = self.mode_box.findData(preselected)
            if position >= 0:
                self.mode_box.setCurrentIndex(position)
        self.mode_box.currentIndexChanged.connect(lambda _i: self._refresh_mode_note())
        form.addRow("子档：", self.mode_box)

        # 授权时长：只有限时档用得上（到点自动收回）。下限放到 1 分钟——想快速验一次就设 1 分钟。
        self.timed_minutes_spin = QSpinBox()
        self.timed_minutes_spin.setRange(1, 1440)
        self.timed_minutes_spin.setValue(int(grant_minutes))
        self.timed_minutes_spin.setSuffix(" 分钟（到点自动收回）")
        self.timed_minutes_spin.setToolTip("1 分钟到 24 小时。想验一次到点收回，就设 1 分钟。")
        self.timed_row = QWidget()
        timed_layout = QHBoxLayout(self.timed_row)
        timed_layout.setContentsMargins(0, 0, 0, 0)
        timed_layout.addWidget(self.timed_minutes_spin)
        timed_layout.addStretch(1)
        form.addRow("授权时长：", self.timed_row)

        self.idle_spin = QSpinBox()
        self.idle_spin.setRange(1, 480)
        self.idle_spin.setValue(int(idle_minutes))
        self.idle_spin.setSuffix(" 分钟")
        self.idle_spin.setToolTip(
            "这段时间里没有任何自动动作，授权自动收回（ADR-0024 的护栏之一，不可关闭）。\n"
            "常驻档的有效时长上限是这里的 2 倍。"
        )
        form.addRow("没动作就收回：", self.idle_spin)

        self.mode_note = QLabel("")
        self.mode_note.setObjectName("hint")
        self.mode_note.setWordWrap(True)
        form.addRow("", self.mode_note)

        self.dry_run_box = QCheckBox("先演练：跑完整判定但不真的发（推荐先跑一次）")
        self.dry_run_box.setChecked(True)
        form.addRow("", self.dry_run_box)

        self.watch_box = QCheckBox("开着程序时盯着他的新消息：他发来，我就接")
        self.watch_box.setChecked(True)
        self.watch_box.setToolTip(
            "这是 ADR-0024 定下的触发方式。不勾就只在你手动点「演练一次」时才有动作；"
            "开起来之后，状态条上随时可以「暂停盯人」。"
        )
        form.addRow("", self.watch_box)
        outer.addWidget(form_box)

        guard_box = QGroupBox("不管怎么选，下面这些都不会变")
        guard_layout = QVBoxLayout(guard_box)
        for text in (
            "群聊永不进全自动（哪怕在这里开了也不发）",
            "要发的话里出现钱 / 承诺 / 见面这类词，当场降级成草稿，不发",
            "每条消息进键盘之前，都会再过一次紧急制动（Ctrl+Alt+Esc）",
            "发出的每一条都写进复盘，并在托盘弹一次提醒",
        ):
            item = QLabel("· " + text)
            item.setObjectName("hint")
            item.setWordWrap(True)
            guard_layout.addWidget(item)
        outer.addWidget(guard_box)

        confirm_box = QGroupBox("确认")
        confirm_layout = QVBoxLayout(confirm_box)
        self.aware_box = QCheckBox("我知道它会替我把消息发出去，出事我认")
        confirm_layout.addWidget(self.aware_box)
        self.word_edit = QLineEdit()
        self.word_edit.setPlaceholderText("把「%s」两个字打一遍" % CONFIRM_WORD)
        self.word_edit.textChanged.connect(self._refresh_ok)
        confirm_layout.addWidget(self.word_edit)
        outer.addWidget(confirm_box)

        self.status = QLabel("")
        self.status.setObjectName("hint")
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.ok_button = QPushButton("开启")
        self.ok_button.setObjectName("primary")
        self.ok_button.setEnabled(False)
        self.ok_button.clicked.connect(self._on_accept)
        buttons.addWidget(self.ok_button)
        outer.addLayout(buttons)

        self.aware_box.toggled.connect(self._refresh_ok)
        self._refresh_ok()
        self._refresh_mode_note()

    # ── 三个子档的说明与可见性 ──────────────────────────────────────────
    def _refresh_mode_note(self) -> None:
        """三个档的真正区别就在这里：单次发完即失效、常驻到叫停、限时到点收回。"""
        mode = str(self.mode_box.currentData() or "")
        is_timed = mode == rules.Mode.AUTO_TIMED.value
        self.timed_row.setVisible(is_timed)
        self._sync_label_visibility("授权时长：", is_timed)

        if mode == rules.Mode.AUTO_ONCE.value:
            self.mode_note.setText(
                "单次：发出任意一条就失效，立刻回到建议档。"
            )
        elif mode == rules.Mode.AUTO_STANDING.value:
            self.mode_note.setText(
                "常驻：持续自动回、条数不限，直到你点「立即收回」、触发停手条件"
                "（对方连着冷淡 / 连着没什么实质内容 / 轮数到上限），或者「没动作就收回」到点。"
            )
        else:
            self.mode_note.setText(
                "限时：跟常驻一样持续自动回，但到了上面那个「授权时长」就自动收回，"
                "不管当时聊到哪。"
            )

    def _sync_label_visibility(self, text: str, visible: bool) -> None:
        """QFormLayout 的行标签是独立控件，隐藏字段时要连它一起隐藏。"""
        for child in self.findChildren(QLabel):
            if child.text() == text:
                child.setVisible(visible)
                return

    # ── 校验 ────────────────────────────────────────────────────────────
    def _refresh_ok(self) -> None:
        if not (self._name or self._username):
            self.ok_button.setEnabled(False)
            self.status.setText("先在左边选一个人。")
            return
        if not self.aware_box.isChecked():
            self.ok_button.setEnabled(False)
            self.status.setText("先把上面那个勾选框勾上。")
            return
        if self.word_edit.text().strip() != CONFIRM_WORD:
            self.ok_button.setEnabled(False)
            self.status.setText("再把「%s」两个字打一遍。" % CONFIRM_WORD)
            return
        self.ok_button.setEnabled(True)
        self.status.setText("可以开了。开启后状态条会一直显示它还有多久、还能发几条。")

    def _on_accept(self) -> None:
        mode = str(self.mode_box.currentData() or rules.Mode.AUTO_ONCE.value)
        now = time.time()
        idle_seconds = float(self.idle_spin.value()) * 60.0
        if mode == rules.Mode.AUTO_TIMED.value:
            # 限时档：到点收回（本人设的时长）
            span = float(self.timed_minutes_spin.value()) * 60.0
        elif mode == rules.Mode.AUTO_STANDING.value:
            # 常驻档：按 ADR-0002 是"持续到叫停"，但 ADR-0024 的静默护栏仍然给它一个上限
            # （两倍静默时间），免得一张授权挂一整夜。
            span = idle_seconds * 2.0
        else:
            span = idle_seconds
        self._grant = rules.AutoGrant(
            mode=mode,
            username=self._username,
            name=self._name,
            created_at=now,
            expires_at=now + span,
            remaining=1 if mode == rules.Mode.AUTO_ONCE.value else -1,
            armed=True,
            dry_run=bool(self.dry_run_box.isChecked()),
            trigger="on_reply",
        )
        self.accept()

    def timed_minutes(self) -> int:
        return int(self.timed_minutes_spin.value())

    def result_grant(self) -> Optional[rules.AutoGrant]:
        return self._grant
