"""PyInstaller 的入口脚本（打包成 exe 时用它，不用包内相对导入）。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from goutou_chat.app import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
