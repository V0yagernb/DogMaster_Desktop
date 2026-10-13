"""界面主题：颜色、字体、全局样式表。

为什么单独一个文件：界面代码里**不许再写死颜色**。以后要换肤、要适配
深色模式，只改这一个文件。颜色值全部集中在 COLORS 里，样式表用
string.Template 插值（QSS 里全是花括号，用 f-string 会被当成占位符）。

配色说明：
- 底色偏冷的浅灰，白卡片浮在上面，长时间盯着不累。
- 主色是墨绿（accent），取自"军师"这种感觉，不用微信那种亮绿。
- 红色只留给制动，全界面只有那一处是红的。
"""

from __future__ import annotations

from string import Template

# ── 颜色 ────────────────────────────────────────────────────────────────
COLORS = {
    "bg": "#eceff3",
    "panel": "#ffffff",
    "panel_alt": "#f7f9fb",
    "border": "#dce0e6",
    "border_strong": "#c6ccd4",
    "text": "#1f2329",
    "text_muted": "#8a9099",
    "accent": "#2f6f4f",
    "accent_dark": "#245640",
    "accent_soft": "#e8f3ed",
    "danger": "#d32f2f",
    "danger_dark": "#b21f1f",
    "ok": "#1a9c5b",
    "warn": "#c77700",
    "bubble_me": "#d6f0c8",
    "bubble_me_border": "#bfe3ad",
    "bubble_other": "#ffffff",
    "bubble_other_border": "#e2e6eb",
    "unread": "#e8453c",
    "selected": "#e8f3ed",
    "disabled": "#b8bec7",
}

FONT_FAMILY = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'

# ── 全局样式表 ──────────────────────────────────────────────────────────
_STYLE = Template(
    """
QWidget {
    background: $bg;
    color: $text;
    font-family: $font_family;
    font-size: 13px;
}
QMainWindow, QDialog { background: $bg; }

/* ── 卡片 ─────────────────────────────────────────────────────────── */
QFrame#card {
    background: $panel;
    border: 1px solid $border;
    border-radius: 8px;
}
QFrame#topbar {
    background: $panel;
    border-bottom: 1px solid $border;
}
QFrame#adviceCard {
    background: $panel;
    border: 1px solid $border;
    border-radius: 8px;
}
QFrame#firstCard {
    background: $accent_soft;
    border: 1px solid $accent;
    border-radius: 8px;
}
QLabel#cardTitle {
    color: $accent_dark;
    font-weight: bold;
    font-size: 13px;
}
QLabel#firstText {
    font-size: 15px;
    font-weight: bold;
    color: $text;
}
QLabel#hint { color: $text_muted; }
QLabel#brand { font-size: 15px; font-weight: bold; color: $accent_dark; }
QLabel#ver { color: $text_muted; font-size: 12px; }

/* ── 按钮 ─────────────────────────────────────────────────────────── */
QPushButton {
    background: $panel;
    border: 1px solid $border_strong;
    border-radius: 6px;
    padding: 5px 12px;
    min-height: 20px;
}
QPushButton:hover { background: $panel_alt; border-color: $accent; }
QPushButton:pressed { background: $accent_soft; }
QPushButton:disabled { color: $disabled; border-color: $border; background: $panel_alt; }
QPushButton#primary {
    background: $accent;
    border: 1px solid $accent_dark;
    color: #ffffff;
    font-weight: bold;
}
QPushButton#primary:hover { background: $accent_dark; }
QPushButton#primary:disabled { background: $disabled; border-color: $border; color: #ffffff; }
QPushButton#brake {
    background: $danger;
    border: 1px solid $danger_dark;
    color: #ffffff;
    font-weight: bold;
    font-size: 15px;
}
QPushButton#brake:hover { background: $danger_dark; }
QPushButton#brake:disabled { background: $disabled; border-color: $border; }
QPushButton#link {
    background: transparent;
    border: none;
    color: $accent;
    text-decoration: underline;
    padding: 2px 4px;
}

/* ── 输入类 ───────────────────────────────────────────────────────── */
QLineEdit, QTextEdit, QTextBrowser, QSpinBox, QComboBox {
    background: $panel;
    border: 1px solid $border_strong;
    border-radius: 6px;
    padding: 4px 6px;
    selection-background-color: $accent;
    selection-color: #ffffff;
}
QLineEdit:focus, QTextEdit:focus, QSpinBox:focus, QComboBox:focus { border-color: $accent; }
QComboBox::drop-down { border: none; width: 18px; }
QComboBox QAbstractItemView {
    background: $panel;
    border: 1px solid $border_strong;
    selection-background-color: $accent_soft;
    selection-color: $text;
}

/* ── 列表 ─────────────────────────────────────────────────────────── */
QListWidget {
    background: $panel;
    border: 1px solid $border;
    border-radius: 8px;
    outline: none;
}
QListWidget::item { border-bottom: 1px solid $panel_alt; }
QListWidget::item:selected { background: $selected; }

/* ── 标签页 ───────────────────────────────────────────────────────── */
QTabWidget::pane {
    background: $panel;
    border: 1px solid $border;
    border-radius: 8px;
    top: -1px;
}
QTabBar::tab {
    background: transparent;
    border: 1px solid transparent;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 6px 14px;
    color: $text_muted;
}
QTabBar::tab:selected {
    background: $panel;
    border-color: $border;
    border-bottom-color: $panel;
    color: $accent_dark;
    font-weight: bold;
}

/* ── 分隔条与滚动条 ───────────────────────────────────────────────── */
QSplitter::handle { background: transparent; }
QSplitter::handle:horizontal { width: 6px; }
QSplitter::handle:vertical { height: 6px; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $border_strong; border-radius: 5px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: $text_muted; }
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: $border_strong; border-radius: 5px; min-width: 30px; }

/* ── 状态栏与菜单 ─────────────────────────────────────────────────── */
QStatusBar { background: $panel; border-top: 1px solid $border; color: $text_muted; }
QStatusBar::item { border: none; }
QMenu { background: $panel; border: 1px solid $border_strong; padding: 4px; }
QMenu::item { padding: 5px 22px 5px 12px; border-radius: 4px; }
QMenu::item:selected { background: $accent_soft; color: $accent_dark; }
QToolTip {
    background: $text;
    color: #ffffff;
    border: none;
    padding: 5px 8px;
}
"""
)


def app_style() -> str:
    """整个程序的样式表。启动时设一次即可。"""
    return _STYLE.substitute(font_family=FONT_FAMILY, **COLORS)


def color(name: str) -> str:
    """按名字取颜色，界面里需要给某个控件单独上色时用。"""
    return COLORS.get(name, "#000000")
