"""档案读写：画像、记忆库、复盘、军师知识引用。

本程序是 `关系档案\\` 的唯一写入者（ADR-0008）。
军师知识只读引用、不复制（ADR-0009）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import config as cfgmod

# 军师知识里最常用的两份：技能正文（规矩）与话术编排器（怎么落笔）
JUNSHI_CORE = "SKILL.md"
JUNSHI_TACTICS = "references/practical/实战话术编排器：从一句回复到后续分支.md"


def _read(path: Path, limit_chars: int) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return ""
    if limit_chars and len(text) > limit_chars:
        return text[:limit_chars] + "\n…（已截断）"
    return text


# ── 军师知识（只引用） ──────────────────────────────────────────────────
def junshi_available() -> bool:
    return cfgmod.JUNSHI_SKILL.exists()


def junshi_digest(limit_chars: int = 9000) -> str:
    """按需取军师知识：技能正文 + 话术编排器（ADR-0009，只读不复制）。"""
    root = cfgmod.JUNSHI_SKILL
    if not root.exists():
        return ""
    core = _read(root / JUNSHI_CORE, 3000)
    tactics = _read(root / JUNSHI_TACTICS, max(0, limit_chars - len(core)))
    return (core + "\n\n" + tactics).strip()


def junshi_reference(name: str, limit_chars: int = 4000) -> str:
    """按文件名取一份军师参考（排障与按需加载用）。"""
    root = cfgmod.JUNSHI_SKILL
    for path in root.rglob(name):
        if path.is_file():
            return _read(path, limit_chars)
    return ""


# ── 画像 ────────────────────────────────────────────────────────────────
def user_style(limit_chars: int = 4000) -> str:
    return _read(cfgmod.profile_dir() / "user-style.md", limit_chars)


def examples(limit_chars: int = 4000) -> str:
    return _read(cfgmod.profile_dir() / "examples.md", limit_chars)


def contact_profile(name: str, limit_chars: int = 2500) -> str:
    """这个人的档案。按名字找 contacts\\ 下的 md（精确优先，再按包含）。"""
    folder = cfgmod.contacts_dir()
    if not folder.exists():
        return ""
    want = (name or "").strip()
    files = sorted(folder.glob("*.md"))
    for path in files:
        if path.stem == want:
            return _read(path, limit_chars)
    for path in files:
        if want and (want in path.stem or path.stem in want):
            return _read(path, limit_chars)
    return ""


def known_contacts() -> List[str]:
    folder = cfgmod.contacts_dir()
    if not folder.exists():
        return []
    return sorted(p.stem for p in folder.glob("*.md"))


def write_profile(name: str, text: str) -> Path:
    """写某个对象的画像（追加到文件末尾）。"""
    cfgmod.ensure_dirs()
    path = cfgmod.contacts_dir() / ("%s.md" % name)
    if not path.exists():
        path.write_text("# %s\n\n（本程序自动维护，可手工修改）\n\n" % name, encoding="utf-8")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n" + text.rstrip() + "\n")
    return path


# ── 复盘（ADR-0018） ────────────────────────────────────────────────────
def write_review(contact: str, body: str, when: Optional[datetime] = None) -> Path:
    cfgmod.ensure_dirs()
    stamp = (when or datetime.now()).strftime("%Y-%m-%d")
    path = cfgmod.review_dir() / ("%s-%s.md" % (stamp, contact))
    header = "# %s 复盘 · %s\n\n" % (contact, stamp)
    if not path.exists():
        path.write_text(header, encoding="utf-8")
    with path.open("a", encoding="utf-8") as handle:
        handle.write("## %s\n\n%s\n\n" % (datetime.now().strftime("%H:%M"), body.rstrip()))
    return path


# ── 军师记忆库（走原脚本，只换存储位置） ────────────────────────────────
def memory_script() -> Path:
    return cfgmod.JUNSHI_SKILL / "scripts" / "memory_store.py"


def memory_status() -> str:
    code, out = _run_memory(["status"])
    return out.strip() or ("memory_store 退出码 %d" % code)


def memory_context(subject_id: str = "", max_chars: int = 4000) -> str:
    """按对象召回压缩上下文；没启用或没记录就返回空（不编造）。"""
    args = ["context", "--max-chars", str(max_chars)]
    if subject_id:
        args += ["--subject-id", subject_id]
    code, out = _run_memory(args)
    if code != 0:
        return ""
    return out.strip()


def memory_enable_help() -> str:
    return (
        "长期记忆还没启用。启用（需要本人明确同意）：\n"
        "  python %s enable --confirm" % memory_script()
    )


def _run_memory(args: List[str]) -> Tuple[int, str]:
    """调军师自带的记忆库脚本，并把它指到本项目的档案根（ADR-0008）。"""
    script = memory_script()
    if not script.exists():
        return 127, "找不到记忆库脚本：%s" % script
    env = dict(os.environ)
    env["GOUTOUJUNSHI_MEMORY_DIR"] = str(cfgmod.memory_dir())
    try:
        proc = subprocess.run(
            [sys.executable, str(script)] + args,
            capture_output=True,
            timeout=60,
            env=env,
        )
    except Exception as exc:  # noqa: BLE001
        return 1, "记忆库调用失败：%s" % exc
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    return proc.returncode, (out + err)


# ── 会话标记：标签 + 军师参与开关（0.4.0） ──────────────────────────────
SESSION_TAGS: List[Tuple[str, str]] = [
    ("", "未标记"),
    ("crush", "恋爱"),
    ("flirt", "暧昧"),
    ("friend", "朋友"),
    ("family", "家人"),
    ("work", "工作"),
    ("other", "其他"),
]

TAG_LABELS: Dict[str, str] = dict(SESSION_TAGS)


def tag_label(tag: str) -> str:
    return TAG_LABELS.get((tag or "").strip(), "未标记")


def load_session_marks() -> Dict[str, Dict[str, Any]]:
    """读全部会话标记。按 username 存：会话名会改，username 不会。"""
    path = cfgmod.session_marks_file()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): value for key, value in raw.items() if isinstance(value, dict)}


def get_session_mark(username: str) -> Dict[str, Any]:
    return dict(load_session_marks().get((username or "").strip()) or {})


def save_session_mark(
    username: str, tag: Optional[str] = None, advise: Optional[bool] = None
) -> Path:
    """写一个会话的标记。只更新传进来的字段，其余原样保留。"""
    name = (username or "").strip()
    if not name:
        raise ValueError("没有 username，不知道该给谁打标记")
    cfgmod.ensure_dirs()
    marks = load_session_marks()
    entry = dict(marks.get(name) or {})
    if tag is not None:
        entry["tag"] = (tag or "").strip()
    if advise is not None:
        entry["advise"] = bool(advise)
    entry["updated"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    marks[name] = entry

    path = cfgmod.session_marks_file()
    path.write_text(json.dumps(marks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


# ── 画像表单：界面上填的背景（0.4.0） ──────────────────────────────────
# 写进同一份 `画像\contacts\<名字>.md`，用一个带注释标记的区块包住：
# 上游技能写的内容一个字不动，而模型读整份文件时自然也看得到这些背景。
PROFILE_BEGIN = "<!-- goutou:profile-form begin -->"
PROFILE_END = "<!-- goutou:profile-form end -->"

# (界面上显示的名字, 输入框里的提示)。表格的第一列直接用中文名，md 给人看也顺眼。
PROFILE_FIELDS: List[Tuple[str, str]] = [
    ("关系", "恋爱 / 暧昧 / 朋友 / 家人 / 同事…"),
    ("我叫他", "你平时怎么称呼他/她"),
    ("他叫我", "他/她平时怎么称呼你"),
    ("认识多久", "比如：三年、同事两年"),
    ("雷区", "不能提的事、不能开的玩笑"),
    ("最近发生的事", "吵架、冷淡、约好要一起做什么"),
    ("其他备注", "任何你觉得军师该知道的"),
]


def _profile_path(name: str) -> Path:
    return cfgmod.contacts_dir() / ("%s.md" % name)


def read_profile_form(name: str) -> Dict[str, str]:
    """读回界面填过的那一份。没填过就返回空字典（不编造）。"""
    path = _profile_path(name)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}
    start = text.find(PROFILE_BEGIN)
    end = text.find(PROFILE_END)
    if start < 0 or end < 0 or end < start:
        return {}
    block = text[start + len(PROFILE_BEGIN):end]
    allowed = {label for label, _hint in PROFILE_FIELDS}
    fields: Dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 2:
            continue
        label = cells[0]
        if label in allowed:
            fields[label] = "|".join(cells[1:]).strip()
    return fields


def write_profile_form(name: str, fields: Dict[str, str]) -> Path:
    """把界面填的背景写进画像文件（替换旧区块，其余原文一字不动）。"""
    cfgmod.ensure_dirs()
    path = _profile_path(name)
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            text = ""
    else:
        text = (
            "# 联系人：%s\n\n"
            "> 这份文件的其余部分由军师技能维护；「我填的背景」一节由本程序写入，"
            "在界面的「资料」页里改。\n" % name
        )

    start = text.find(PROFILE_BEGIN)
    end = text.find(PROFILE_END)
    if start >= 0 and end > start:
        text = text[:start] + text[end + len(PROFILE_END):]
    text = text.rstrip() + "\n\n"

    lines = [PROFILE_BEGIN, "", "## 我填的背景（在界面上填的，可改）", "", "| 项 | 值 |", "|---|---|"]
    for label, _hint in PROFILE_FIELDS:
        value = (fields.get(label) or "").replace("\n", " ").strip()
        lines.append("| %s | %s |" % (label, value))
    lines += ["", PROFILE_END, ""]
    path.write_text(text + "\n".join(lines), encoding="utf-8")
    return path
