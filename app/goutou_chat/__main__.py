"""允许 `python -m goutou_chat`。"""

from .app import main

if __name__ == "__main__":
    raise SystemExit(main())
