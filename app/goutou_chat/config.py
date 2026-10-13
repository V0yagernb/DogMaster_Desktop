"""路径与配置：所有可调项集中在这里，方便排障与整体搬迁。

规矩（见 docs/决策记录.md）：
- ADR-0008：数据只住在项目的 `关系档案\\` 下，本程序是它的独占写入者。
- ADR-0009：军师知识只引用、不复制。
- ADR-0010：上游零件用冻结副本，不依赖另一个工作区。
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List

APP_NAME = "goutou-chat"
VERSION_FILE = "VERSION"
ARCHIVE_DIRNAME = "关系档案"

# ── 读取后端 ──────────────────────────────────────────────────────────────
DEFAULT_BACKEND_PORT = 10392

# ── 外部依赖的绝对位置（只读引用，不复制） ────────────────────────────────
# 上游「进程聊天工具」技能包：排障时用 wc.ps1，冻结副本的同步来源。
# 换机器请设 GOUTOU_PCT_SKILL；下面那个默认值是开发机上的位置。
UPSTREAM_PCT_SKILL = Path(
    os.environ.get("GOUTOU_PCT_SKILL") or r"F:\AI小文件\.dsh\skills\process-chat-tool"
)
# 狗头军师技能包：只按需读它的 references，不复制（ADR-0009）。
JUNSHI_SKILL = Path(os.path.expanduser("~")) / ".agents" / "skills" / "goutoujunshi"
# DSH 的 CLI 入口（实测：node bin.js headless "<任务>" 约 3 秒返回）。
# 换机器请设 GOUTOU_DSH_BIN；下面那个默认值是开发机上的位置。
DSH_BIN = Path(
    os.environ.get("GOUTOU_DSH_BIN")
    or r"F:\AI小文件\vendor\dsh-0.2\node_modules\@deepseek-ai\dsh\lib\bin.js"
)
WDA_EXE = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Programs"
    / "WeChatDataAnalysis"
    / "WeChatDataAnalysis.exe"
)
WDA_DB_ROOT = (
    Path(os.environ.get("APPDATA", ""))
    / "wechat-data-analysis-desktop"
    / "output"
    / "databases"
)


def project_home() -> Path:
    """项目根目录。

    优先级：环境变量 GOUTOU_HOME > 从 exe / 源码位置往上找带 VERSION 与
    关系档案 的目录 > 按源码位置推算。

    为什么要看 sys.executable：打包成 exe 后 `__file__` 指向的是 PyInstaller
    解压出来的临时目录（_MEIxxxx），那里既没有 VERSION 也没有 关系档案。
    exe 放在 `app\dist\` 下时，从 exe 位置往上两级正好是项目根。
    """
    env = os.environ.get("GOUTOU_HOME")
    if env:
        return Path(env).expanduser().resolve()

    here = Path(__file__).resolve()
    candidates: List[Path] = []
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir)
        candidates.extend(exe_dir.parents)
    candidates.append(here)
    candidates.extend(here.parents)

    for parent in candidates:
        if (parent / VERSION_FILE).exists() and (parent / ARCHIVE_DIRNAME).exists():
            return parent
    if getattr(sys, "frozen", False):
        # exe 被单独拷走、又没设 GOUTOU_HOME：退到 exe 所在目录（数据会建在那儿）
        return Path(sys.executable).resolve().parent
    # app/goutou_chat/config.py -> goutou_chat -> app -> 项目根
    return here.parents[2]


def archive_root() -> Path:
    return project_home() / ARCHIVE_DIRNAME


def app_version() -> str:
    """版本号统一从根目录的 VERSION 文件读。"""
    try:
        return (project_home() / VERSION_FILE).read_text(encoding="utf-8").strip() or "0.0.0"
    except Exception:
        return "0.0.0"


# ── 密钥（直连通道用；与 config.json 分开存） ────────────────────────────
# 单独一个文件的原因：config.json 是要跟着项目走的（能看、能备份、能改），
# API key 不能——它只住在数据根里，且必须在 .gitignore 里封死。
def secrets_file() -> Path:
    return archive_root() / "配置" / "secrets.json"


def load_api_key() -> str:
    path = secrets_file()
    if not path.exists():
        return ""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    value = raw.get("api_key") if isinstance(raw, dict) else ""
    return str(value or "").strip()


def save_api_key(key: str) -> Path:
    """只写这一个小文件，权限尽量收紧（Windows 下不保证，但至少不放在仓库里）。"""
    ensure_dirs()
    path = secrets_file()
    path.write_text(
        json.dumps({"api_key": (key or "").strip()}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def mask_secret(value: str) -> str:
    """给界面显示用：只露头尾，中间打码。"""
    text = (value or "").strip()
    if not text:
        return "（没填）"
    if len(text) <= 8:
        return "*" * len(text)
    return "%s%s%s" % (text[:4], "*" * 8, text[-4:])


def config_file() -> Path:
    return archive_root() / "配置" / "config.json"


def session_marks_file() -> Path:
    """每个会话的标记（标签 + 是否让军师出主意）。

    单独一个文件、按 username 存：会话名会改，username 不会；而且这份东西是
    使用者的判断（"这个人归军师管吗"），与程序配置分开更好备份与回滚。
    """
    return archive_root() / "配置" / "sessions.json"


def profile_dir() -> Path:
    return archive_root() / "画像"


def contacts_dir() -> Path:
    return profile_dir() / "contacts"


def memory_dir() -> Path:
    return archive_root() / "记忆库"


def corpus_dir() -> Path:
    return archive_root() / "语料"


def review_dir() -> Path:
    return archive_root() / "复盘"


def log_dir() -> Path:
    return archive_root() / "日志"


def screenshot_dir() -> Path:
    return archive_root() / "截图"


def vendor_dir() -> Path:
    return project_home() / "vendor-frozen"


def pct_script() -> Path:
    return vendor_dir() / "scripts" / "pct.ps1"


def targets_dir() -> Path:
    return vendor_dir() / "targets"


def ensure_dirs() -> None:
    """把运行期需要的目录补齐（幂等）。"""
    for d in (
        archive_root(),
        config_file().parent,
        profile_dir(),
        contacts_dir(),
        memory_dir(),
        corpus_dir(),
        review_dir(),
        log_dir(),
    ):
        d.mkdir(parents=True, exist_ok=True)


@dataclass
class Config:
    """本程序的全部可调项。改这里等于改默认值，运行时以 config.json 为准。"""

    # 档位：advice（建议档）/ auto_once / auto_standing / auto_timed
    mode: str = "advice"
    # ADR-0014：全自动静默多久后自动退出（分钟，界面里可改）
    auto_exit_minutes: int = 15
    # ADR-0013：紧急制动热键，仅全自动态生效
    hotkey: str = "Ctrl+Alt+Esc"

    # 读取后端
    backend_port: int = DEFAULT_BACKEND_PORT
    backend_auto_start: bool = True
    backend_wait_seconds: int = 150

    # 读取与刷新
    read_limit: int = 30
    session_limit: int = 300
    heartbeat_seconds: int = 15

    # 界面：点关闭按钮是收进托盘还是直接退出（默认收进托盘，别把盯消息的人赶走）
    close_to_tray: bool = True
    # 界面：气泡区最多显示多少条（太多会拖慢重绘）
    bubble_limit: int = 200
    # 界面：状态栏显示"这次花了多少秒"
    show_elapsed: bool = True

    # 直连通道（ADR-0019）：直连拿不到结果时，是否自动回退到 DSH 子进程兜底
    llm_provider: str = "zhipu-cn"
    llm_base_url: str = ""          # 空 = 用预置地址
    llm_model: str = "glm-5.3-flash"
    llm_temperature: float = 0.7
    llm_timeout_seconds: int = 120
    fallback_to_dsh: bool = True
    # 思考档位（智谱 glm-5.3 系列只有 low/high/max，**默认是 max**）。
    # 不设成 low 的代价实测过：先想 18537 字、首字等 136 秒。这里是本项目最要命的一项。
    # 空串 = 不发这个参数（给不认识它的提供方用）。
    llm_reasoning_effort: str = "low"
    # 两段式：先极速给一句首选，再补备选与风险（首字从 ~3 秒降到 ~1.4 秒）
    two_stage: bool = True

    # ── 全自动授权（ADR-0024） ──────────────────────────────────────────
    # 存成普通 dict，读的时候用 rules.AutoGrant.from_dict 容错解析（烂数据退成"演练+未武装"）
    auto_grant: Dict[str, Any] = field(default_factory=dict)
    # 授权挂着但一直没触发，多久自动收回（分钟）——ADR-0024 的三条护栏之一，不可关闭
    grant_idle_minutes: int = 30
    # 限时档的授权时长（分钟）：到点自动收回
    grant_timed_minutes: int = 60
    # 上游发送零件用的 profile 名（vendor-frozen\targets\*.json）
    send_target: str = "wechat"
    # 是不是盯着新消息自动接（ADR-0024）。授权生效即生效；状态条上有"暂停盯人"随时停。
    auto_watch: bool = False
    # 两次自动检查之间至少隔多久（秒），免得心跳太密一直问后端
    auto_watch_interval_seconds: int = 20
    # "你正在用电脑就别抢窗口"的判定门槛（秒）：这段时间内有键鼠操作就推迟自动发送。
    # 键盘注入必须抢前台，没有这条闸，你切出去打字会被它打断（实测反馈就是这个）。
    auto_idle_guard_seconds: int = 5
    # 要不要守这条闸。守 = 你用电脑时它等着（不打断你，但你在电脑前一整天它就一整天不发）；
    # 不守 = 照发不误（会抢你的窗口）。默认守，因为"不打断人"比"秒回"重要。
    auto_respect_busy: bool = True

    # 大脑（ADR-0007）
    dsh_model: str = ""
    dsh_timeout_seconds: int = 240

    # 留存（ADR-0006：截图用完即删）
    keep_screenshot: bool = False

    # 白名单（ADR-0004）
    whitelist_standing: List[str] = field(default_factory=list)
    whitelist_timed: List[str] = field(default_factory=list)
    never_auto_groups: bool = True

    # 停手条件（ADR-0002 / Q20）
    max_turns: int = 15
    stop_after_short_replies: int = 2
    stop_after_no_substance: int = 3
    stop_after_minutes: int = 15

    log_level: str = "INFO"

    # ── 存取 ────────────────────────────────────────────────────────────
    @classmethod
    def load(cls) -> "Config":
        ensure_dirs()
        path = config_file()
        cfg = cls()
        if path.exists():
            try:
                raw: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return cfg
            for key, value in raw.items():
                if hasattr(cfg, key):
                    setattr(cfg, key, value)
        else:
            cfg.save()
        return cfg

    def save(self) -> Path:
        ensure_dirs()
        path = config_file()
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return path

    def is_whitelisted(self, name: str) -> bool:
        return name in self.whitelist_standing or name in self.whitelist_timed
