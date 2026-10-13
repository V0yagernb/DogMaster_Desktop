"""聊天记录区：把结构化消息画成气泡。

为什么不用 QTextBrowser 直接拼 HTML：Qt 的富文本子集**不支持圆角**，
拼出来的气泡全是方角，一眼就是半成品。这里改用 QListView + 自绘
（QStyledItemDelegate）：圆角、对齐、时间分隔都能自己说了算，而且上千条
记录也不会卡（只画看得见的那几行）。

数据来源是 wda.messages() 返回的原始 dict，字段名以本地解密库为准：
createTime / isSent / senderDisplayName / content / renderType。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from PyQt5.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt
from PyQt5.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PyQt5.QtWidgets import QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from . import theme

# 两条消息间隔超过这么多秒，就重新显示一次时间（和微信的手感一致）
TIME_GAP_SECONDS = 300


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def prepare_messages(messages: List[Dict[str, Any]], me_label: str = "我") -> List[Dict[str, Any]]:
    """把原始消息整理成界面要用的行（纯函数，可测）。

    每条上新增的字段都以 `_` 开头，避免和本地库的字段名撞车：
    _ts / _show_time / _day / _is_me / _who / _text / _placeholder。
    """
    rows: List[Dict[str, Any]] = []
    last_ts = 0
    last_day = ""
    for message in messages or []:
        row: Dict[str, Any] = dict(message)
        ts = _to_int(message.get("createTime") or message.get("create_time"))
        if ts:
            day = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
        else:
            day = ""

        row["_ts"] = ts
        row["_show_time"] = bool(ts) and (ts - last_ts > TIME_GAP_SECONDS or day != last_day)
        row["_day"] = day
        if ts:
            last_ts = ts
            last_day = day

        row["_is_me"] = bool(message.get("isSent"))
        row["_who"] = me_label if row["_is_me"] else (message.get("senderDisplayName") or "对方")

        body = (message.get("content") or "").strip()
        row["_placeholder"] = not body
        if not body:
            # 图片/语音/文件这类没有正文，显示一个占位符，不要假装有内容
            body = "[%s]" % (message.get("renderType") or "非文本")
        row["_text"] = body
        rows.append(row)
    return rows


def format_time_label(ts: int, now: Optional[datetime] = None) -> str:
    """时间分隔条上的字：今天/昨天只写时刻，更早写日期。"""
    if not ts:
        return ""
    moment = datetime.fromtimestamp(ts)
    today = (now or datetime.now()).date()
    delta = (today - moment.date()).days
    if delta == 0:
        return moment.strftime("%H:%M")
    if delta == 1:
        return "昨天 " + moment.strftime("%H:%M")
    if moment.year == today.year:
        return moment.strftime("%m-%d %H:%M")
    return moment.strftime("%Y-%m-%d %H:%M")


def newest_stamp_of(rows: List[Dict[str, Any]]) -> int:
    """一组消息里最新的时间戳（没有就 0）。

    自动刷新靠它判断"有没有新东西"：跟当前列表的最新一条比，变了才重载。
    优先用整理过的 `_ts`，退回本地库的 `createTime`。
    """
    stamps: List[int] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            stamps.append(
                int(row.get("_ts") or row.get("createTime") or row.get("create_time") or 0)
            )
        except (TypeError, ValueError):
            continue
    return max(stamps) if stamps else 0


class MessageModel(QAbstractListModel):
    """消息列表模型：一行一条，data(UserRole) 直接给整理好的 dict。"""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._rows: List[Dict[str, Any]] = []

    def set_messages(self, messages: List[Dict[str, Any]], me_label: str = "我") -> None:
        self.beginResetModel()
        self._rows = prepare_messages(messages, me_label=me_label)
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802 - Qt 命名
        if parent.isValid():
            return 0
        return len(self._rows)

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid() or not (0 <= index.row() < len(self._rows)):
            return None
        if role in (Qt.UserRole, Qt.DisplayRole, Qt.ToolTipRole):
            return self._rows[index.row()]
        return None

    def message_at(self, row: int) -> Optional[Dict[str, Any]]:
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None


class MessageDelegate(QStyledItemDelegate):
    """气泡的画笔。左右对齐、圆角、时间分隔都在这里。"""

    PAD_H = 12        # 气泡内左右留白
    PAD_V = 8         # 气泡内上下留白
    SIDE = 16         # 气泡距视图左右边缘
    GAP = 10          # 两条消息之间
    TIME_H = 30       # 时间分隔条占的高度
    NAME_H = 16       # 群聊里发送者名字那一行
    MAX_RATIO = 0.68  # 气泡最宽占视图宽度的比例

    def __init__(self, view: QListView) -> None:
        super().__init__(view)
        self.view = view
        self._body_font = QFont(theme.FONT_FAMILY.split(",")[0].strip('"'), 10)
        self._name_font = QFont(theme.FONT_FAMILY.split(",")[0].strip('"'), 8)
        self._time_font = QFont(theme.FONT_FAMILY.split(",")[0].strip('"'), 8)

    # ── 尺寸计算 ────────────────────────────────────────────────────────
    def _content_width(self) -> int:
        width = self.view.viewport().width() if self.view else 0
        return max(200, width)

    def _max_bubble_width(self) -> int:
        return int(self._content_width() * self.MAX_RATIO)

    def _bubble_size(self, row: Dict[str, Any]) -> QSize:
        """算气泡大小：先按最大宽度排版，再按实际文字量收窄。"""
        metrics = QFontMetrics(self._body_font)
        text = row.get("_text") or ""
        limit = self._max_bubble_width() - self.PAD_H * 2
        rect = metrics.boundingRect(
            QRect(0, 0, max(40, limit), 10000), Qt.TextWordWrap, text
        )
        width = min(self._max_bubble_width(), rect.width() + self.PAD_H * 2 + 2)
        height = rect.height() + self.PAD_V * 2
        return QSize(width, height)

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:  # noqa: N802
        row = index.data(Qt.UserRole) or {}
        height = self.GAP + self._bubble_size(row).height()
        if row.get("_show_time"):
            height += self.TIME_H
        if self._show_name(row):
            height += self.NAME_H
        return QSize(self._content_width(), height)

    @staticmethod
    def _show_name(row: Dict[str, Any]) -> bool:
        """群聊里才显示发送者名字；一对一聊天不用（省地方）。"""
        return bool(row.get("_group")) and not row.get("_is_me")

    # ── 绘制 ────────────────────────────────────────────────────────────
    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        row = index.data(Qt.UserRole) or {}
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = option.rect
        cursor_y = rect.top()

        if row.get("_show_time"):
            painter.setFont(self._time_font)
            painter.setPen(QPen(QColor(theme.color("text_muted"))))
            painter.drawText(
                QRect(rect.left(), cursor_y, rect.width(), self.TIME_H),
                Qt.AlignHCenter | Qt.AlignVCenter,
                format_time_label(row.get("_ts") or 0),
            )
            cursor_y += self.TIME_H

        if self._show_name(row):
            painter.setFont(self._name_font)
            painter.setPen(QPen(QColor(theme.color("text_muted"))))
            name_rect = QRect(
                rect.left() + self.SIDE, cursor_y, rect.width() - self.SIDE * 2, self.NAME_H
            )
            painter.drawText(name_rect, Qt.AlignLeft | Qt.AlignVCenter, row.get("_who") or "")
            cursor_y += self.NAME_H

        size = self._bubble_size(row)
        is_me = bool(row.get("_is_me"))
        if is_me:
            bubble = QRect(
                rect.right() - self.SIDE - size.width(), cursor_y, size.width(), size.height()
            )
            fill = QColor(theme.color("bubble_me"))
            border = QColor(theme.color("bubble_me_border"))
            text_color = QColor(theme.color("text"))
        else:
            bubble = QRect(rect.left() + self.SIDE, cursor_y, size.width(), size.height())
            fill = QColor(theme.color("bubble_other"))
            border = QColor(theme.color("bubble_other_border"))
            text_color = QColor(theme.color("text"))

        painter.setPen(QPen(border))
        painter.setBrush(fill)
        painter.drawRoundedRect(bubble, 10, 10)

        painter.setFont(self._body_font)
        if row.get("_placeholder"):
            painter.setPen(QPen(QColor(theme.color("text_muted"))))
            painter.setFont(self._name_font)
        else:
            painter.setPen(QPen(text_color))
        painter.drawText(
            bubble.adjusted(self.PAD_H, self.PAD_V, -self.PAD_H, -self.PAD_V),
            int(Qt.TextWordWrap | Qt.AlignLeft | Qt.AlignTop),
            row.get("_text") or "",
        )
        painter.restore()


class ChatView(QListView):
    """聊天记录视图。给一段消息，它负责画出来并滚到底。"""

    def __init__(self, parent: Any = None) -> None:
        super().__init__(parent)
        self._model = MessageModel(self)
        self.setModel(self._model)
        self.setItemDelegate(MessageDelegate(self))
        self.setSelectionMode(QListView.NoSelection)
        self.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setUniformItemSizes(False)
        self.setSpacing(0)
        self.setStyleSheet(
            "QListView{background:%s;border:none;}" % theme.color("panel_alt")
        )

    def set_messages(
        self,
        messages: List[Dict[str, Any]],
        me_label: str = "我",
        is_group: bool = False,
        keep_scroll: bool = False,
    ) -> None:
        """换一批消息。

        keep_scroll=True（每秒自动刷新时用）：本来就在看历史的话，别把你弹回底部；
        停在底部时才继续跟着新消息走。
        """
        was_at_bottom = self._at_bottom()
        offset = self.verticalScrollBar().value()

        tagged = []
        for message in messages or []:
            row = dict(message)
            row["_group"] = is_group
            tagged.append(row)
        self._model.set_messages(tagged, me_label=me_label)

        if keep_scroll and not was_at_bottom:
            self.verticalScrollBar().setValue(offset)
        else:
            self.scrollToBottom()

    def _at_bottom(self) -> bool:
        bar = self.verticalScrollBar()
        return bar.value() >= bar.maximum() - 4

    def newest_stamp(self) -> int:
        """当前列表里最新一条的时间戳（自动刷新用它判断"有没有新消息"）。"""
        return newest_stamp_of(self.messages())

    def message_count(self) -> int:
        return self._model.rowCount()

    def messages(self) -> List[Dict[str, Any]]:
        return [self._model.message_at(i) or {} for i in range(self._model.rowCount())]

    def resizeEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        """窗口变宽变窄时，气泡要重排，否则会留一堆错位的空白。"""
        super().resizeEvent(event)
        self.scheduleDelayedItemsLayout()
        self.viewport().update()
