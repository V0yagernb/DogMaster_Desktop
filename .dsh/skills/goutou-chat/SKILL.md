---
name: goutou-chat
description: 狗头军师桌面版。在 Windows 上把「关系判断与话术」和「读微信消息、发微信消息」合成一条链路：先做开机体检（微信/QQ/读取后端/解密库/工具时效），再读某个人的聊天记录，让军师给出判断与可直接发送的草稿，最后由本人决定发不发。含紧急制动与发送前检查点。用于用户说「帮我看看XX的聊天记录」「这句怎么回」「替我跟XX聊」「读一下微信消息」时。不用于导出或解密微信数据（那是 WeChatDataAnalysis 的活）、不用于非聊天的窗口自动化（那用 process-chat-tool）、不在未经点名授权时自动发送。
whenToUse: 用户要求读微信聊天记录、让军师分析一段对话并拟回复、替他与某人聊天、盯某个会话的未读、或排查「读不到微信消息」时。也用于查询本机读取后端（本地解密库）的状态。
---

# 狗头军师桌面版（goutou-chat）

一句话：**读消息、给判断、刹得住。** 第一版只做建议档，**绝不自动发送**。

## 目录与位置（不要凭记忆，按这里的绝对路径走）

| 东西 | 位置 |
| --- | --- |
| 程序（命令行 + 界面） | `F:\goutoujunshi\app`（`python -m goutou_chat`） |
| 数据根（画像/语料/记忆库/复盘/日志/配置） | `F:\goutoujunshi\关系档案\` |
| 冻结的上游零件（pct.ps1 + targets） | `F:\goutoujunshi\vendor-frozen\` |
| 军师技能（只引用、不复制） | `<你的用户目录>\.agents\skills\goutoujunshi` |
| 读取后端（本地解密库 HTTP，端口 10392） | `%LOCALAPPDATA%\Programs\WeChatDataAnalysis` |
| 决策记录（每条规矩的来龙去脉） | `F:\goutoujunshi\docs\决策记录.md` |

## 怎么用（命令行，最适合本技能）

```powershell
$app = "F:\goutoujunshi\app"
Push-Location $app

python -m goutou_chat --health                  # 体检：先看通不通，别猜
python -m goutou_chat --sessions                # 列会话（谁有未读、最后一句）
python -m goutou_chat --read 联系人A --limit 20    # 读某个人的最近记录
python -m goutou_chat --advice 联系人A             # 让军师给一次建议（只出字，不发）
python -m goutou_chat --brain-test              # 测大脑通路（DSH headless，约 3 秒）
python -m goutou_chat                           # 打开界面（给人点着用）
python -m goutou_chat --demo                    # 打开界面但用假数据（界面会标明「演示数据」）

Pop-Location
```

也可以直接调包装脚本：`& <技能目录>\scripts\goutou.ps1 -Action health|sessions|read|advice -Name 联系人A -Limit 20`
（脚本源码是纯 ASCII，中文一律从命令行参数传进去。）

## 界面与打包（0.2.0 起）

- **界面**：三栏（会话列表 / 气泡聊天记录 / 军师建议四块卡片）+ 复盘浏览 + 资料面板 + 系统托盘；
  点关闭是收进托盘，不是退出。
- **演示模式**：`--demo` 用一份固定假数据把界面填满（**不碰** wda 与 pct，不写复盘），
  标题栏与顶栏都会标明「演示数据」。微信与后端没起时看效果就靠它。
- **出截图**：`python tools\ui_preview.py --out <目录>`。注意它用的是 `windows` 平台 +
  `WA_DontShowOnScreen`——本机 Qt 的 `offscreen` 插件字体库是空的（实测 0 个字体），
  用它截出来的图一个字都没有。
- **打包**：`pwsh -File .\build.ps1`，产物 `app\dist\goutou-chat.exe`（单文件、无控制台），
  约 36 MB。加 `-Shortcut` 才会写桌面快捷方式（桌面在工作区之外，所以默认不做）。
  exe 靠 `VERSION` 与 `关系档案` 定位数据根；把 exe 单独拷走要设 `GOUTOU_HOME`。

## 读消息是怎么读到的

微信消息**不截图**，走本地解密库的结构化接口：

- `GET http://127.0.0.1:10392/api/chat/sessions?limit=N`
- `GET http://127.0.0.1:10392/api/chat/messages?username=<wxid>&limit=N&order=desc`

端口发现顺序：环境变量 `WECHAT_TOOL_PORT` → `%APPDATA%\wechat-data-analysis-desktop\desktop-settings.json` 的 `backendPort` → 默认 10392。

**两个接口在代码里被封死**：`/api/get_image_key`、`/api/cdn/*` —— 它们会把账号信息 POST 到第三方，`wda.py` 见到就抛错，不要绕过它。

## 大脑通道（0.3.0 起）

程序有两条通道，`advisor.py` 负责选：

1. **直连**（默认首选）：走各家 OpenAI 兼容接口，支持流式，界面边收边填四块卡片。
   key 在设置窗口里填，存在 `关系档案\配置\secrets.json`（**不进版本库**）。
2. **兜底**：DSH 子进程（`node <bin.js> headless -`）。只在直连不可用时自动启用——
   注意它每次要多花约 **1.87 秒**（起进程 + 装配配置），且不支持流式。

**回退时必须说出来**：状态栏会写"这次走的是兜底通道"。看到这句话就知道这次慢是为什么。

命令行版的 `--advice` 也一样走 `advisor.py`：配了 key 就直连，没配就走兜底。

**群聊不给建议**：`rules.advice_allowed()` 对 `@chatroom` 一律返回不允许，界面上军师按钮是灰的。
这是有意的（ADR-0020）：群聊记录不送去第三方，发错一句也是公开事故。

## 红线（写在代码里，不是建议）

1. **第一版绝不自动发送**。全自动档在界面上是灰的。任何「替用户发出去」的动作都必须先有本人的点名授权。
2. **群聊永不进全自动**（`@chatroom` 一律拒绝），身份映射有疑问时以群防线兜底。
3. **只发文本**：不发图片、不发任何表情包。本人立过规矩——会发表情包的一律是他本人。
4. **不替本人承诺**钱、时间、出行、见面。话里出现 `rules.HARD_STOP_TOPICS` 里的词，就必须留余地并提示本人。
5. **发错人比晚回消息严重得多**：发送必须带上下文校验（上游 `-ExpectHash`，过期返回 exit 4 拒发）。
6. **紧急制动**（默认 `Ctrl+Alt+Esc`，仅全自动态生效）：按下后停止一切自动动作，并丢弃已拟好未发的内容；**已经进键盘的那一条不会撤回**，要如实告诉用户。

`rules.may_auto_send()` 是所有自动发送的唯一入口，任何绕过它的发送都算 bug。

## 排障顺序（照这个顺序查，别乱试）

1. `--health` 看哪一项是 `[×]`。
2. **读取后端没起**：界面点「启动读取后端」，或 `wc.ps1 -Action status`。后端是 Electron 程序，起来要几十秒。
3. **工具时效**：原生组件自构建起 45 天失效（体检会报还剩几天）。到期症状是后端起不来、日志里 `Broker code signature validation failed`。升级由本人决定，程序不擅自下载安装。
4. **军事知识找不到**：检查 `<你的用户目录>\.agents\skills\goutoujunshi` 是否还在（另一处 `.claude\skills\goutoujunshi` 是指向它的目录联接）。
5. **要读图/非微信应用**：那不属于本技能，用 `process-chat-tool`（截图通道）。
6. **exe 双击没反应、或旁边多出一份空的 `关系档案`**：确认 exe 在 `app\dist\` 下
   （往上两级是项目根），或者设环境变量 `GOUTOU_HOME` 指向数据根。

## 与军师、与上游的分工

- **判断与话术**：来自狗头军师技能包，按需读它的 references（本技能只引用，不复制那 43 份文件）。
- **读写通道**：读走本地解密库 HTTP；发走上游 `pct.ps1` 的键盘注入（冻结副本在 `vendor-frozen\`，与上游会分叉，同步要人工做）。
- **本人语料与画像**：在 `关系档案\语料\` 与 `关系档案\画像\`。原 `process-chat-tool` 的 `style\` 是指向画像目录的目录联接，两边看到的是同一份数据。

## 用完要回写

一轮对话结束后：

1. 复盘写进 `关系档案\复盘\日期-对象.md`（`--advice` 会自动写）；
2. 只把**稳定事实**（约好的事、冲突、修复）通过军师的 `memory_store.py` 写进记忆库（配额：总 200 条、单条 200 字）；
3. 新口头禅与说话习惯补进 `关系档案\画像\`。
