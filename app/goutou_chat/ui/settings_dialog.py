"""设置窗口：军师大脑（直连通道）+ 界面与行为。

为什么单开一个窗口而不是塞进侧栏：这些项是"装一次、之后很少动"的，混在日常
看聊天的地方只会碍事。

九项（v0.3.0 定的清单）：
大脑组  ① 模型通道 ② API key ③ 测试连接 ④ 兜底开关
界面组  ⑤ 静默退出分钟 ⑥ 紧急制动热键 ⑦ 气泡条数 ⑧ 显示本次耗时 ⑨ 数据根（只读）
另加一项「关闭时收进托盘」——这个行为不写出来，人会找不到怎么退出。

两条规矩：
- **API key 不进 config.json**，单独存 `关系档案\\配置\\secrets.json`，并且在
  `.gitignore` 里封死；界面上只显示掩码。
- 「测试连接」是**真发一个最小请求**（几个 token），不是假装通——不做假绿。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .. import advisor as advisor_mod
from .. import config as cfgmod
from .. import llm
from .. import rules
from .workers import Worker


class SettingsDialog(QDialog):
    """设置。保存后发 `saved` 信号，主窗口据此重新加载大脑设置。"""

    saved = pyqtSignal()

    def __init__(self, cfg: Optional[cfgmod.Config] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.cfg = cfg or cfgmod.Config.load()
        self.setWindowTitle("设置 · 狗头军师")
        self.setMinimumWidth(620)
        self._workers: List[Worker] = []

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(self._build_brain_group())
        layout.addWidget(self._build_ui_group())
        layout.addWidget(self._build_footer())

        self._load_from_config()
        self._on_provider_changed()

    # ── 大脑组 ──────────────────────────────────────────────────────────
    def _build_brain_group(self) -> QWidget:
        box = QGroupBox("军师大脑（直连，比旧的子进程通道快）")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)

        self.provider_box = QComboBox()
        for provider in llm.PROVIDERS:
            self.provider_box.addItem(provider.label, provider.key)
            index = self.provider_box.count() - 1
            if provider.note:
                self.provider_box.setItemData(index, provider.note, Qt.ToolTipRole)
        self.provider_box.currentIndexChanged.connect(self._on_provider_changed)
        form.addRow("提供方：", self.provider_box)

        self.base_url_edit = QLineEdit()
        self.base_url_edit.setPlaceholderText("接口地址（OpenAI 兼容的 /chat/completions 前缀）")
        form.addRow("接口地址：", self.base_url_edit)

        self.model_box = QComboBox()
        self.model_box.setEditable(True)          # 允许手填新模型名，不用等我改代码
        form.addRow("模型：", self.model_box)

        # 这一项是本项目最要命的参数：智谱 glm-5.3 系列默认 "max"（深度思考），
        # 实测先想 18537 字、首字等 136 秒；设成 "low" 后首字 8.4 秒、极速版 1.35 秒。
        self.effort_box = QComboBox()
        for label, value in (
            ("低（快，推荐）", "low"),
            ("高（增强推理）", "high"),
            ("深（默认档，实测首字 136 秒）", "max"),
            ("不发这个参数（给不认识它的提供方）", ""),
        ):
            self.effort_box.addItem(label, value)
        self.effort_box.setToolTip(
            "控制模型「想多久」。智谱 glm-5.3 系列默认是「深」，会先想一两万字才吐第一个字；\n"
            "设成「低」实测能把首字从 136 秒降到 8 秒。别的提供方不一定认这个参数，\n"
            "认不出时程序会自动去掉它重试一次。"
        )
        form.addRow("思考档位：", self.effort_box)

        self.two_stage_box = QCheckBox("两段式：先给一句首选（约 1.4 秒），再补备选与风险")
        self.two_stage_box.setToolTip(
            "首字延迟几乎只跟输入长度有关。第一段用瘦任务书（不带军师知识）抢时间，\n"
            "第二段带上军师知识补退路与风险。"
        )
        form.addRow("", self.two_stage_box)

        key_row = QHBoxLayout()
        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.Password)
        self.key_edit.setPlaceholderText("粘进 API key（只存在本机数据根里）")
        key_row.addWidget(self.key_edit, 1)
        self.show_key_box = QCheckBox("显示")
        self.show_key_box.toggled.connect(self._on_toggle_key_visible)
        key_row.addWidget(self.show_key_box)
        holder = QWidget()
        holder.setLayout(key_row)
        form.addRow("API key：", holder)

        self.key_state_label = QLabel("")
        self.key_state_label.setObjectName("hint")
        self.key_state_label.setWordWrap(True)
        form.addRow("", self.key_state_label)

        test_row = QHBoxLayout()
        self.test_button = QPushButton("测试连接")
        self.test_button.setToolTip("真发一个最小请求，用毫秒数告诉你通不通")
        self.test_button.clicked.connect(self._on_test_connection)
        test_row.addWidget(self.test_button)
        self.test_label = QLabel("还没测")
        self.test_label.setObjectName("hint")
        self.test_label.setWordWrap(True)
        test_row.addWidget(self.test_label, 1)
        test_holder = QWidget()
        test_holder.setLayout(test_row)
        form.addRow("", test_holder)

        self.fallback_box = QCheckBox("直连不可用时，自动回退到旧的慢通道（DSH 子进程）兜底")
        self.fallback_box.setToolTip(
            "回退时状态栏会写明「这次是兜底」，不会让你以为它变快了。"
        )
        form.addRow("", self.fallback_box)
        return box

    # ── 界面与行为组 ────────────────────────────────────────────────────
    def _build_ui_group(self) -> QWidget:
        box = QGroupBox("界面与行为")
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignRight)

        # 档位从顶栏搬进来了（顶栏只留状态）。0.6.0 起全自动真的能发，所以这里**可以选**：
        # 选建议档会当场收回授权；选全自动子档会引导你去点「全自动」对某个人授权
        # （授权是"对某个人"的事，光改一个全局档位没有意义）。
        self.mode_box = QComboBox()
        for mode in (
            rules.Mode.ADVICE,
            rules.Mode.AUTO_ONCE,
            rules.Mode.AUTO_STANDING,
            rules.Mode.AUTO_TIMED,
        ):
            self.mode_box.addItem(mode.label, mode.value)
            if mode.is_auto:
                index = self.mode_box.count() - 1
                self.mode_box.setItemData(
                    index,
                    "选中后会引导你去「全自动」按钮对某个人授权——授权是按人给的，不是一个全局开关",
                    Qt.ToolTipRole,
                )
        self.mode_box.setToolTip(
            "建议档：只给草稿，你自己发。\n"
            "全自动档：程序直接发（要按人授权、可随时收回、默认先演练）。"
        )
        form.addRow("档位：", self.mode_box)

        self.exit_spin = QSpinBox()
        self.exit_spin.setRange(1, 240)
        self.exit_spin.setSuffix(" 分钟")
        self.exit_spin.setToolTip("全自动态在最后一次动作后多久自动退出（ADR-0014）。第一版只有建议档，这项先备着")
        form.addRow("静默退出：", self.exit_spin)

        self.hotkey_edit = QLineEdit()
        self.hotkey_edit.setPlaceholderText("例如 Ctrl+Alt+Esc")
        form.addRow("紧急制动热键：", self.hotkey_edit)

        self.bubble_spin = QSpinBox()
        self.bubble_spin.setRange(30, 2000)
        self.bubble_spin.setSuffix(" 条")
        self.bubble_spin.setToolTip("聊天区一次读多少条（太多会拖慢重绘）")
        form.addRow("聊天区显示：", self.bubble_spin)

        self.elapsed_box = QCheckBox("状态栏显示「这次花了多少秒 / 首字多少秒」")
        form.addRow("", self.elapsed_box)

        self.tray_box = QCheckBox("点关闭按钮时收进托盘（不退出）")
        form.addRow("", self.tray_box)

        # 全自动的"别打断我"开关（0.6.1）
        self.respect_busy_box = QCheckBox("我在用电脑时，全自动先等着（不抢窗口、不打断我打字）")
        self.respect_busy_box.setToolTip(
            "键盘注入必须把目标窗口抢到最前面。勾上 = 你正在用电脑（5 秒内有键鼠操作）时它推迟；\n"
            "取消勾选 = 照发不误（会抢你的窗口）。手动点「立刻接一次」不受这条限制。"
        )
        form.addRow("", self.respect_busy_box)

        root_row = QHBoxLayout()
        self.root_edit = QLineEdit()
        self.root_edit.setReadOnly(True)
        root_row.addWidget(self.root_edit, 1)
        open_button = QPushButton("打开")
        open_button.clicked.connect(self._on_open_root)
        root_row.addWidget(open_button)
        root_holder = QWidget()
        root_holder.setLayout(root_row)
        form.addRow("数据根：", root_holder)
        return box

    def _build_footer(self) -> QWidget:
        row = QHBoxLayout()
        restore = QPushButton("恢复默认")
        restore.setToolTip("只把上面的值改回默认，点保存才真正写盘")
        restore.clicked.connect(self._restore_defaults)
        row.addWidget(restore)
        row.addStretch(1)

        self.status_label = QLabel("")
        self.status_label.setObjectName("hint")
        row.addWidget(self.status_label)

        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)

        save = QPushButton("保存")
        save.setObjectName("primary")
        save.clicked.connect(self._on_save)
        row.addWidget(save)

        holder = QWidget()
        holder.setLayout(row)
        return holder

    # ── 载入 / 回填 ─────────────────────────────────────────────────────
    def _load_from_config(self) -> None:
        cfg = self.cfg
        provider_key = str(getattr(cfg, "llm_provider", llm.DEFAULT_PROVIDER))
        position = self.provider_box.findData(provider_key)
        self.provider_box.setCurrentIndex(position if position >= 0 else 0)

        self.base_url_edit.setText(str(getattr(cfg, "llm_base_url", "") or ""))
        self._fill_models(str(getattr(cfg, "llm_model", llm.DEFAULT_MODEL)))
        effort = str(getattr(cfg, "llm_reasoning_effort", "low") or "")
        effort_pos = self.effort_box.findData(effort)
        self.effort_box.setCurrentIndex(effort_pos if effort_pos >= 0 else 0)
        self.two_stage_box.setChecked(bool(getattr(cfg, "two_stage", True)))

        self.fallback_box.setChecked(bool(getattr(cfg, "fallback_to_dsh", True)))
        mode = str(getattr(cfg, "mode", rules.Mode.ADVICE.value))
        mode_pos = self.mode_box.findData(mode)
        self.mode_box.setCurrentIndex(mode_pos if mode_pos >= 0 else 0)
        self.exit_spin.setValue(int(getattr(cfg, "auto_exit_minutes", 15)))
        self.hotkey_edit.setText(str(getattr(cfg, "hotkey", "Ctrl+Alt+Esc")))
        self.bubble_spin.setValue(int(getattr(cfg, "bubble_limit", 200)))
        self.elapsed_box.setChecked(bool(getattr(cfg, "show_elapsed", True)))
        self.tray_box.setChecked(bool(getattr(cfg, "close_to_tray", True)))
        self.respect_busy_box.setChecked(bool(getattr(cfg, "auto_respect_busy", True)))
        self.root_edit.setText(str(cfgmod.archive_root()))

        stored = cfgmod.load_api_key()
        if stored:
            self.key_state_label.setText("已存一把 key：%s（要换就直接在上面粘新的）" % cfgmod.mask_secret(stored))
        else:
            self.key_state_label.setText("还没存 key。粘一把进去，点「测试连接」确认能用。")

    def _fill_models(self, current: str) -> None:
        self.model_box.clear()
        provider = llm.PROVIDER_MAP.get(self.provider_box.currentData() or "")
        for name in (provider.models if provider else []):
            self.model_box.addItem(name)
        if current and self.model_box.findText(current) < 0:
            self.model_box.addItem(current)
        self.model_box.setCurrentText(current or (provider.models[0] if provider and provider.models else ""))

    def _on_provider_changed(self) -> None:
        provider = llm.PROVIDER_MAP.get(self.provider_box.currentData() or "")
        if not provider:
            return
        # 地址：预置只作为预填建议；用户自己填过就尊重用户的
        if not self.base_url_edit.text().strip() or self.base_url_edit.text().strip() in {
            p.base_url for p in llm.PROVIDERS if p.base_url
        }:
            self.base_url_edit.setText(provider.base_url)
        if provider.note:
            self.test_label.setText(provider.note)
        self._fill_models(self.model_box.currentText().strip())

    def _on_toggle_key_visible(self, shown: bool) -> None:
        self.key_edit.setEchoMode(QLineEdit.Normal if shown else QLineEdit.Password)

    # ── 测试连接 ────────────────────────────────────────────────────────
    def _current_settings(self) -> llm.LlmSettings:
        typed = self.key_edit.text().strip()
        effort = self.effort_box.currentData()
        return llm.LlmSettings(
            provider=str(self.provider_box.currentData() or ""),
            base_url=self.base_url_edit.text().strip(),
            model=self.model_box.currentText().strip(),
            api_key=typed or cfgmod.load_api_key(),
            timeout=30,
            reasoning_effort="" if effort is None else str(effort),
        )

    def _on_test_connection(self) -> None:
        settings = self._current_settings()
        self.test_button.setEnabled(False)
        self.test_label.setText("正在真发一个最小请求…")
        worker = Worker(lambda: llm.test_connection(settings), self)
        self._workers.append(worker)

        def done(result: Any) -> None:
            if worker in self._workers:
                self._workers.remove(worker)
            ok, note, first_ms, total_ms = result
            self.test_button.setEnabled(True)
            if ok:
                self.test_label.setText(
                    "✓ %s｜首字 %d 毫秒，总计 %d 毫秒" % (note, first_ms, total_ms)
                )
            else:
                self.test_label.setText("✗ %s" % note)

        def failed(message: str) -> None:
            if worker in self._workers:
                self._workers.remove(worker)
            self.test_button.setEnabled(True)
            self.test_label.setText("✗ %s" % message)

        worker.done.connect(done)
        worker.failed.connect(failed)
        worker.start()

    # ── 保存 ────────────────────────────────────────────────────────────
    def _restore_defaults(self) -> None:
        from ..llm import DEFAULT_MODEL, DEFAULT_PROVIDER

        self.provider_box.setCurrentIndex(max(0, self.provider_box.findData(DEFAULT_PROVIDER)))
        self.base_url_edit.clear()
        self._fill_models(DEFAULT_MODEL)
        self.effort_box.setCurrentIndex(0)          # 低
        self.two_stage_box.setChecked(True)
        self.fallback_box.setChecked(True)
        self.mode_box.setCurrentIndex(max(0, self.mode_box.findData(rules.Mode.ADVICE.value)))
        self.exit_spin.setValue(15)
        self.hotkey_edit.setText("Ctrl+Alt+Esc")
        self.bubble_spin.setValue(200)
        self.elapsed_box.setChecked(True)
        self.tray_box.setChecked(True)
        self.status_label.setText("已改回默认值，点「保存」才写盘")

    def _collect(self) -> Dict[str, Any]:
        effort = self.effort_box.currentData()
        return {
            "llm_provider": str(self.provider_box.currentData() or llm.DEFAULT_PROVIDER),
            "llm_base_url": self.base_url_edit.text().strip(),
            "llm_model": self.model_box.currentText().strip(),
            "llm_reasoning_effort": "" if effort is None else str(effort),
            "two_stage": bool(self.two_stage_box.isChecked()),
            "fallback_to_dsh": bool(self.fallback_box.isChecked()),
            "mode": str(self.mode_box.currentData() or rules.Mode.ADVICE.value),
            "auto_exit_minutes": int(self.exit_spin.value()),
            "hotkey": self.hotkey_edit.text().strip() or "Ctrl+Alt+Esc",
            "bubble_limit": int(self.bubble_spin.value()),
            "show_elapsed": bool(self.elapsed_box.isChecked()),
            "close_to_tray": bool(self.tray_box.isChecked()),
            "auto_respect_busy": bool(self.respect_busy_box.isChecked()),
        }

    def _on_save(self) -> None:
        updates = self._collect()
        for key, value in updates.items():
            setattr(self.cfg, key, value)

        typed = self.key_edit.text().strip()
        try:
            if typed:
                cfgmod.save_api_key(typed)
            self.cfg.save()
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText("存不上：%s" % exc)
            return

        self._load_from_config()
        self.key_edit.clear()
        self.show_key_box.setChecked(False)
        self.status_label.setText("已保存")
        self.saved.emit()

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt 命名
        for worker in list(self._workers):
            try:
                worker.wait(2000)
            except Exception:  # noqa: BLE001
                pass
        self._workers.clear()
        super().closeEvent(event)

    def _on_open_root(self) -> None:
        import os

        try:
            os.startfile(str(cfgmod.archive_root()))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            self.status_label.setText("打不开：%s" % exc)


def key_summary() -> "Tuple[bool, str]":
    """给别处用的一行摘要（比如主窗口状态栏）。"""
    settings = advisor_mod.make_settings()
    ok, why = settings.ready()
    if not ok:
        return False, why
    return True, "%s · %s" % (settings.provider, settings.model)
