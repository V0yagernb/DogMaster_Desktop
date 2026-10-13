"""界面用的后台线程。

单独一个文件的理由：主窗口与设置窗口都要起后台任务，而它们互相导入会绕成环。
这里只放两个小类，谁都能安全地用。

两条踩过的坑（写在这里免得重犯）：
1. 属性名不能叫 `_stop`——那是 `threading.Thread` 自己的内部方法，覆盖后 `join()`
   会抛 TypeError，线程收不干净，进程退出时崩（退出码 0xC0000409）。
2. 界面控件**只能在主线程碰**：后台线程里一律用信号把结果发回去，别直接改控件。
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from PyQt5.QtCore import QThread, pyqtSignal
from PyQt5.QtWidgets import QWidget


class Worker(QThread):
    """跑一个慢动作，把结果或错误发回主线程。"""

    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[[], Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.fn = fn

    def run(self) -> None:  # pragma: no cover - 需要事件循环
        try:
            self.done.emit(self.fn())
        except Exception as exc:  # noqa: BLE001 - 界面必须把错误说出来
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))


class StreamWorker(QThread):
    """跑一个"边出边发"的任务（军师的建议就是这种）。

    传给它的函数签名是 `fn(on_delta, on_note, on_stage, on_reasoning)`：
    - `on_delta(文本片段)`：每收到一段正文就发一次；
    - `on_note(提示)`：状态变化（"已回退到兜底通道"这类）；
    - `on_stage(结果)`：两段式里第一段（极速版）跑完时发一次，界面据此马上显示首选；
    - `on_reasoning(思考片段)`：模型"想"的过程（glm-5.x 会先想一大段）。
      **这一路不能省**：丢掉它，界面在模型思考期间就是一片空白，看着像卡死。
    """

    delta = pyqtSignal(str)
    note = pyqtSignal(str)
    stage = pyqtSignal(object)
    reasoning = pyqtSignal(str)
    done = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, fn: Callable[..., Any], parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.fn = fn

    def run(self) -> None:  # pragma: no cover - 需要事件循环
        try:
            result = self.fn(self.delta.emit, self.note.emit, self.stage.emit, self.reasoning.emit)
            self.done.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))
