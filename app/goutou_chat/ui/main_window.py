"""主窗口：顶栏 / 体检条 / 三栏主体 / 状态栏 / 系统托盘。

界面设计原则（docs/界面草图.md）：
- 只做建议档：能读、能看、能刹住，**不自动发送**。
- 全自动档在界面上是灰的，悬停写明"第二版启用"——不拿没实现的能力骗人。
- 紧急制动永远在最显眼的位置，且永远可点。
- `--demo` 时全部聊天数据是假的，界面必须明确标出来（不做假绿）。
- **通道要如实说**：直连出的建议与兜底通道出的建议，状态栏写法不一样；
  回退时必须写明"这次慢是因为走了兜底"。

线程模型：慢动作（体检、读消息、测连接、问军师）全丢进 Worker / StreamWorker，
界面线程只负责画；建议是流式的，边收边填四块卡片。退出必须走 shutdown()，
否则进程会在 Qt 收尾阶段崩掉（实测退出码 0xC0000409）。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Dict, List, Optional

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtWidgets import (
    QAction,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QSystemTrayIcon,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import archive, brain as brain_mod, config as cfgmod, health as health_mod, rules
from .. import analysis
from ..advisor import Advisor, AdviseResult
from ..autopilot import AutoStep, Autopilot
from ..brake import Brake
from ..pct import Pct
from ..sender import AutoSender, SendOutcome, consume
from ..wda import WdaClient
from . import theme
from .advice_panel import AdvicePanel
from .analysis_panel import AnalysisPanel
from .chat_view import ChatView
from .demo import DEMO_ADVICE, DEMO_BANNER, DEMO_MESSAGES, DEMO_SESSIONS
from .grant_dialog import GrantDialog
from .icon import app_icon
from .profile_panel import ProfilePanel
from .review_panel import ReviewPanel
from .session_list import SessionList
from .settings_dialog import SettingsDialog
from .workers import StreamWorker, Worker

MESSAGE_TIMEOUT_MS = 15000
# 演示模式下模拟流式的节奏
DEMO_CHUNK = 14
DEMO_INTERVAL_MS = 45


class MainWindow(QMainWindow):
    def __init__(self, cfg: Optional[cfgmod.Config] = None, demo: bool = False) -> None:
        super().__init__()
        self.cfg = cfg or cfgmod.Config.load()
        self.demo = bool(demo)
        cfgmod.ensure_dirs()
        self.version = cfgmod.app_version()

        self.wda = WdaClient(port=self.cfg.backend_port)
        self.pct = Pct()
        self.brake = Brake(hotkey=self.cfg.hotkey)
        self.brake.on_change = self._on_brake_change
        self.advisor = Advisor(self.cfg)
        self.sender = AutoSender(self.cfg, self.brake, self.pct)
        self.autopilot = Autopilot(self.cfg, self.wda, self.pct, self.advisor, self.sender)
        self._last_auto_check = 0.0
        self._last_busy_note = 0.0

        self.sessions: List[Dict[str, Any]] = []
        self.messages: List[Dict[str, Any]] = []
        self.current: Optional[Dict[str, Any]] = None
        self._workers: List[Any] = []
        self._running: set = set()
        self._checks: List[health_mod.Check] = []
        self._really_quit = False
        self._demo_queue: List[str] = []

        self._message_timer = QTimer(self)
        self._message_timer.setSingleShot(True)
        self._message_timer.timeout.connect(lambda: self.status_message.setText(""))

        self._demo_timer = QTimer(self)
        self._demo_timer.timeout.connect(self._pump_demo_stream)

        # 授权倒计时：心跳是 15 秒一次，但"限时 1 分钟"必须准点收回，
        # 所以另起一个每秒的定时器专门盯它（只刷新一个标签，开销可忽略）。
        self.grant_timer = QTimer(self)
        self.grant_timer.timeout.connect(self._tick_grant)
        self.grant_timer.start(1000)

        title = "狗头军师 · 桌面版 %s（建议档：只给建议，不自动发）" % self.version
        if self.demo:
            title = "狗头军师 · 桌面版 %s（%s）" % (self.version, DEMO_BANNER)
        self.setWindowTitle(title)
        self.setWindowIcon(app_icon())
        self.resize(1260, 820)

        self._build_ui()
        self._build_tray()
        self._register_hotkey()
        self._start_timers()
        QTimer.singleShot(50, self._first_load)

    # ── 界面搭建 ────────────────────────────────────────────────────────
    def _build_ui(self) -> None:
        root = QWidget()
        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 8, 10, 6)
        outer.setSpacing(8)

        outer.addWidget(self._build_topbar())
        outer.addWidget(self._build_auto_bar())
        outer.addWidget(self._build_health_bar())

        splitter = QSplitter(Qt.Horizontal)
        self.session_list = SessionList()
        self.session_list.setMinimumWidth(250)
        self.session_list.selected.connect(self._on_session_selected)
        splitter.addWidget(self._wrap_card(self.session_list))

        splitter.addWidget(self._build_chat_column())

        self.tabs = QTabWidget()
        self.advice_panel = AdvicePanel()
        self.advice_panel.advice_requested.connect(self.generate_advice)
        self.advice_panel.copied.connect(self._flash)
        self.tabs.addTab(self.advice_panel, "军师")
        self.analysis_panel = AnalysisPanel()
        self.analysis_panel.analyze_requested.connect(self._on_analyze)
        self.tabs.addTab(self.analysis_panel, "关系")
        self.review_panel = ReviewPanel()
        self.tabs.addTab(self.review_panel, "复盘")
        self.tabs.addTab(self._build_profile_page(), "资料")
        self.tabs.setMinimumWidth(330)
        splitter.addWidget(self.tabs)

        splitter.setSizes([300, 620, 430])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        outer.addWidget(splitter, 1)

        self.setCentralWidget(root)

        # 状态栏：自己管消息，避免 QStatusBar.showMessage 把左边的档位信息顶掉
        self.status_left = QLabel("档位：建议档｜制动：未按下")
        self.status_message = QLabel("")
        self.status_message.setObjectName("hint")
        self.brake_label = QLabel("紧急制动：未注册")
        self.brake_label.setObjectName("hint")
        bar = self.statusBar()
        bar.addWidget(self.status_left)
        bar.addWidget(self.status_message, 1)
        bar.addPermanentWidget(self.brake_label)

    def _wrap_card(self, widget: QWidget) -> QFrame:
        """把一块内容套进白卡片，视觉上才像桌面程序而不是裸控件堆。"""
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(widget)
        return frame

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topbar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(8)

        brand = QLabel("狗头军师")
        brand.setObjectName("brand")
        layout.addWidget(brand)
        version = QLabel("桌面版 %s" % self.version)
        version.setObjectName("ver")
        layout.addWidget(version)

        layout.addSpacing(10)

        if self.demo:
            demo_tag = QLabel("◀ %s" % DEMO_BANNER)
            demo_tag.setStyleSheet(
                "color:#8a5a00;background:#fff4d6;border:1px solid #e6c26b;"
                "border-radius:6px;padding:3px 8px;font-weight:bold;"
            )
            demo_tag.setToolTip("这些聊天内容是为了看界面效果编的，不是真实记录。")
            layout.addWidget(demo_tag)

        layout.addStretch(1)

        # 档位与静默退出都收进「设置」了；顶栏只留状态（档位信息在状态栏那一行）
        self.channel_label = QLabel("")
        self.channel_label.setObjectName("hint")
        layout.addWidget(self.channel_label)

        self.settings_button = QPushButton("设置")
        self.settings_button.setToolTip("模型通道、API key、档位、静默退出、热键…")
        self.settings_button.clicked.connect(self._open_settings)
        layout.addWidget(self.settings_button)

        self.auto_button = QPushButton("全自动")
        self.auto_button.setToolTip("开启全自动档：它会替你发消息（默认先演练，不会真发）")
        self.auto_button.clicked.connect(self._open_grant_dialog)
        layout.addWidget(self.auto_button)

        self.brake_button = QPushButton("紧急制动")
        self.brake_button.setObjectName("brake")
        self.brake_button.setMinimumHeight(40)
        self.brake_button.setMinimumWidth(150)
        self.brake_button.setToolTip("停止一切自动动作，并作废已拟好未发的内容")
        self.brake_button.clicked.connect(lambda: self.brake.engage("界面按钮"))
        layout.addWidget(self.brake_button)

        self.release_button = QPushButton("解除制动")
        self.release_button.setMinimumHeight(40)
        self.release_button.setToolTip("必须手动解除，程序不会自己撤销制动")
        self.release_button.clicked.connect(lambda: self.brake.release())
        layout.addWidget(self.release_button)
        return bar

    def _build_auto_bar(self) -> QWidget:
        """全自动生效时才出现的一条状态：谁、还剩多久、还能发几条、是不是演练。"""
        self.auto_bar = QFrame()
        self.auto_bar.setObjectName("card")
        self.auto_bar.setStyleSheet(
            "QFrame#card{background:#fff4d6;border:1px solid #e6c26b;border-radius:8px;}"
        )
        layout = QHBoxLayout(self.auto_bar)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(8)
        self.auto_label = QLabel("")
        self.auto_label.setWordWrap(True)
        layout.addWidget(self.auto_label, 1)
        self.dry_button = QPushButton("演练一次")
        self.dry_button.setToolTip("演练：读最近记录 → 拟稿 → 走完整判定，但不真的发、也不抢你的屏幕")
        self.dry_button.clicked.connect(self._on_manual_step)
        layout.addWidget(self.dry_button)
        self.switch_button = QPushButton("试切一次")
        self.switch_button.setToolTip(
            "只做\"切到这个会话\"这一步，并把上游截的标题图路径给你——用来验证会不会切错人（不发送）"
        )
        self.switch_button.clicked.connect(self._on_try_switch)
        layout.addWidget(self.switch_button)
        self.watch_button = QPushButton("暂停盯人")
        self.watch_button.setToolTip("暂停后授权还在，但不会再去读新消息、也不会自动发")
        self.watch_button.clicked.connect(self._on_toggle_watch)
        layout.addWidget(self.watch_button)
        self.revoke_button = QPushButton("立即收回")
        self.revoke_button.clicked.connect(lambda: self._revoke_grant())
        layout.addWidget(self.revoke_button)
        self.auto_bar.setVisible(False)
        return self.auto_bar

    def _build_health_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("card")
        layout = QVBoxLayout(bar)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.health_label = QLabel("体检：还没跑")
        self.health_label.setWordWrap(True)
        row.addWidget(self.health_label, 1)

        self.health_detail_button = QPushButton("明细")
        self.health_detail_button.setCheckable(True)
        self.health_detail_button.toggled.connect(self._toggle_health_detail)
        row.addWidget(self.health_detail_button)

        self.health_button = QPushButton("重新体检")
        self.health_button.clicked.connect(self.refresh_health)
        row.addWidget(self.health_button)

        self.backend_button = QPushButton("启动读取后端")
        self.backend_button.setToolTip("读取后端是 Electron 程序，起来要几十秒")
        self.backend_button.clicked.connect(self._on_start_backend)
        row.addWidget(self.backend_button)
        layout.addLayout(row)

        self.health_detail = QTextBrowser()
        self.health_detail.setMaximumHeight(150)
        self.health_detail.setVisible(False)
        layout.addWidget(self.health_detail)
        return bar

    def _build_chat_column(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        head = QHBoxLayout()
        head.setSpacing(6)
        self.chat_title = QLabel("还没有选人")
        self.chat_title.setObjectName("cardTitle")
        head.addWidget(self.chat_title)
        self.chat_sub = QLabel("")
        self.chat_sub.setObjectName("hint")
        head.addWidget(self.chat_sub)
        head.addStretch(1)

        # 这个会话归不归军师管、以及它是什么关系（0.4.0）
        head.addWidget(QLabel("标签："))
        self.tag_box = QComboBox()
        for key, label in archive.SESSION_TAGS:
            self.tag_box.addItem(label, key)
        self.tag_box.setToolTip("标成「工作」的会话，军师默认不管")
        self.tag_box.currentIndexChanged.connect(self._on_tag_changed)
        head.addWidget(self.tag_box)

        self.advise_box = QCheckBox("军师参与")
        self.advise_box.setToolTip(
            "不勾：这个会话不送给模型分析，也不给建议（群聊默认不勾）"
        )
        self.advise_box.toggled.connect(self._on_advise_toggled)
        head.addWidget(self.advise_box)

        self.reload_button = QPushButton("刷新记录")
        self.reload_button.clicked.connect(self.load_messages)
        head.addWidget(self.reload_button)
        layout.addLayout(head)

        self.chat_view = ChatView()
        layout.addWidget(self.chat_view, 1)
        return frame

    def _build_profile_page(self) -> QWidget:
        self.profile_panel = ProfilePanel()
        self.profile_panel.saved.connect(lambda name: self._flash("已保存「%s」的背景" % name))
        self.profile_panel.memory_requested.connect(self._on_check_memory)
        return self.profile_panel

    # ── 托盘 ────────────────────────────────────────────────────────────
    def _build_tray(self) -> None:
        self.tray: Optional[QSystemTrayIcon] = None
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        tray = QSystemTrayIcon(app_icon(), self)
        tray.setToolTip("狗头军师 · 建议档（只给建议，不自动发）")

        menu = QMenu()
        show_action = QAction("显示主窗口", self)
        show_action.triggered.connect(self._restore_window)
        menu.addAction(show_action)
        settings_action = QAction("设置…", self)
        settings_action.triggered.connect(self._open_settings)
        menu.addAction(settings_action)
        menu.addSeparator()
        brake_action = QAction("紧急制动", self)
        brake_action.triggered.connect(lambda: self.brake.engage("托盘菜单"))
        menu.addAction(brake_action)
        release_action = QAction("解除制动", self)
        release_action.triggered.connect(lambda: self.brake.release())
        menu.addAction(release_action)
        menu.addSeparator()
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_app)
        menu.addAction(quit_action)

        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        self.tray = tray

    def _on_tray_activated(self, reason: int) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._restore_window()

    def _restore_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self._really_quit = True
        self.shutdown()
        QApplication.quit()

    # ── 全自动授权（ADR-0024） ──────────────────────────────────────────
    def _open_grant_dialog(self, preselected: str = "") -> None:
        name = self._current_name()
        username = self._current_username()
        if not username:
            QMessageBox.information(self, "先选一个人", "全自动是「点名授权」：先在左边点一个会话。")
            return
        if rules.is_group(username):
            QMessageBox.information(self, "群聊不行", "群聊永不进全自动（ADR-0004），点了也不发。")
            return
        allowed, why = rules.advice_allowed(username, archive.get_session_mark(username))
        if not allowed:
            QMessageBox.information(self, "这个不归军师管", why)
            return

        dialog = GrantDialog(
            int(self.cfg.grant_idle_minutes),
            name,
            username,
            self.demo,
            preselected,
            int(self.cfg.grant_timed_minutes),
            self,
        )
        if dialog.exec_() != QDialog.Accepted:
            return
        grant = dialog.result_grant()
        if grant is None:
            return
        self.cfg.grant_idle_minutes = int(dialog.idle_spin.value())
        self.cfg.grant_timed_minutes = int(dialog.timed_minutes())
        self.cfg.auto_watch = bool(dialog.watch_box.isChecked())
        self.cfg.auto_grant = grant.to_dict()
        # 换了授权对象/子档：把"已经回过几轮"重新数（否则新对象的轮数会沿用上一个人的）
        self.autopilot.forget(username)
        try:
            self.cfg.save()
        except Exception as exc:  # noqa: BLE001
            self._flash("授权没存上：%s" % exc)
            return
        self._refresh_auto_bar()
        self._flash(
            "已开启全自动：%s（%s）"
            % (grant.describe(), "先是演练，不会真发" if grant.dry_run else "会真的发出去")
        )

    def _revoke_grant(self, silent: bool = False, reason: str = "") -> None:
        if not self.cfg.auto_grant:
            return
        self.cfg.auto_grant = {}
        try:
            self.cfg.save()
        except Exception:  # noqa: BLE001
            pass
        self.auto_bar.setVisible(False)
        if silent:
            if reason:
                self._flash("授权已收回：%s" % reason)
        else:
            self._flash("已收回授权，回到建议档。")

    def _refresh_auto_bar(self) -> None:
        grant = rules.AutoGrant.from_dict(self.cfg.auto_grant)
        active, _why = grant.state()
        if not active:
            self.auto_bar.setVisible(False)
            return
        try:
            mode_label = rules.MODE_LABELS[rules.Mode(grant.mode)].split("（")[0]
        except Exception:  # noqa: BLE001
            mode_label = grant.mode
        quota = "不限条数" if grant.remaining < 0 else "还能自动发 %d 条" % grant.remaining
        flag = "｜演练中（不会真发）" if grant.dry_run else "｜会真的发出去"
        watching = "盯着他的新消息" if self.cfg.auto_watch else "已暂停盯人"
        self.auto_label.setText(
            "⚠ 全自动生效：%s｜对象：%s｜%s｜%s｜%s%s"
            % (
                mode_label,
                grant.name or grant.username,
                grant.relative_expiry(),
                quota,
                watching,
                flag,
            )
        )
        self.watch_button.setText("暂停盯人" if self.cfg.auto_watch else "继续盯人")
        # 演练模式与真发模式，这个按钮是同一个入口、不同的语义
        self.dry_button.setText("演练一次" if grant.dry_run else "立刻接一次")
        self.auto_bar.setVisible(True)

    def _on_manual_step(self) -> None:
        """手动跑一次。演练模式只拟稿判定；真发模式会真的切会话并发送。

        为什么留这个入口：第一次对某个人真发，必须是**手动、单次、你看着**的。
        """
        grant = rules.AutoGrant.from_dict(self.cfg.auto_grant)
        active, why = grant.state()
        if not active:
            self._flash("没有生效的授权：%s" % why)
            return
        if grant.dry_run:
            self._on_dry_run()
            return
        target = self.cfg.send_target
        pilot = self.autopilot
        self._flash("手动接一次：正在读记录、拟稿、切会话…（会把这个人的窗口提到前面）")
        self._run(lambda: pilot.step(grant, target, force=True), self._on_auto_step, key="autopilot")

    def _on_toggle_watch(self) -> None:
        self.cfg.auto_watch = not bool(self.cfg.auto_watch)
        try:
            self.cfg.save()
        except Exception as exc:  # noqa: BLE001
            self._flash("没存上：%s" % exc)
        self._refresh_auto_bar()
        self._flash("自动盯人已%s" % ("打开" if self.cfg.auto_watch else "暂停"))

    def _on_try_switch(self) -> None:
        """只做"切会话"这一步，不发送——用来验证上游会不会切错人。"""
        grant = rules.AutoGrant.from_dict(self.cfg.auto_grant)
        active, why = grant.state()
        if not active:
            self._flash("没有生效的授权：%s" % why)
            return
        name = grant.name or grant.username
        target = self.cfg.send_target
        pct = self.pct
        self._flash("正在切到这个会话（会把他窗口提到前面来）…")
        self._run(lambda: pct.switch(target, name), self._on_switch_result, key="switch")

    def _on_switch_result(self, info: Any) -> None:
        if not isinstance(info, dict):
            self._flash("切换返回看不懂：%r" % (info,))
            return
        header = str(info.get("headerImg") or "")
        switched = str(info.get("switched-to") or "")
        self._flash(
            "已切到「%s」；标题图：%s —— 请核对是不是这个人（这张图在切换时就截好了）"
            % (switched, header or "（上游没给路径）")
        )
        if self.tray and header:
            self.tray.showMessage(
                "核对一下切的是不是这个人", header, QSystemTrayIcon.Information, 9000
            )

    def _maybe_autopilot(self) -> None:
        """心跳里问一句：他的新消息到了吗？（跑在后台线程，别卡界面）"""
        if not bool(self.cfg.auto_watch):
            return
        if "autopilot" in self._running:
            return
        grant = rules.AutoGrant.from_dict(self.cfg.auto_grant)
        active, _why = grant.state()
        if not active:
            return
        now = time.time()
        interval = max(5, int(getattr(self.cfg, "auto_watch_interval_seconds", 20)))
        if now - self._last_auto_check < interval:
            return
        self._last_auto_check = now
        target = self.cfg.send_target
        pilot = self.autopilot
        self._run(lambda: pilot.step(grant, target), self._on_auto_step, key="autopilot")

    def _on_auto_step(self, step: AutoStep) -> None:
        if not step.acted:
            # "你在用电脑，所以它没抢窗口"值得说一声，但别每 20 秒念一遍
            if "正在用电脑" in (step.reason or "") and time.time() - self._last_busy_note > 300:
                self._last_busy_note = time.time()
                self._flash("自动档在等：你正在用电脑，它不抢你的窗口")
            return
        outcome = step.outcome
        if outcome is None:
            self._flash("自动档没动：%s" % step.reason)
            return
        if outcome.sent:
            # 记账：单次档发完即失效，常驻/限时扣一条
            grant = rules.AutoGrant.from_dict(self.cfg.auto_grant)
            self.cfg.auto_grant = consume(grant).to_dict()
            try:
                self.cfg.save()
            except Exception:  # noqa: BLE001
                pass
            self._refresh_auto_bar()
            self._flash("已自动发出：%s" % outcome.text)
            self._record_auto_action(outcome, dry_run=False)
        elif outcome.dry_run:
            self._flash("自动档（演练）：本来会发「%s」" % outcome.text)
            self._record_auto_action(outcome, dry_run=True)
        else:
            self._flash("自动档没发：%s" % (outcome.reason or step.reason))

    def _tick_grant(self) -> None:
        """每秒一次的快活：授权倒计时 + 当前会话的消息探测。"""
        if self.cfg.auto_grant:
            self._check_grant_expiry()
            if self.cfg.auto_grant:      # 还没被收回 → 刷新"还剩多少"
                self._refresh_auto_bar()
        self._tick_messages()

    def _tick_messages(self) -> None:
        """每秒问一句"这个会话有没有新消息"。

        只取最新**一条**（很轻），跟当前列表里最新那条比时间戳——真变了才去拉全量。
        不这么做的话，每秒重设整个列表会让你正往上翻的历史被弹回底部，还闪。
        """
        if self.demo or not self.current:
            return
        if "messages" in self._running or "msgprobe" in self._running:
            return
        username = self._current_username()
        if not username:
            return
        probe = self.wda
        self._run(
            lambda: probe.messages(username, limit=1, order="desc"),
            lambda rows: self._on_message_probe(username, rows),
            key="msgprobe",
        )

    def _on_message_probe(self, username: str, rows: List[Dict[str, Any]]) -> None:
        if username != self._current_username():
            return                       # 你已经切到别的会话了
        if not rows:
            return
        try:
            newest = int(rows[0].get("createTime") or rows[0].get("create_time") or 0)
        except (TypeError, ValueError):
            return
        if newest <= self.chat_view.newest_stamp():
            return                       # 没有新东西，别白刷
        self.load_messages(silent=True)

    def _check_grant_expiry(self) -> None:
        """过期/没额度的授权当场收回，别留一张空头授权。"""
        if not self.cfg.auto_grant:
            return
        grant = rules.AutoGrant.from_dict(self.cfg.auto_grant)
        active, why = grant.state()
        if active:
            self._refresh_auto_bar()
            return
        self._revoke_grant(silent=True, reason=why)
        if self.tray and why:
            self.tray.showMessage("自动档授权已收回", why, QSystemTrayIcon.Information, 6000)

    def _on_dry_run(self) -> None:
        """演练：用真记录拟一次稿，然后走完整判定，但强制 dry_run（绝不碰键盘）。"""
        grant = rules.AutoGrant.from_dict(self.cfg.auto_grant)
        active, why = grant.state()
        if not active:
            self._flash("现在没有生效的授权：%s" % why)
            return
        username = grant.username
        name = grant.name or username
        self._flash("演练中：正在读记录、拟稿…")
        advisor = self.advisor
        backend = self.wda
        cfg = self.cfg

        def work(on_delta, on_note, on_stage, on_reasoning):
            if self.demo:
                messages = list(DEMO_MESSAGES.get(username, []))
            else:
                ready, note = backend.ensure_backend()
                if not ready:
                    raise RuntimeError("读不到记录：%s" % note)
                messages = list(reversed(backend.messages(username, limit=cfg.read_limit, order="desc")))
            transcript = brain_mod.format_transcript(messages, limit=cfg.read_limit)
            kwargs = {
                "contact_name": name,
                "transcript": transcript,
                "junshi_text": archive.junshi_digest(2500),
                "style_text": archive.user_style(1500),
                "contact_profile": archive.contact_profile(name, 3000),
                "memory_text": "",
                "red_hits": rules.needs_human_decision(transcript),
            }
            result = advisor.advise(kwargs, on_note=on_note)
            advice = brain_mod.parse_advice(result.text)
            text = advice.first or result.text.strip()[:60]
            dry = rules.AutoGrant.from_dict(grant.to_dict())
            dry.dry_run = True                       # 演练就是演练，哪怕授权是真发
            return self.sender.dispatch(
                dry, username, name, text, expect_hash="", target=cfg.send_target
            )

        worker = StreamWorker(work, self)
        worker.note.connect(self._flash)
        worker.done.connect(self._on_dry_run_done)
        worker.failed.connect(lambda message: self._flash("演练失败：%s" % message))
        self._track(worker, "dryrun")
        worker.start()

    def _on_dry_run_done(self, outcome: SendOutcome) -> None:
        self._flash("演练结果：%s" % outcome.describe())
        self._record_auto_action(outcome, dry_run=True)

    def _record_auto_action(self, outcome: SendOutcome, dry_run: bool) -> None:
        """ADR-0024 的护栏之二：留痕 + 托盘提醒。自动档做过的每件事都要查得到。"""
        who = self._current_name() or "自动档"
        stamp = "演练（没有真发）" if dry_run else "已发出"
        body = (
            "**%s**\n\n"
            "- 拟稿：%s\n"
            "- 判定：%s\n"
            "- 目标：%s\n"
        ) % (stamp, outcome.text or "（空）", outcome.reason or outcome.describe(), outcome.target or "—")
        try:
            archive.write_review(who, body)
            self.review_panel.refresh()
        except Exception as exc:  # noqa: BLE001
            self._flash("留痕没写上：%s" % exc)
        if self.tray:
            self.tray.showMessage(
                "自动档 · %s" % stamp,
                outcome.describe()[:140],
                QSystemTrayIcon.Information,
                6000,
            )

    # ── 设置 ────────────────────────────────────────────────────────────
    def _open_settings(self) -> None:
        dialog = SettingsDialog(self.cfg, self)
        dialog.saved.connect(self._on_settings_saved)
        dialog.exec_()

    def _on_settings_saved(self) -> None:
        self.advisor = Advisor(self.cfg)          # 模型通道/兜底开关可能变了
        self._refresh_channel_label()
        self._refresh_mode_label()
        self.relabel_hotkey()

        # 设置里改档位：建议档 = 当场收回授权；全自动子档 = 引导去对某个人授权
        try:
            mode = rules.Mode(self.cfg.mode)
        except ValueError:
            mode = rules.Mode.ADVICE
        if mode is rules.Mode.ADVICE:
            if self.cfg.auto_grant:
                self._revoke_grant(silent=True, reason="你在设置里改回建议档了")
            else:
                self._flash("设置已保存（建议档）")
            return

        grant = rules.AutoGrant.from_dict(self.cfg.auto_grant)
        if not grant.username:
            self._flash(
                "选了 %s——但授权是「对某个人」给的，先点顶栏「全自动」选人"
                % rules.MODE_LABELS[mode].split("（")[0]
            )
            QTimer.singleShot(200, lambda: self._open_grant_dialog(mode.value))
            return
        if grant.mode != mode.value:
            grant.mode = mode.value
            self.cfg.auto_grant = grant.to_dict()
            try:
                self.cfg.save()
            except Exception:  # noqa: BLE001
                pass
            self._flash("子档已改成 %s" % rules.MODE_LABELS[mode].split("（")[0])
        self._refresh_auto_bar()

    def _refresh_mode_label(self) -> None:
        """状态栏左侧那一行：档位 + 制动状态。档位只能去设置里改。"""
        brake = "**已按下**" if self.brake.engaged else "未按下"
        mode = rules.Mode(self.cfg.mode) if self.cfg.mode in {m.value for m in rules.Mode} else rules.Mode.ADVICE
        self.status_left.setText("档位：%s｜制动：%s" % (mode.label.split("（")[0], brake))

    def _refresh_channel_label(self) -> None:
        ready, why = self.advisor.direct_ready()
        if ready:
            settings = self.advisor.settings
            self.channel_label.setText("大脑：%s · %s" % (settings.provider, settings.model))
            self.channel_label.setToolTip("直连通道就绪")
        else:
            self.channel_label.setText("大脑：兜底通道（未配直连）")
            self.channel_label.setToolTip("%s——点「设置」填 API key 会快很多" % why)

    # ── 后台任务 ────────────────────────────────────────────────────────
    def _track(self, worker: Any, key: str = "") -> None:
        self._workers.append(worker)
        if key:
            self._running.add(key)

        def cleanup() -> None:
            if key:
                self._running.discard(key)
            if worker in self._workers:
                self._workers.remove(worker)

        worker.finished.connect(cleanup)

    def _run(self, fn: Callable[[], Any], on_done: Callable[[Any], None], key: str = "general") -> None:
        if key in self._running:
            self._flash("上一个动作还没跑完，稍等一下…")
            return
        worker = Worker(fn, self)
        worker.done.connect(on_done)
        worker.failed.connect(lambda message: self._flash("出错：%s" % message))
        self._track(worker, key)
        worker.start()

    # ── 第一次加载 ──────────────────────────────────────────────────────
    def _first_load(self) -> None:
        self._refresh_channel_label()
        self._refresh_auto_bar()
        self.refresh_health()
        if self.demo:
            self._on_sessions_done(list(DEMO_SESSIONS))
            self.session_list.select_username(DEMO_SESSIONS[0]["username"])
            return
        self.refresh_sessions()

    # ── 体检 ────────────────────────────────────────────────────────────
    def refresh_health(self) -> None:
        self.health_label.setText("体检中…")
        self._run(
            lambda: health_mod.run_health(self.cfg, self.wda),
            self._on_health_done,
            key="health",
        )

    def _on_health_done(self, checks: List[health_mod.Check]) -> None:
        self._checks = checks
        ok = sum(1 for c in checks if c.ok)
        bad = [c for c in checks if not c.ok and c.level == "bad"]
        summary = "体检：%d/%d 通过" % (ok, len(checks))
        if bad:
            summary += "｜挡住读消息的是：" + "、".join(c.title for c in bad)
        else:
            summary += "｜可以读消息"
        self.health_label.setText(summary)
        self.health_label.setToolTip(health_mod.format_health(checks))
        self.health_detail.setPlainText(health_mod.format_health(checks))
        self.health_detail_button.setText("明细" if not bad else "明细（%d 项没过）" % len(bad))

    def _toggle_health_detail(self, shown: bool) -> None:
        self.health_detail.setVisible(shown)

    def _on_start_backend(self) -> None:
        self._flash("正在启动读取后端…（Electron 起来要几十秒）")
        self._run(self.wda.ensure_backend, self._after_backend, key="backend")

    def _after_backend(self, result: Any) -> None:
        ok, note = result
        self._flash(note)
        if ok:
            self.refresh_health()
            self.refresh_sessions()

    # ── 会话与消息 ──────────────────────────────────────────────────────
    def refresh_sessions(self) -> None:
        if self.demo:
            self._on_sessions_done(list(DEMO_SESSIONS))
            return

        def load() -> List[Dict[str, Any]]:
            ready, note = self.wda.ensure_backend()
            if not ready:
                raise RuntimeError("读不到消息：%s" % note)
            return self.wda.sessions(limit=self.cfg.session_limit)

        self._run(load, self._on_sessions_done, key="sessions")

    def _on_sessions_done(self, sessions: List[Dict[str, Any]]) -> None:
        self.sessions = list(sessions or [])
        self.session_list.set_sessions(self.sessions)

    def _on_session_selected(self, row: Dict[str, Any]) -> None:
        self.current = row
        self.load_messages()
        self.refresh_profile()

    def load_messages(self, silent: bool = False) -> None:
        if not self.current:
            if not silent:
                self._flash("先在左边点一个人")
            return
        username = self.current.get("_username") or self.current.get("username") or ""
        row = dict(self.current)

        if self.demo:
            self._on_messages_done(row, list(DEMO_MESSAGES.get(username, [])), silent=silent)
            return

        limit = max(int(self.cfg.read_limit), int(self.cfg.bubble_limit))

        def load() -> List[Dict[str, Any]]:
            return list(reversed(self.wda.messages(username, limit=limit, order="desc")))

        self._run(
            load,
            lambda messages: self._on_messages_done(row, messages, silent=silent),
            key="messages",
        )

    def _on_messages_done(
        self, row: Dict[str, Any], messages: List[Dict[str, Any]], silent: bool = False
    ) -> None:
        self.messages = messages
        self.current = row
        name = row.get("_name") or row.get("name") or "?"
        username = row.get("_username") or row.get("username") or ""
        is_group = rules.is_group(username)
        tag = "［群］" if is_group else ""
        self.chat_title.setText("%s%s" % (tag, name))
        self.chat_sub.setText("%d 条记录" % len(messages))
        # 自动刷新时保住滚动位置：你正往上翻历史，就不该被弹回底部
        self.chat_view.set_messages(
            messages, me_label="我", is_group=is_group, keep_scroll=silent
        )
        if silent:
            return

        self._apply_session_mark()
        allowed, why = rules.advice_allowed(username, archive.get_session_mark(username))
        if allowed:
            self._flash("当前：%s%s（%d 条）" % (tag, name, len(messages)))
        else:
            self._flash(why)

    # ── 关系体检（1.0.0） ───────────────────────────────────────────────
    def _on_analyze(self, limit: int) -> None:
        """把与这个人的历史记录拉出来做一次体检：本地统计 → 抽样 → 让军师解读。"""
        if not self.current:
            self._flash("先在左边点一个人")
            return
        username = self._current_username()
        name = self._current_name()
        allowed, why = rules.advice_allowed(username, archive.get_session_mark(username))
        if not allowed:
            QMessageBox.information(self, "这个不归军师管", why)
            return

        self.analysis_panel.set_busy(True, "正在读历史记录…（只读，不发任何消息）")
        advisor = self.advisor
        demo = self.demo
        backend = self.wda

        def work(on_delta, on_note, on_stage, on_reasoning):
            if demo:
                messages = list(DEMO_MESSAGES.get(username, []))
            else:
                ready, note = backend.ensure_backend()
                if not ready:
                    raise RuntimeError("读不到记录：%s" % note)
                messages = list(
                    reversed(backend.messages(username, limit=int(limit), order="desc"))
                )
            if not messages:
                raise RuntimeError("这个会话没有可分析的记录")
            stats = analysis.chat_stats(messages)
            sample = analysis.sample_for_analysis(messages, limit=200)
            on_note(
                "统计完 %d 条记录（跨度 %d 天），抽了 %d 条样本送去分析…"
                % (stats.total, stats.days_span, len(sample))
            )
            recent_text = " ".join(
                (item.get("content") or "") for item in messages[-200:]
            )
            prompt = analysis.build_analysis_prompt(
                contact_name=name,
                stats=stats,
                sample=sample,
                contact_profile=archive.contact_profile(name, 3000),
                style_text=archive.user_style(1200),
                total_count=stats.total,
                red_hits=rules.needs_human_decision(recent_text),
            )
            result = advisor.ask_prompt(prompt, on_note=on_note, on_reasoning=on_reasoning)
            return stats, analysis.parse_analysis(result.text), result, len(sample)

        worker = StreamWorker(work, self)
        worker.note.connect(self._flash)
        worker.done.connect(lambda payload: self._on_analysis_done(name, payload))
        worker.failed.connect(self._on_analysis_failed)
        self._track(worker, "analysis")
        worker.start()

    def _on_analysis_done(self, name: str, payload: Any) -> None:
        stats, sections, result, sample_size = payload
        show_elapsed = bool(getattr(self.cfg, "show_elapsed", True))
        note = "统计 %d 条（样本 %d 条）｜%s" % (
            stats.total,
            sample_size,
            result.describe(show_elapsed),
        )
        if "raw" in sections:
            note += "｜模型这次没按格式返回，原文放在【现状】里了"
        self.analysis_panel.set_busy(False, note)
        self.analysis_panel.set_stats(stats)
        self.analysis_panel.set_sections(sections, note=note)

        # 留痕：数字 + 解读一起写进复盘，以后能回看"当时是什么状况"
        body = "### 关系体检\n\n" + "\n".join("- " + line for line in stats.lines())
        body += "\n\n" + (result.text or "")
        try:
            archive.write_review(name, body)
            self.review_panel.refresh()
        except Exception as exc:  # noqa: BLE001
            self._flash("体检记录没写上：%s" % exc)

    def _on_analysis_failed(self, message: str) -> None:
        self.analysis_panel.set_busy(False, "这次没体检成。")
        self._flash("关系体检出错：%s" % message)

    # ── 军师建议（流式） ────────────────────────────────────────────────
    def generate_advice(self) -> None:
        if not self.current:
            QMessageBox.information(self, "先选一个人", "先在左边点一个会话，再让军师看一眼。")
            return
        name = self._current_name()
        username = self._current_username()
        allowed, why = rules.advice_allowed(username, archive.get_session_mark(username))
        if not allowed:
            QMessageBox.information(self, "这个不归军师管", why)
            return

        self.tabs.setCurrentWidget(self.advice_panel)
        self.advice_panel.begin_stream()

        if self.demo:
            split = DEMO_ADVICE.find("【备选】")
            quick_text = DEMO_ADVICE[:split] if split > 0 else DEMO_ADVICE
            self.advice_panel.set_quick(
                brain_mod.parse_advice(quick_text), note="极速版已出（演示数据）…"
            )
            tail = DEMO_ADVICE[split:] if split > 0 else ""
            self._demo_queue = [tail[i:i + DEMO_CHUNK] for i in range(0, len(tail), DEMO_CHUNK)]
            self._demo_timer.start(DEMO_INTERVAL_MS)
            return

        transcript = brain_mod.format_transcript(self.messages, limit=self.cfg.read_limit)
        red_hits = rules.needs_human_decision(transcript)
        advisor = self.advisor

        def work(on_delta: Callable[[str], None], on_note: Callable[[str], None],
                 on_stage: Callable[[Any], None], on_reasoning: Callable[[str], None]) -> AdviseResult:
            # 读盘（军师知识 / 说话风格 / 画像 / 记忆库）放在**后台线程**里做。
            # 它们很快（实测 0.1 秒），但 0.3.0 曾经把这几步写在主线程，
            # 结果点按钮的瞬间界面会先僵一下——别再犯。
            style = archive.user_style(1200)
            profile = archive.contact_profile(name, 1500)
            quick_kwargs = {
                "contact_name": name,
                "transcript": transcript,
                "style_text": style,
                "contact_profile": profile,
                "red_hits": red_hits,
            }
            followup_kwargs = {
                "contact_name": name,
                "transcript": transcript,
                "junshi_text": archive.junshi_digest(2500),
                "style_text": style,
                "contact_profile": profile,
                "red_hits": red_hits,
            }
            fallback_kwargs = {
                "contact_name": name,
                "transcript": transcript,
                "junshi_text": archive.junshi_digest(6000),
                "style_text": archive.user_style(3500),
                "contact_profile": archive.contact_profile(name, 6000),
                "memory_text": archive.memory_context(subject_id=name, max_chars=3000),
                "red_hits": red_hits,
            }
            if bool(getattr(self.cfg, "two_stage", True)):
                return advisor.advise_two_stage(
                    quick_kwargs,
                    followup_kwargs,
                    fallback_kwargs,
                    on_quick=on_stage,
                    on_delta=on_delta,
                    on_note=on_note,
                    on_reasoning=on_reasoning,
                )
            return advisor.advise(
                fallback_kwargs, on_delta=on_delta, on_note=on_note, on_reasoning=on_reasoning
            )

        worker = StreamWorker(work, self)
        worker.delta.connect(self.advice_panel.push_delta)
        worker.stage.connect(self._on_quick_ready)
        worker.reasoning.connect(self.advice_panel.push_reasoning)
        worker.note.connect(self._flash)
        worker.done.connect(lambda result: self._on_advice_result(name, result))
        worker.failed.connect(self._on_advice_failed)
        self._track(worker, "advice")
        worker.start()

    def _on_quick_ready(self, result: AdviseResult) -> None:
        """第一段回来了。直连就是"极速版"（判断+首选）；兜底通道则是一次出全部。"""
        if result.channel == "dsh":
            # 兜底没有分段，直接当最终结果显示，别停在"正在补…"
            self._on_advice_result("", result)
            return
        advice = brain_mod.parse_advice(result.text)
        self.advice_panel.set_quick(
            advice,
            note="首选已经出来了（首字 %.1f 秒）——正在补备选与风险…"
            % (result.first_token_ms / 1000.0 if result.first_token_ms else 0.0),
        )

    def _pump_demo_stream(self) -> None:
        if not self._demo_queue:
            self._demo_timer.stop()
            self._on_advice_result("联系人A", AdviseResult(
                text=DEMO_ADVICE, channel="direct", elapsed_ms=900, first_token_ms=320
            ))
            return
        self.advice_panel.push_delta(self._demo_queue.pop(0))

    def _on_advice_failed(self, message: str) -> None:
        self.advice_panel.end_stream(brain_mod.Advice(), note="这次没问出结果。")
        self._flash("军师出错：%s" % message)

    def _on_advice_result(self, name: str, result: AdviseResult) -> None:
        if not name and self.current:
            name = self.current.get("_name") or self.current.get("name") or ""
        advice = brain_mod.parse_advice(result.text)
        note = "建议已生成（没有发送任何东西）。"
        if not advice.structured and advice.raw:
            note = "军师这次没按格式返回，原文放在【判断】里了（没有发送任何东西）。"
        self.advice_panel.end_stream(advice, note=note)

        # 通道要如实说：兜底就是兜底，绝不让人以为它变快了
        show_elapsed = bool(getattr(self.cfg, "show_elapsed", True))
        if result.channel == "dsh":
            self._flash("这次走的是兜底通道（%s）——慢是因为它是老通道" % (result.fallback_reason or "直连不可用"))
        elif show_elapsed:
            extra = "，其中想字 %d" % result.reasoning_chars if result.reasoning_chars else ""
            self._flash(
                "直连：首字 %.1f 秒，两段合计 %.1f 秒%s"
                % (result.first_token_ms / 1000.0, result.elapsed_ms / 1000.0, extra)
            )
        else:
            self._flash("建议已生成（直连）")

        # 红线在正文里再提示一次：涉及钱、承诺、见面这类，必须本人拍板
        red_hits = rules.needs_human_decision(advice.raw)
        if red_hits:
            self.advice_panel.set_note(
                "⚠ 这条对话里有要你亲自拍板的事：%s。军师给的只能是留余地的话。" % "、".join(red_hits)
            )

        if not self.demo:
            try:
                archive.write_review(name, advice.as_transcript())
                self.review_panel.refresh()
            except Exception as exc:  # noqa: BLE001
                self._flash("复盘写盘失败：%s" % exc)

    # ── 资料页（0.4.0：能填了） ─────────────────────────────────────────
    def _current_name(self) -> str:
        if not self.current:
            return ""
        return self.current.get("_name") or self.current.get("name") or ""

    def _current_username(self) -> str:
        if not self.current:
            return ""
        return self.current.get("_username") or self.current.get("username") or ""

    def refresh_profile(self) -> None:
        self.profile_panel.set_contact(self._current_name())

    # ── 会话标记：标签 + 军师参与开关（0.4.0） ──────────────────────────
    def _apply_session_mark(self) -> None:
        """按存下来的标记，把标签框与开关摆好，并决定军师对这个会话的可用性。"""
        username = self._current_username()
        if not username:
            self.advice_panel.set_available(False, "先在左边点一个会话。")
            return
        mark = archive.get_session_mark(username)

        self.tag_box.blockSignals(True)
        position = self.tag_box.findData(str(mark.get("tag") or ""))
        self.tag_box.setCurrentIndex(position if position >= 0 else 0)
        self.tag_box.blockSignals(False)

        # 没显式设过开关时，按规则算默认值（群聊、工作类默认不参与）
        explicit = mark.get("advise")
        default_on = rules.advice_allowed(username, {"tag": mark.get("tag")})[0]
        value = bool(explicit) if explicit is not None else default_on
        self.advise_box.blockSignals(True)
        self.advise_box.setChecked(value)
        self.advise_box.blockSignals(False)

        allowed, why = rules.advice_allowed(username, mark)
        self.advice_panel.set_available(allowed, why)

    def _on_tag_changed(self, _index: int) -> None:
        username = self._current_username()
        if not username:
            return
        tag = str(self.tag_box.currentData() or "")
        try:
            archive.save_session_mark(username, tag=tag)
        except Exception as exc:  # noqa: BLE001
            self._flash("标签没存上：%s" % exc)
            return
        self._flash("已把「%s」标成「%s」" % (self._current_name(), archive.tag_label(tag)))
        self._apply_session_mark()

    def _on_advise_toggled(self, checked: bool) -> None:
        username = self._current_username()
        if not username:
            return
        try:
            archive.save_session_mark(username, advise=bool(checked))
        except Exception as exc:  # noqa: BLE001
            self._flash("开关没存上：%s" % exc)
            return
        allowed, why = rules.advice_allowed(username, archive.get_session_mark(username))
        self.advice_panel.set_available(allowed, why)
        self._flash(
            "已记住：这个会话来%s让军师出主意" % ("会" if checked else "不会")
        )

    def _on_check_memory(self) -> None:
        self._run(archive.memory_status, lambda text: self._flash("记忆库：%s" % text), key="memory")

    # ── 制动 ────────────────────────────────────────────────────────────
    def _register_hotkey(self) -> None:
        ok, note = self.brake.register()
        self.brake_label.setText("紧急制动：%s" % note)

    def relabel_hotkey(self) -> None:
        """设置里改了热键：注销旧的、注册新的（改不动就如实说）。"""
        if self.brake.hotkey == self.cfg.hotkey:
            self.brake_label.setText("紧急制动：%s" % self.brake.hotkey_note)
            return
        self.brake.unregister()
        self.brake = Brake(hotkey=self.cfg.hotkey)
        self.brake.on_change = self._on_brake_change
        self._register_hotkey()

    def _on_brake_change(self, engaged: bool, reason: str) -> None:
        if engaged:
            self.brake_button.setEnabled(False)
            self.brake_button.setText("已制动")
            self._refresh_mode_label()
            self.advice_panel.set_note(
                "已按下紧急制动：后续自动动作全部停止，已拟好未发的内容一律作废。"
                "如果刚才有一条已经进了键盘，它可能已经发出去了——这一条撤不回。"
            )
            if self.tray:
                self.tray.showMessage(
                    "已紧急制动",
                    "自动动作已停止。已进键盘的那一条撤不回。",
                    QSystemTrayIcon.Warning,
                    5000,
                )
        else:
            self.brake_button.setEnabled(True)
            self.brake_button.setText("紧急制动")
            self._refresh_mode_label()

    # ── 心跳 ────────────────────────────────────────────────────────────
    # 档位与静默退出都不在顶栏了：档位收进「设置」，静默退出时间早就在设置里。
    # 档位状态仍然在状态栏左侧那一行显示（只是不能在这里改）。
    def _start_timers(self) -> None:
        if self.demo:
            return
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._on_tick)
        self.timer.start(max(5, int(self.cfg.heartbeat_seconds)) * 1000)

    def _on_tick(self) -> None:
        self._check_grant_expiry()
        if self.brake.engaged:
            return
        self._maybe_autopilot()
        if "sessions" in self._running or "messages" in self._running:
            return
        self.refresh_sessions()

    # ── 杂项 ────────────────────────────────────────────────────────────
    def _flash(self, text: str) -> None:
        """状态栏提示：自己管，免得和左边的档位信息打架。"""
        self.status_message.setText(text)
        self._message_timer.start(MESSAGE_TIMEOUT_MS)

    def _open_folder(self, folder: Any) -> None:
        import os

        path = str(folder)
        try:
            os.makedirs(path, exist_ok=True)
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            self._flash("打不开目录：%s" % exc)

    def shutdown(self) -> None:
        """收尾：定时器、热键线程、后台任务全部停干净。

        不做这一步，进程退出时会在 Qt 收尾阶段崩掉（实测退出码 0xC0000409）。
        """
        # 退出时把全自动授权收回：免得"昨天开的授权今天还挂着"（ADR-0024 的护栏思路）
        if self.cfg.auto_grant:
            self.cfg.auto_grant = {}
            try:
                self.cfg.save()
            except Exception:  # noqa: BLE001
                pass
        for name in ("timer", "_demo_timer", "grant_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:  # noqa: BLE001
                    pass
        for worker in list(self._workers):
            try:
                worker.wait(3000)
            except Exception:  # noqa: BLE001
                pass
        self._workers.clear()
        self.brake.unregister()
        if getattr(self, "tray", None):
            self.tray.hide()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt 的命名
        if (
            not self._really_quit
            and getattr(self, "tray", None) is not None
            and self.tray.isVisible()
            and bool(self.cfg.close_to_tray)
        ):
            event.ignore()
            self.hide()
            self.tray.showMessage(
                "狗头军师还在跑",
                "窗口收进托盘了。点托盘图标可以再打开，右键有紧急制动。",
                QSystemTrayIcon.Information,
                4000,
            )
            return
        self.shutdown()
        super().closeEvent(event)


def run_ui(demo: bool = False) -> int:
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(theme.app_style())
    window = MainWindow(demo=demo)
    # 无论是关窗还是退出应用，都要走同一条收尾路径
    app.aboutToQuit.connect(window.shutdown)
    window.show()
    return app.exec_()
