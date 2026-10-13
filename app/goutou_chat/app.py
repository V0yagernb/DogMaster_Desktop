"""命令行入口：体检、读消息、问军师、开界面。

用法：
    python -m goutou_chat --health            体检
    python -m goutou_chat --sessions          列会话
    python -m goutou_chat --read 联系人A         看最近记录
    python -m goutou_chat --advice 联系人A       让军师给一次建议（只出字，不发）
    python -m goutou_chat --brain-test        测一下大脑通路
    python -m goutou_chat                     打开界面
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from typing import List, Optional

from . import archive, advisor as advisor_mod, brain as brain_mod, config as cfgmod, health as health_mod, rules
from .wda import WdaClient


def _force_utf8() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except Exception:
            pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="goutou-chat",
        description="狗头军师桌面版：读消息、给建议、刹得住。第一版不自动发送。",
    )
    parser.add_argument("--health", action="store_true", help="跑一次体检")
    parser.add_argument("--json", action="store_true", help="体检结果用 JSON 输出")
    parser.add_argument("--sessions", action="store_true", help="列出会话")
    parser.add_argument("--read", metavar="对象", help="读某个人/某个群的最近记录")
    parser.add_argument("--advice", metavar="对象", help="让军师给一次建议（不会发送）")
    parser.add_argument("--limit", type=int, default=20, help="读多少条（默认 20）")
    parser.add_argument("--brain-test", action="store_true", help="测大脑通路")
    parser.add_argument("--ui", action="store_true", help="打开界面（默认行为）")
    parser.add_argument(
        "--demo",
        action="store_true",
        help="打开界面，但用一份假聊天做样子（界面上会标明「演示数据」，不碰真实记录）",
    )
    return parser


def _print(text: str) -> None:
    _force_utf8()
    print(text)


def _cmd_health(as_json: bool) -> int:
    cfg = cfgmod.Config.load()
    checks = health_mod.run_health(cfg)
    _print(health_mod.to_json(checks) if as_json else health_mod.format_health(checks))
    return 0 if all(c.ok or c.level == "warn" for c in checks) else 1


def _cmd_sessions() -> int:
    cfg = cfgmod.Config.load()
    client = WdaClient(port=cfg.backend_port)
    ready, note = client.ensure_backend()
    if not ready:
        _print("读不到会话：%s" % note)
        return 1
    rows: List[str] = []
    for item in client.sessions(limit=cfg.session_limit):
        tag = "［群］" if rules.is_group(item.get("username") or "") else ""
        unread = item.get("unreadCount") or 0
        rows.append(
            "%s%s%s  未读%s  %s"
            % (
                tag,
                item.get("name") or "?",
                (" ●%d" % unread) if unread else "",
                unread,
                (item.get("lastMessage") or "").replace("\n", " ")[:30],
            )
        )
    _print("共 %d 个会话\n%s" % (len(rows), "\n".join(rows)))
    return 0


def _load_messages(name: str, limit: int):
    cfg = cfgmod.Config.load()
    client = WdaClient(port=cfg.backend_port)
    ready, note = client.ensure_backend()
    if not ready:
        _print("读不到消息：%s" % note)
        return None, None
    session = client.find_session(name)
    if not session:
        _print("没有找到叫「%s」的会话。先跑 --sessions 看准确名字。" % name)
        return None, None
    messages = client.messages(session.get("username") or "", limit=limit, order="desc")
    return session, list(reversed(messages))


def _cmd_read(name: str, limit: int) -> int:
    session, messages = _load_messages(name, limit)
    if session is None:
        return 1
    tag = "［群］" if rules.is_group(session.get("username") or "") else ""
    _print("── %s%s 最近 %d 条 ──" % (tag, session.get("name"), len(messages)))
    _print(brain_mod.format_transcript(messages, limit=limit))
    return 0


def _cmd_advice(name: str, limit: int) -> int:
    session, messages = _load_messages(name, limit)
    if session is None:
        return 1
    contact = session.get("name") or name
    username = session.get("username") or ""
    allowed, why = rules.advice_allowed(username)
    if not allowed:
        _print(why)
        return 1

    cfg = cfgmod.Config.load()
    transcript = brain_mod.format_transcript(messages, limit=limit)
    red_hits = rules.needs_human_decision(transcript)
    advisor = advisor_mod.Advisor(cfg)

    def on_delta(piece: str) -> None:
        # 命令行也能看到"边想边出字"：直接往 stdout 吐，不攒到最后
        try:
            sys.stdout.write(piece)
            sys.stdout.flush()
        except Exception:
            pass

    def on_note(text: str) -> None:
        _print("\n[通道] %s" % text)

    started = datetime.now()
    result = advisor.advise(
        {
            "contact_name": contact,
            "transcript": transcript,
            "junshi_text": archive.junshi_digest(9000),
            "style_text": archive.user_style(3500),
            "contact_profile": archive.contact_profile(contact, 6000),
            "memory_text": archive.memory_context(subject_id=contact, max_chars=3000),
            "red_hits": red_hits,
        },
        on_delta=on_delta,
        on_note=on_note,
    )
    spent = (datetime.now() - started).total_seconds()

    if red_hits:
        _print("\n⚠ 对方话里出现了敏感词：%s（必须留余地，不许替本人承诺）" % "、".join(red_hits))
    _print("\n（%s；用时 %.1f 秒；复盘已写入 %s；**没有发送任何东西**）"
           % (result.describe(bool(getattr(cfg, "show_elapsed", True))), spent,
              archive.write_review(contact, brain_mod.parse_advice(result.text).as_transcript())))
    return 0


def _cmd_brain_test() -> int:
    cfg = cfgmod.Config.load()
    engine = brain_mod.DshBrain(model=cfg.dsh_model, timeout=cfg.dsh_timeout_seconds)
    ok, note = engine.available()
    if not ok:
        _print(note)
        return 1
    started = datetime.now()
    answer = engine.ask("只回复两个字：可以")
    spent = (datetime.now() - started).total_seconds()
    _print("大脑通路：%s\n返回：%s\n用时：%.1f 秒" % (note, answer, spent))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    _force_utf8()
    args = _build_parser().parse_args(argv)
    cfgmod.ensure_dirs()

    if args.health:
        return _cmd_health(args.json)
    if args.sessions:
        return _cmd_sessions()
    if args.read:
        return _cmd_read(args.read, args.limit)
    if args.advice:
        return _cmd_advice(args.advice, args.limit)
    if args.brain_test:
        return _cmd_brain_test()

    try:
        from .ui.main_window import run_ui
    except ImportError as exc:
        _print("打不开界面（缺 PyQt5？）：%s\n可以先跑 --health 用命令行版。" % exc)
        return 2
    return run_ui(demo=args.demo)
