r"""离屏渲染主窗口并存截图——验收与视觉回归用。

为什么不在屏幕上截：这个脚本不需要有人盯着，也不用抢前台；更重要的是它
不会因为窗口被别的程序压住而截到别人。跑法：

    python tools\ui_preview.py --out F:\goutoujunshi\_preview

产出：01-主界面（军师页）、02-复盘页、03-资料页、04-体检明细、05-已制动。
用的全是 --demo 那份假数据，界面上会自己标出「演示数据」。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import Qt  # noqa: E402
from PyQt5.QtWidgets import QApplication, QSystemTrayIcon  # noqa: E402

from goutou_chat.ui import theme  # noqa: E402
from goutou_chat.ui.demo import DEMO_SESSIONS  # noqa: E402
from goutou_chat.ui.main_window import MainWindow  # noqa: E402


def pump(app: QApplication, seconds: float) -> None:
    """空转事件循环：让界面把后台任务的结果画上去。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)


def shot(widget, out_dir: Path, name: str) -> Path:
    path = out_dir / ("%s.png" % name)
    widget.grab().save(str(path))
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="离屏渲染界面并截图")
    parser.add_argument("--out", default=str(Path(__file__).resolve().parents[1] / "_preview"))
    parser.add_argument("--width", type=int, default=1260)
    parser.add_argument("--height", type=int, default=820)
    parser.add_argument(
        "--platform",
        default="windows",
        choices=["windows", "offscreen", "minimal"],
        help=(
            "Qt 平台插件。默认 windows：本机的 offscreen 插件字体库是空的（实测 0 个字体），"
            "用它截出来的图一个字都没有。windows 平台配合 WA_DontShowOnScreen，"
            "排版是真的、窗口不上屏。"
        ),
    )
    parser.add_argument(
        "--real",
        action="store_true",
        help="用真实数据（读取后端要在跑）。默认用 --demo 那份假数据。",
    )
    parser.add_argument("--who", default="", help="真实模式下先选中谁（按会话名字精确匹配）")
    args = parser.parse_args()
    # 平台插件必须在 QApplication 构造之前定下来
    os.environ["QT_QPA_PLATFORM"] = args.platform

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    # 不需要系统托盘：关掉它，免得托盘图标去摸不存在的壳
    QSystemTrayIcon.isSystemTrayAvailable = staticmethod(lambda: False)  # type: ignore[assignment]

    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.app_style())

    window = MainWindow(demo=not args.real)
    window.resize(args.width, args.height)
    if args.platform != "offscreen":
        # 正常排版、正常渲染，但不真的显示到屏幕上（不打扰正在用电脑的人）
        window.setAttribute(Qt.WA_DontShowOnScreen, True)
    window.show()
    pump(app, 1.2)

    prefix = ""
    if args.real:
        prefix = "real-"
        # 真实数据要等读取后端返回（会话多的时候要几秒）
        pump(app, 8.0)
        rows = window.session_list.rows()
        picked = None
        if args.who:
            picked = next((row for row in rows if row.get("_name") == args.who), None)
        if picked is None and rows:
            picked = rows[0]
        if picked is not None:
            window.session_list.select_username(picked.get("_username") or "")
            pump(app, 3.0)
        print("真实模式：会话 %d 个，选中 %s" % (len(rows), (picked or {}).get("_name", "无")))
    else:
        # 演示一下全自动状态条（演练中，不会真发）
        from goutou_chat import rules

        grant = rules.AutoGrant(
            mode=rules.Mode.AUTO_ONCE.value,
            username=DEMO_SESSIONS[0]["username"],
            name=DEMO_SESSIONS[0]["name"],
            created_at=time.time(),
            expires_at=time.time() + 1800,
            remaining=1,
            armed=True,
            dry_run=True,
        )
        window.cfg.auto_grant = grant.to_dict()
        window._refresh_auto_bar()
        pump(app, 0.3)

    made = []
    # 演示数据的第一条就是"联系人A"；真实模式已经选好了人，这里不点建议（避免写真实复盘）
    if not args.real:
        window.generate_advice()
        pump(app, 3.0)      # 演示模式是模拟流式，一段一段吐出来
    made.append(shot(window, out_dir, prefix + "01-主界面-军师"))

    # 设置窗口
    from goutou_chat.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(window.cfg, window)
    if args.platform != "offscreen":
        dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
    dialog.resize(680, 640)
    dialog.show()
    pump(app, 0.6)
    made.append(shot(dialog, out_dir, prefix + "06-设置"))
    dialog.close()

    # 关系体检页：用一份假数据填（不调模型，截图不该花钱）
    from goutou_chat.analysis import ChatStats

    demo_stats = ChatStats(
        total=500, mine=231, theirs=269, days_span=57, active_days=30,
        avg_mine_chars=15.4, avg_theirs_chars=15.5,
        mine_reply_median=12, theirs_reply_median=16,
        initiations_mine=8, initiations_theirs=25,
        recent_7d=56, previous_7d=57,
        non_text_mine=41, non_text_theirs=78, short_ratio_theirs=0.16,
        longest_silence_days=8, last_message_from_them_hours=0.3,
        top_topics=[("表情", 57), ("旺柴", 43), ("图片", 19)],
    )
    window.analysis_panel.set_stats(demo_stats)
    window.analysis_panel.set_sections(
        {
            "status": "（演示数据）这是母子关系，不是暧昧对象。57 天里 30 天有聊天，"
                      "她发起 25 次、你发起 8 次——她明显在主动找你。最近 7 天 56 条、"
                      "此前 57 条，联系频率稳定。",
            "balance": "她更主动（25 次发起 vs 你 8 次，约 3 倍）。但回复速度双方都很快"
                       "（中位 12 秒 vs 16 秒），单条字数几乎一样——聊起来之后投入是平衡的。",
            "good": "- 她主动发起是你的 3 倍，内容全是生活关心\n- 线下动作与线上关心一致\n"
                    "- 她愿意往你的世界凑（AI、软件这些话题）",
            "bad": "- 57 天里只有 30 天有聊天，最长断聊 8 天\n- 你主动开话题几乎为零\n"
                   "- 部分对话退化成纯表情对轰",
            "risk": "你的「报备式短句」只在被问时才出现，她会在你忙的那几个月里持续单方面等待。",
            "next": "- 把 8 天的断聊上限压到 3 天内\n- 每周给一个实质话题，替换纯表情对轰\n"
                    "- 她主动提到的事，隔天接住问一句",
        },
        note="（演示数据）统计 500 条（样本 200 条）｜直连｜首字 1.0 秒｜共 17.4 秒",
    )
    window.tabs.setCurrentWidget(window.analysis_panel)
    pump(app, 0.5)
    made.append(shot(window, out_dir, prefix + "08-关系体检"))
    window.tabs.setCurrentWidget(window.advice_panel)

    # 全自动授权对话框（要开全自动时才出现）
    from goutou_chat.ui.grant_dialog import GrantDialog

    grant_dialog = GrantDialog(
        idle_minutes=30,
        contact_name="联系人A",
        username="wxid_demo_a",
        demo=True,
        grant_minutes=60,
        parent=window,
    )
    if args.platform != "offscreen":
        grant_dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
    grant_dialog.resize(600, 640)
    grant_dialog.show()
    pump(app, 0.5)
    made.append(shot(grant_dialog, out_dir, prefix + "07-全自动授权"))
    grant_dialog.close()

    window.tabs.setCurrentWidget(window.review_panel)
    window.review_panel.refresh()
    pump(app, 0.4)
    made.append(shot(window, out_dir, prefix + "02-复盘"))

    window.tabs.setCurrentIndex(2)
    window.refresh_profile()
    pump(app, 0.4)
    made.append(shot(window, out_dir, prefix + "03-资料"))

    window.tabs.setCurrentWidget(window.advice_panel)
    window.health_detail_button.setChecked(True)
    pump(app, 0.4)
    made.append(shot(window, out_dir, prefix + "04-体检明细"))
    window.health_detail_button.setChecked(False)

    window.brake.engage("预览制动")
    pump(app, 0.5)
    made.append(shot(window, out_dir, prefix + "05-已制动"))

    window.shutdown()
    for path in made:
        print(path)
    print("共 %d 张" % len(made))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
