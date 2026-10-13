"""程序图标：用代码画，不依赖外部图片文件。

为什么不用图片文件：exe 打包后图标要跟着走，多一个二进制资源就多一处
"发布时忘了带"的可能。这里现画：墨绿圆角方块 + 白色「军」字。

同时提供 save_ico()：PyInstaller 的 --icon 只吃 .ico，而 Qt 的 ICO 插件
在部分版本上不支持写，所以这里自己拼 ICO 容器（内层放 PNG，Vista 以上都认）。
"""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Iterable, Tuple

from PyQt5.QtCore import QBuffer, QIODevice, QRectF, Qt
from PyQt5.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPixmap

from . import theme

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)

# QFont 需要一个 QGuiApplication 撑着：没有它，画字会直接以访问冲突收场
# （实测 0xC0000005，还是静默崩溃，连报错都没有）。打包脚本里没人替我们建这个实例，
# 所以这里自己兜住。引用必须留着，否则对象被回收，后面照样崩。
_GUI_APP = None


def _ensure_gui():
    global _GUI_APP
    from PyQt5.QtGui import QGuiApplication

    if QGuiApplication.instance() is None:
        _GUI_APP = QGuiApplication([])
    return _GUI_APP


def render_icon_image(size: int = 256) -> QImage:
    """画一张方形图标。尺寸变了字也跟着缩放，小尺寸下也看得清。"""
    _ensure_gui()
    image = QImage(size, size, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)

    margin = size * 0.04
    rect = QRectF(margin, margin, size - margin * 2, size - margin * 2)
    path = QPainterPath()
    path.addRoundedRect(rect, size * 0.24, size * 0.24)
    painter.fillPath(path, QColor(theme.color("accent")))

    painter.setPen(QColor("#ffffff"))
    family = theme.FONT_FAMILY.split(",")[0].strip('"')
    font = QFont(family, int(size * 0.46))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(rect, Qt.AlignCenter, "军")
    painter.end()
    return image


def _png_bytes(image: QImage) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "PNG")
    data = bytes(buffer.data())
    buffer.close()
    return data


def app_icon() -> QIcon:
    """窗口与托盘共用的图标。"""
    icon = QIcon()
    pixmap = QPixmap.fromImage(render_icon_image(256))
    icon.addPixmap(pixmap)
    return icon


def save_png(path: Path, size: int = 256) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    render_icon_image(size).save(str(path), "PNG")
    return path


def save_ico(path: Path, sizes: Iterable[int] = ICO_SIZES) -> Path:
    """自己拼 ICO：头 6 字节 + 每张 16 字节目录项 + 各尺寸 PNG 数据。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    entries: list = []
    for size in sizes:
        entries.append((int(size), _png_bytes(render_icon_image(int(size)))))

    header = struct.pack("<HHH", 0, 1, len(entries))
    offset = len(header) + 16 * len(entries)
    directory = b""
    payload = b""
    for size, data in entries:
        width = 0 if size >= 256 else size
        height = 0 if size >= 256 else size
        directory += struct.pack(
            "<BBBBHHII", width, height, 0, 0, 1, 32, len(data), offset
        )
        payload += data
        offset += len(data)

    path.write_bytes(header + directory + payload)
    return path


def write_icons(folder: Path) -> Tuple[Path, Path]:
    """打包前调用：在指定目录写出 icon.ico 与 icon.png，返回两个路径。"""
    folder = Path(folder)
    return save_ico(folder / "icon.ico"), save_png(folder / "icon.png")
