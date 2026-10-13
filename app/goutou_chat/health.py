"""开机体检：逐项打勾，绝不搞假绿。

为什么不能只报"检测到微信"：后端没起、45 天时效过期、解密库不在的时候，
微信明明开着，程序也读不到任何一条消息。所以这里每一项都真的去验。
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from . import config as cfgmod
from .pct import Pct
from .wda import WdaClient


@dataclass
class Check:
    key: str
    title: str
    ok: bool
    detail: str
    hint: str = ""
    level: str = "ok"  # ok / warn / bad

    def line(self) -> str:
        mark = {"ok": "[√]", "warn": "[!]", "bad": "[×]"}.get(self.level, "[?]")
        text = "%s %s：%s" % (mark, self.title, self.detail)
        if self.hint:
            text += "  → %s" % self.hint
        return text


def _running_processes(names: List[str]) -> List[str]:
    """用 tasklist 查进程（不引入 psutil 依赖）。"""
    wanted = {n.lower() for n in names}
    try:
        proc = subprocess.run(
            ["tasklist", "/FO", "CSV", "/NH"],
            capture_output=True,
            timeout=30,
        )
    except Exception:
        return []
    text = proc.stdout.decode("utf-8", "replace")
    found = []
    for line in text.splitlines():
        first = line.split(",")[0].strip().strip('"').lower()
        if first in wanted:
            found.append(first)
    return sorted(set(found))


def _db_files() -> List[Path]:
    root = cfgmod.WDA_DB_ROOT
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.db") if p.is_file())


def _expiry_check() -> Check:
    """45 天时效（ADR-0015）。以安装目录里最新文件的修改时间估算构建日。"""
    exe = cfgmod.WDA_EXE
    if not exe.exists():
        return Check("expiry", "工具时效", False, "读取后端未安装", level="bad")
    newest = exe.stat().st_mtime
    # 只看安装目录顶层（递归整棵 Electron 目录太慢，这里只要一个够用的估算）
    try:
        for path in exe.parent.iterdir():
            try:
                if path.is_file():
                    newest = max(newest, path.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    built = datetime.fromtimestamp(newest)
    deadline = built + timedelta(days=45)
    left = (deadline - datetime.now()).days
    detail = "约剩 %d 天（按安装文件时间 %s 估算，到 %s）" % (
        left,
        built.strftime("%Y-%m-%d"),
        deadline.strftime("%Y-%m-%d"),
    )
    if left <= 0:
        return Check("expiry", "工具时效", False, detail, "已过期，读取通道会失效", "bad")
    if left <= 7:
        return Check("expiry", "工具时效", True, detail, "快到 7 天红线，准备升级", "warn")
    return Check("expiry", "工具时效", True, detail, level="ok")


def run_health(cfg: Optional[cfgmod.Config] = None, wda: Optional[WdaClient] = None) -> List[Check]:
    cfg = cfg or cfgmod.Config.load()
    wda = wda or WdaClient(port=cfg.backend_port)
    checks: List[Check] = []

    # 1. 微信
    wechat = _running_processes(["Weixin.exe", "WeChat.exe", "WeChatAppEx.exe"])
    main = [p for p in wechat if p.lower() in ("weixin.exe", "wechat.exe")]
    if main:
        checks.append(Check("wechat", "微信", True, "在跑（%s）" % "、".join(wechat)))
    elif wechat:
        checks.append(
            Check("wechat", "微信", True, "只看到小程序宿主 %s" % "、".join(wechat),
                  "微信主窗口可能没开", "warn")
        )
    else:
        checks.append(Check("wechat", "微信", False, "没在跑", "打开微信后再体检", "bad"))

    # 2. QQ（可选通道）
    qq = _running_processes(["QQ.exe", "QQNT.exe", "QQScLauncher.exe"])
    if qq:
        checks.append(Check("qq", "QQ", True, "在跑（%s）" % "、".join(qq)))
    else:
        checks.append(Check("qq", "QQ", True, "没在跑", "需要处理 QQ 时再打开", "warn"))

    # 3. 读取后端
    health = wda.health()
    if health:
        checks.append(Check("backend", "读取后端", True, "端口 %d 有响应" % wda.port))
    else:
        checks.append(
            Check("backend", "读取后端", False, "端口 %d 没响应" % wda.port,
                  "可在界面点「启动读取后端」", "bad")
        )

    # 4. 解密库
    dbs = _db_files()
    if dbs:
        biggest = max(dbs, key=lambda p: p.stat().st_size)
        checks.append(
            Check("db", "本地解密库", True,
                  "%d 个库文件，最大 %s（%.1f MB）" % (
                      len(dbs), biggest.name, biggest.stat().st_size / 1024 / 1024))
        )
    else:
        checks.append(
            Check("db", "本地解密库", False, "没找到库文件", "需要在读取后端里先完成解密", "bad")
        )

    # 5. 时效
    checks.append(_expiry_check())

    # 6. 账号
    if health:
        try:
            accounts = wda.accounts()
            if accounts:
                names = [str(a.get("nickname") or a.get("name") or a.get("username") or "?") for a in accounts[:3]]
                checks.append(Check("account", "登录账号", True, "、".join(names)))
            else:
                checks.append(Check("account", "登录账号", True, "后端没返回账号", "可能还没解密", "warn"))
        except Exception as exc:
            checks.append(Check("account", "登录账号", False, str(exc), level="warn"))
    else:
        checks.append(Check("account", "登录账号", False, "后端没起，查不到", level="warn"))

    # 7. 冻结零件
    ok, detail = Pct().available()
    checks.append(Check("vendor", "上游零件副本", ok, detail,
                        "" if ok else "从技能包复制一份到 vendor-frozen", "ok" if ok else "bad"))

    # 8. 大脑通路
    if cfgmod.DSH_BIN.exists():
        checks.append(Check("brain", "大脑通路", True, "DSH CLI 就位（headless 实测约 3 秒）"))
    else:
        checks.append(Check("brain", "大脑通路", False, "找不到 DSH CLI",
                            "检查 %s" % cfgmod.DSH_BIN, "bad"))

    # 9. 军师知识
    if cfgmod.JUNSHI_SKILL.exists():
        refs = cfgmod.JUNSHI_SKILL / "references"
        count = len(list(refs.rglob("*.md"))) if refs.exists() else 0
        checks.append(Check("junshi", "军师知识库", True, "按需引用 %d 份参考" % count))
    else:
        checks.append(Check("junshi", "军师知识库", False, "找不到 %s" % cfgmod.JUNSHI_SKILL,
                            "本程序只引用不复制（ADR-0009）", "bad"))

    return checks


def format_health(checks: List[Check]) -> str:
    """给人看的体检报告。"""
    ok = sum(1 for c in checks if c.ok)
    lines = ["体检结果：%d/%d 项通过" % (ok, len(checks)), ""]
    lines += [c.line() for c in checks]
    bad = [c for c in checks if not c.ok and c.level == "bad"]
    if bad:
        lines += ["", "挡住读消息的是：%s" % "、".join(c.title for c in bad)]
    return "\n".join(lines)


def to_json(checks: List[Check]) -> str:
    import json

    return json.dumps([c.__dict__ for c in checks], ensure_ascii=False, indent=2)
