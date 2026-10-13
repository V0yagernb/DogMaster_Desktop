"""演示数据：给界面做样子、做截图验收用的假聊天，**不是真实记录**。

为什么要它：本机的微信和读取后端经常没起（体检就报这个），界面就一直是空的，
既没法看效果也没法做视觉回归。这里放一份固定的假数据，只在 `--demo` 时启用。

两条硬规矩：
1. 启用时界面必须**明确标出**"演示数据"，不许让人误以为是真的（不做假绿）。
2. 演示模式下不许走任何真实发送链路——它连 wda、pct 都不碰。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List

DEMO_BANNER = "演示数据（不是真实聊天记录）"


def _ts(minutes_ago: int) -> int:
    return int((datetime.now() - timedelta(minutes=minutes_ago)).timestamp())


DEMO_SESSIONS: List[Dict[str, Any]] = [
    {
        "name": "联系人A",
        "username": "wxid_demo_a",
        "unreadCount": 2,
        "lastMessage": "那你说呀",
        "lastTime": _ts(3),
    },
    {
        "name": "群聊A",
        "username": "1234567890@chatroom",
        "unreadCount": 7,
        "lastMessage": "周六中午老地方，都来",
        "lastTime": _ts(18),
    },
    {
        "name": "联系人B",
        "username": "wxid_demo_b",
        "unreadCount": 0,
        "lastMessage": "行，明天我看下时间",
        "lastTime": _ts(90),
    },
    {
        "name": "联系人C",
        "username": "wxid_demo_c",
        "unreadCount": 0,
        "lastMessage": "周末回来吃饭吗",
        "lastTime": _ts(60 * 26),
    },
    {
        "name": "群聊B",
        "username": "9876543210@chatroom",
        "unreadCount": 0,
        "lastMessage": "三缺一",
        "lastTime": _ts(60 * 50),
    },
]

DEMO_MESSAGES: Dict[str, List[Dict[str, Any]]] = {
    "wxid_demo_a": [
        {"createTime": _ts(64), "isSent": False, "senderDisplayName": "联系人A", "content": "在干嘛"},
        {"createTime": _ts(62), "isSent": True, "content": "刚下班，在路上"},
        {"createTime": _ts(61), "isSent": False, "senderDisplayName": "联系人A", "content": "哦"},
        {"createTime": _ts(20), "isSent": False, "senderDisplayName": "联系人A", "content": "今天好累啊"},
        {"createTime": _ts(12), "isSent": True, "content": "怎么了，工作上的事？"},
        {
            "createTime": _ts(10),
            "isSent": False,
            "senderDisplayName": "联系人A",
            "content": "",
            "renderType": "图片",
        },
        {"createTime": _ts(9), "isSent": False, "senderDisplayName": "联系人A", "content": "你看这个"},
        {"createTime": _ts(3), "isSent": False, "senderDisplayName": "联系人A", "content": "那你说呀"},
    ],
    "1234567890@chatroom": [
        {"createTime": _ts(40), "isSent": False, "senderDisplayName": "联系人C", "content": "周六中午老地方，都来"},
        {"createTime": _ts(35), "isSent": False, "senderDisplayName": "联系人F", "content": "收到"},
        {"createTime": _ts(18), "isSent": False, "senderDisplayName": "联系人C", "content": "你回来不"},
    ],
    "wxid_demo_b": [
        {"createTime": _ts(95), "isSent": True, "content": "那个方案你看了吗"},
        {"createTime": _ts(90), "isSent": False, "senderDisplayName": "联系人B", "content": "行，明天我看下时间"},
    ],
    "wxid_demo_c": [
        {"createTime": _ts(60 * 27), "isSent": False, "senderDisplayName": "联系人C", "content": "周末回来吃饭吗"},
        {"createTime": _ts(60 * 26), "isSent": True, "content": "我看下安排，回头跟你说"},
    ],
    "9876543210@chatroom": [
        {"createTime": _ts(60 * 51), "isSent": False, "senderDisplayName": "联系人E", "content": "三缺一"},
        {"createTime": _ts(60 * 50), "isSent": False, "senderDisplayName": "联系人D", "content": "晚上九点"},
    ],
}

DEMO_ADVICE = """【判断】她今天连着发了两条"好累"，中间还发了一张图，说明她想找人说话，不是真的在问你问题。
你上一条问的是"工作上的事"，方向对，但问得太像查户口。现在这句"那你说呀"是在等你接住情绪，
不是在等方案。最要紧的一件事：先接情绪，别急着分析原因。

【首选】那别硬撑了，跟我说说

【备选】1. 看着就累，过来我这儿坐会儿——适合你们离得近、她愿意出门的情况
2. 先不说事，先吃点东西——适合她这会儿连话都不想讲的情况

【风险】不要追问"到底谁惹你了"，那会让她觉得在交代问题。她要是只回一个"嗯"，
就别硬聊了，隔天再找她。涉及见面、接送这类承诺，你自己拍板，程序不许替你答应。"""
