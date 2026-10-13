"""资料页：给一个人填背景（0.4.0）。

以前这一页只能**看**（把画像文件渲染成只读文本）。现在的要点是**能填**：

    「关系」「我叫他」「他叫我」「认识多久」「雷区」「最近发生的事」「其他备注」

填完写进同一份 `关系档案\\画像\\contacts\\<名字>.md` 里一个带注释标记的区块。
为什么要写进那份文件、而不是另开一个：生成建议时送的就是这份画像，
写在里面模型自然就看得到——**这就是"让 AI 知道我和他/她是什么关系"的办法**，
不用再教程序多读一个数据源。

两条规矩：
1. 只替换自己那个区块，上游技能写的正文一字不动（免得两边互相覆盖）；
2. 保存前不写盘，切换对象时如果改过会问一句，不静默丢掉人填的东西。
"""

from __future__ import annotations

from typing import Dict, List, Optional

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import archive
from .. import config as cfgmod


class ProfilePanel(QWidget):
    """某一个人的背景表单。`saved` 在写盘成功后发出（带对象名）。"""

    saved = pyqtSignal(str)
    memory_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._contact = ""
        self._dirty = False
        self._edits: Dict[str, QLineEdit] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.title = QLabel("还没选人")
        self.title.setObjectName("cardTitle")
        head.addWidget(self.title)
        self.hint = QLabel("")
        self.hint.setObjectName("hint")
        head.addWidget(self.hint)
        head.addStretch(1)

        self.open_button = QPushButton("打开画像目录")
        self.open_button.clicked.connect(lambda: self._open_folder(cfgmod.profile_dir()))
        head.addWidget(self.open_button)
        self.memory_button = QPushButton("查记忆库")
        self.memory_button.setToolTip("长期记忆库默认未启用；这里只是看看它的状态")
        self.memory_button.clicked.connect(self.memory_requested.emit)
        head.addWidget(self.memory_button)
        outer.addLayout(head)

        box = QGroupBox("我填的背景（会随画像一起送给军师）")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)
        for label, hint in archive.PROFILE_FIELDS:
            edit = QLineEdit()
            edit.setPlaceholderText(hint)
            edit.textChanged.connect(self._on_edited)
            self._edits[label] = edit
            form.addRow("%s：" % label, edit)
        outer.addWidget(box)

        actions = QHBoxLayout()
        actions.addStretch(1)
        self.status = QLabel("")
        self.status.setObjectName("hint")
        actions.addWidget(self.status)
        self.save_button = QPushButton("保存")
        self.save_button.setObjectName("primary")
        self.save_button.clicked.connect(self.save)
        actions.addWidget(self.save_button)
        outer.addLayout(actions)

        self.raw_label = QLabel("画像原文（只读；上面填的内容会追加进这份文件）")
        self.raw_label.setObjectName("hint")
        outer.addWidget(self.raw_label)
        self.raw_view = QTextBrowser()
        outer.addWidget(self.raw_view, 1)

        self.set_contact("")

    # ── 数据 ────────────────────────────────────────────────────────────
    def set_contact(self, name: str) -> None:
        """切换对象。改过还没保存就先留住（不静默丢）。"""
        name = (name or "").strip()
        if name == self._contact:
            return
        if self._dirty and self._contact:
            self.save(quiet=True)          # 换人前先把上一个人的改动落盘
        self._contact = name
        self.refresh()

    def refresh(self) -> None:
        name = self._contact
        if not name:
            self.title.setText("还没选人")
            self.hint.setText("在左边点一个会话，就能给这个人填背景")
            for edit in self._edits.values():
                edit.blockSignals(True)
                edit.clear()
                edit.blockSignals(False)
            self.raw_view.setPlainText("")
            self._dirty = False
            return

        self.title.setText("资料：%s" % name)
        known = archive.known_contacts()
        exists = name in known
        self.hint.setText("画像文件：%s" % ("已有" if exists else "还没有，保存时会新建"))

        values = archive.read_profile_form(name)
        for label, edit in self._edits.items():
            edit.blockSignals(True)
            edit.setText(values.get(label, ""))
            edit.blockSignals(False)

        text = archive.contact_profile(name, 20000)
        self.raw_view.setPlainText(text or "（这个人还没有画像文件）")
        self._dirty = False
        self.status.setText("")

    def save(self, quiet: bool = False) -> bool:
        name = self._contact
        if not name:
            if not quiet:
                self.status.setText("先选一个人")
            return False
        fields = {label: edit.text() for label, edit in self._edits.items()}
        if not any(value.strip() for value in fields.values()):
            if not quiet:
                self.status.setText("一个字段都没填，先写点东西再存")
            return False
        try:
            path = archive.write_profile_form(name, fields)
        except Exception as exc:  # noqa: BLE001
            self.status.setText("存不上：%s" % exc)
            return False
        self._dirty = False
        self.status.setText("已保存到 %s" % path.name)
        self.refresh()
        self.saved.emit(name)
        return True

    # ── 内部 ────────────────────────────────────────────────────────────
    def _on_edited(self, _text: str) -> None:
        self._dirty = True
        self.status.setText("改过了，记得点「保存」")

    def _open_folder(self, folder: object) -> None:
        import os

        path = str(folder)
        try:
            os.makedirs(path, exist_ok=True)
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            self.status.setText("打不开：%s" % exc)
