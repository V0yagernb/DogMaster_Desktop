"""生成程序图标（icon.ico + icon.png）。打包脚本会调它。

单独一个入口的原因：PyInstaller 的 --icon 只吃 .ico 文件，而图标是用代码画的
（见 goutou_chat/ui/icon.py）。用法：

    python tools\make_icon.py --out app\assets
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from goutou_chat.ui.icon import write_icons  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="生成程序图标")
    parser.add_argument(
        "--out",
        default=str(Path(__file__).resolve().parents[1] / "assets"),
        help="输出目录（默认 app\\assets）",
    )
    args = parser.parse_args()

    folder = Path(args.out)
    ico, png = write_icons(folder)
    print(ico)
    print(png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
