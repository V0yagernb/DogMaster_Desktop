r"""复盘浏览面板（第二版清单第 4 项）。

复盘文件本来就一直在写（`关系档案\复盘\日期-对象.md`，ADR-0018），只是之前
没有地方看。这里是只读浏览：左边列文件（新的在上），右边看内容。

不做编辑、不做删除——复盘是给人回看的记录，程序不该顺手改它。
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import config as cfgmod
from . import theme


class ReviewPanel(QWidget):
    """复盘浏览。refresh() 时重新扫一遍目录。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh)
        tools.addWidget(self.refresh_button)
        self.open_dir_button = QPushButton("打开复盘目录")
        self.open_dir_button.clicked.connect(self._open_dir)
        tools.addWidget(self.open_dir_button)
        tools.addStretch(1)
        outer.addLayout(tools)

        self.hint = QLabel("")
        self.hint.setObjectName("hint")
        self.hint.setWordWrap(True)
        outer.addWidget(self.hint)

        splitter = QSplitter(Qt.Horizontal)
        self.file_list = QListWidget()
        self.file_list.setStyleSheet(
            "QListWidget{background:%s;border:1px solid %s;border-radius:8px;}"
            % (theme.color("panel"), theme.color("border"))
        )
        self.file_list.currentItemChanged.connect(lambda *_: self._show_current())
        splitter.addWidget(self.file_list)

        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(False)
        splitter.addWidget(self.preview)
        splitter.setSizes([190, 260])
        outer.addWidget(splitter, 1)

        self.refresh()

    # ── 数据 ────────────────────────────────────────────────────────────
    def review_files(self) -> List[Path]:
        folder = cfgmod.review_dir()
        if not folder.exists():
            return []
        files = [p for p in folder.glob("*.md") if p.is_file()]
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files

    def refresh(self) -> None:
        keep = self._current_path()
        self.file_list.clear()
        files = self.review_files()
        for path in files:
            # 只显示"日期-对象"这个名字：面板窄，塞不下文件大小
            item = QListWidgetItem(path.stem)
            item.setData(Qt.UserRole, str(path))
            try:
                item.setToolTip("%s（%.1f KB）" % (path.name, path.stat().st_size / 1024))
            except OSError:
                pass
            self.file_list.addItem(item)

        if not files:
            self.hint.setText(
                "还没有复盘。让军师看过一次之后，会自动写进 %s。" % cfgmod.review_dir()
            )
            self.preview.setPlainText("")
            return

        self.hint.setText("共 %d 份复盘，存在 %s" % (len(files), cfgmod.review_dir()))
        target_row = 0
        if keep:
            for index in range(self.file_list.count()):
                if self.file_list.item(index).data(Qt.UserRole) == keep:
                    target_row = index
                    break
        self.file_list.setCurrentRow(target_row)

    def _current_path(self) -> str:
        item = self.file_list.currentItem()
        return item.data(Qt.UserRole) if item else ""

    def _show_current(self) -> None:
        raw = self._current_path()
        if not raw:
            self.preview.setPlainText("")
            return
        path = Path(raw)
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - 读不了要说出来
            self.preview.setPlainText("读不到这个文件：%s" % exc)
            return
        # Markdown 能力是 Qt 5.14+ 才有，这里就是个显示，不支持也不影响使用
        try:
            self.preview.setMarkdown(text)
        except AttributeError:
            self.preview.setPlainText(text)

    def _open_dir(self) -> None:
        """用系统资源管理器打开复盘目录（只读浏览，不动文件）。"""
        import os
        import subprocess

        folder = cfgmod.review_dir()
        folder.mkdir(parents=True, exist_ok=True)
        try:
            os.startfile(str(folder))  # type: ignore[attr-defined]
        except AttributeError:
            subprocess.Popen(["explorer", str(folder)])
