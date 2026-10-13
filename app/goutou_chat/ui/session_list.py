"""会话列表：搜索框 + 双行会话行（头像 / 名字 / 最后一句 / 未读红点）。

同样用自绘而不是 QListWidget 的纯文本：一行要放四样东西（头像、名字、
最后一句、未读徽标），纯文本排不出这个样子。

过滤逻辑只有一条：`_source_rows` 存全量，搜索时过滤出子集塞回模型。
刷新会话时保留搜索词，别把用户正在看的东西冲掉。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import (
    QLabel,
    QLineEdit,
    QListView,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QVBoxLayout,
    QWidget,
)

from . import theme

AVATAR_SIZE = 38
ROW_HEIGHT = 62

# 头像底色轮换，让每个人看起来不一样（同一个人永远同一个颜色：按名字求和取模）
AVATAR_COLORS = ["#5b8def", "#e07a5f", "#3d9970", "#b07cc6", "#e0a13c", "#4aa3c7", "#c2566d"]


def _avatar_color(name: str) -> str:
    if not name:
        return AVATAR_COLORS[0]
    return AVATAR_COLORS[sum(ord(ch) for ch in name) % len(AVATAR_COLORS)]


def _pick_time(item: Dict[str, Any]) -> int:
    """本地库给的时间字段名字不固定，挨个试一遍（找不到就是 0）。"""
    for key in ("lastTime", "last_time", "createTime", "create_time", "timestamp", "time"):
        value = item.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return int(value)
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return 0


def _format_time(ts: int) -> str:
    if not ts:
        return ""
    moment = datetime.fromtimestamp(ts)
    today = datetime.now().date()
    delta = (today - moment.date()).days
    if delta <= 0:
        return moment.strftime("%H:%M")
    if delta == 1:
        return "昨天"
    if moment.year == today.year:
        return moment.strftime("%m-%d")
    return moment.strftime("%y-%m-%d")


def prepare_sessions(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """整理会话行（纯函数，可测）：有未读的排前面，其余按最后消息时间倒序。"""
    rows: List[Dict[str, Any]] = []
    for item in items or []:
        row: Dict[str, Any] = dict(item)
        username = item.get("username") or ""
        name = (item.get("name") or username or "?").strip()
        row["_name"] = name
        row["_username"] = username
        row["_group"] = username.endswith("@chatroom")
        try:
            row["_unread"] = int(item.get("unreadCount") or item.get("unread_count") or 0)
        except (TypeError, ValueError):
            row["_unread"] = 0
        preview = item.get("lastMessage") or item.get("last_message") or ""
        row["_preview"] = str(preview).replace("\n", " ").strip()
        row["_ts"] = _pick_time(item)
        row["_time"] = _format_time(row["_ts"])
        row["_initial"] = name[:1].upper() if name else "?"
        row["_avatar_color"] = _avatar_color(name)
        rows.append(row)

    rows.sort(key=lambda r: (-(r["_unread"] > 0), -r["_ts"], r["_name"]))
    return rows


class SessionModel(QAbstractListModel):
    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._rows: List[Dict[str, Any]] = []

    def set_rows(self, rows: List[Dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        if role in (Qt.UserRole, Qt.DisplayRole, Qt.ToolTipRole):
            return self._rows[index.row()]
        return None


class SessionDelegate(QStyledItemDelegate):
    def __init__(self, view: QListView) -> None:
        super().__init__(view)
        self.view = view
        family = theme.FONT_FAMILY.split(",")[0].strip('"')
        self._name_font = QFont(family, 10)
        self._name_font.setBold(True)
        self._preview_font = QFont(family, 9)
        self._small_font = QFont(family, 8)
        self._avatar_font = QFont(family, 13)
        self._avatar_font.setBold(True)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        return QSize(self.view.viewport().width(), ROW_HEIGHT)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        row = index.data(Qt.UserRole) or {}
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect

        if option.state & QStyle.State_Selected:
            painter.fillRect(rect, QColor(theme.color("selected")))
        else:
            painter.fillRect(rect, QColor(theme.color("panel")))
        painter.setPen(QPen(QColor(theme.color("panel_alt"))))
        painter.drawLine(rect.left() + 8, rect.bottom() - 1, rect.right() - 8, rect.bottom() - 1)

        # 头像
        avatar = QRect(
            rect.left() + 12,
            rect.top() + (ROW_HEIGHT - AVATAR_SIZE) // 2,
            AVATAR_SIZE,
            AVATAR_SIZE,
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(row.get("_avatar_color") or AVATAR_COLORS[0]))
        painter.drawEllipse(avatar)
        painter.setPen(QPen(QColor("#ffffff")))
        painter.setFont(self._avatar_font)
        painter.drawText(avatar, Qt.AlignCenter, row.get("_initial") or "?")

        text_left = avatar.right() + 12
        text_right = rect.right() - 10
        label = ("［群］" if row.get("_group") else "") + (row.get("_name") or "?")

        # 右上角：有未读就画红点，否则画时间
        right_width = 0
        unread = row.get("_unread") or 0
        if unread:
            badge_text = "%d" % unread if unread < 100 else "99+"
            metrics = QFontMetrics(self._small_font)
            badge_w = max(18, metrics.horizontalAdvance(badge_text) + 10)
            badge = QRect(text_right - badge_w, rect.top() + 12, badge_w, 16)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.color("unread")))
            painter.drawRoundedRect(badge, 8, 8)
            painter.setPen(QPen(QColor("#ffffff")))
            painter.setFont(self._small_font)
            painter.drawText(badge, Qt.AlignCenter, badge_text)
            right_width = badge_w + 6
        elif row.get("_time"):
            metrics = QFontMetrics(self._small_font)
            right_width = metrics.horizontalAdvance(row["_time"]) + 6
            painter.setPen(QPen(QColor(theme.color("text_muted"))))
            painter.setFont(self._small_font)
            painter.drawText(
                QRect(text_right - right_width, rect.top() + 12, right_width, 14),
                Qt.AlignRight | Qt.AlignVCenter,
                row["_time"],
            )

        name_width = max(40, text_right - text_left - right_width)
        painter.setPen(QPen(QColor(theme.color("text"))))
        painter.setFont(self._name_font)
        painter.drawText(
            QRect(text_left, rect.top() + 11, name_width, 18),
            Qt.AlignLeft | Qt.AlignVCenter,
            QFontMetrics(self._name_font).elidedText(label, Qt.ElideRight, name_width),
        )

        painter.setPen(QPen(QColor(theme.color("text_muted"))))
        painter.setFont(self._preview_font)
        preview = row.get("_preview") or "（还没有消息）"
        painter.drawText(
            QRect(text_left, rect.top() + 32, text_right - text_left, 18),
            Qt.AlignLeft | Qt.AlignVCenter,
            QFontMetrics(self._preview_font).elidedText(preview, Qt.ElideRight, text_right - text_left),
        )
        painter.restore()


class SessionList(QWidget):
    """搜索框 + 会话列表。选中某一行会发出 `selected`（整理过的行）。"""

    selected = pyqtSignal(dict)

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._source_rows: List[Dict[str, Any]] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜名字或最后一句…")
        self.search.textChanged.connect(lambda _text: self._apply_filter())
        layout.addWidget(self.search)

        self.view = QListView()
        self._model = SessionModel(self)
        self.view.setModel(self._model)
        self.view.setItemDelegate(SessionDelegate(self.view))
        self.view.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.view.setEditTriggers(QListView.NoEditTriggers)
        self.view.setStyleSheet("QListView{background:%s;border:none;}" % theme.color("panel"))
        self.view.selectionModel().currentChanged.connect(self._on_current_changed)
        layout.addWidget(self.view, 1)

        self.count_label = QLabel("会话：还没加载")
        self.count_label.setObjectName("hint")
        layout.addWidget(self.count_label)

    # ── 数据 ────────────────────────────────────────────────────────────
    def set_sessions(self, items: List[Dict[str, Any]]) -> None:
        """刷新会话；搜索词保持不变。"""
        self._source_rows = prepare_sessions(items)
        self._apply_filter()

    def rows(self) -> List[Dict[str, Any]]:
        return list(self._source_rows)

    def current_row(self) -> Optional[Dict[str, Any]]:
        index = self.view.currentIndex()
        if not index.isValid():
            return None
        return index.data(Qt.UserRole)

    def select_username(self, username: str) -> bool:
        """按 username 选中某一行（演示数据与自动定位用）。"""
        for row_index in range(self._model.rowCount()):
            index = self._model.index(row_index, 0)
            row = index.data(Qt.UserRole) or {}
            if row.get("_username") == username:
                self.view.setCurrentIndex(index)
                return True
        return False

    def _apply_filter(self) -> None:
        keyword = (self.search.text() or "").strip().lower()
        if not keyword:
            self._model.set_rows(self._source_rows)
            self.count_label.setText("会话：%d 个" % len(self._source_rows))
            return
        filtered = [
            row for row in self._source_rows
            if keyword in (row.get("_name") or "").lower()
            or keyword in (row.get("_preview") or "").lower()
        ]
        self._model.set_rows(filtered)
        self.count_label.setText(
            "会话：%d 个（筛出 %d 个）" % (len(self._source_rows), len(filtered))
        )

    def _on_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        if current.isValid():
            self.selected.emit(current.data(Qt.UserRole) or {})
