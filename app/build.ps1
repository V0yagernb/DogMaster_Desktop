# 打包（PyInstaller）：单文件版 + 文件夹版
#
# 用法（在 app\ 目录下）：
#   pwsh -File .\build.ps1                    # 两个版本都打（默认）
#   pwsh -File .\build.ps1 -Mode onefile      # 只打单文件
#   pwsh -File .\build.ps1 -Mode onedir       # 只打文件夹版（启动快）
#   pwsh -File .\build.ps1 -Shortcut          # 顺手在桌面建快捷方式（指向文件夹版）
#   pwsh -File .\build.ps1 -SkipInstall       # 不检查/安装 PyInstaller
#
# 为什么有两个版本：
# - 单文件：只有一个 exe，方便拷走。但每次启动都要先把 30+ MB 解压到临时目录，
#   双击之后有几秒完全没反应——注意，解压阶段 Python 还没起来，任何"正在启动"的
#   提示都显示不出来，这是单文件打包的先天限制，不是没做。
# - 文件夹：免解压，启动快得多，日常用这个；代价是要整个文件夹一起带着。
# 桌面快捷方式指向文件夹版。
#
# 数据根怎么找：exe 靠 VERSION 与 关系档案 两个目录定位项目根。放在 dist\ 下时
# 往上找就能找到项目根。若把 exe 单独拷到别的机器，请设环境变量 GOUTOU_HOME。
#
# 本脚本是 UTF-8 with BOM：Windows PowerShell 5.1 会把不带 BOM 的 .ps1 按 ANSI 解码，
# 中文就成乱码了。改完这个文件记得保留 BOM（pwsh 7 无所谓，5.1 很在意）。

param(
    [switch]$Shortcut,
    [switch]$SkipInstall,
    [string]$Name = "goutou-chat",
    [string]$FolderName = "goutou-chat-app",
    [ValidateSet("onefile", "onedir", "both")]
    [string]$Mode = "both"
)

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot

Write-Host "==> [1/5] 生成图标（现画，不用外部图片）"
python "$PSScriptRoot\tools\make_icon.py" --out "$PSScriptRoot\assets"
if ($LASTEXITCODE -ne 0) { throw "生成图标失败" }

Write-Host "==> [2/5] 检查 PyInstaller"
if (-not $SkipInstall) {
    python -m pip show pyinstaller > $null 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Host "    没装，正在安装（需要联网）"
        python -m pip install pyinstaller
        if ($LASTEXITCODE -ne 0) { throw "装 PyInstaller 失败" }
    }
}

$common = @(
    '--noconfirm', '--clean', '--windowed',
    '--icon', "$PSScriptRoot\assets\icon.ico",
    '--paths', $PSScriptRoot,
    '--exclude-module', 'tkinter',
    '--exclude-module', 'matplotlib',
    '--exclude-module', 'numpy',
    '--exclude-module', 'PIL',
    '--exclude-module', 'PyQt5.QtWebEngineWidgets',
    '--exclude-module', 'PyQt5.QtMultimedia'
)

$onefileExe = Join-Path $PSScriptRoot "dist\$Name.exe"
$onedirExe = Join-Path $PSScriptRoot "dist\$FolderName\$FolderName.exe"

if ($Mode -eq 'onefile' -or $Mode -eq 'both') {
    Write-Host "==> [3/5] 打包单文件版（双击可开，但每次启动要先解压）"
    python -m PyInstaller @common --onefile --name $Name "$PSScriptRoot\run.py"
    if ($LASTEXITCODE -ne 0) { throw "单文件版打包失败" }
}

if ($Mode -eq 'onedir' -or $Mode -eq 'both') {
    Write-Host "==> [4/5] 打包文件夹版（启动快，日常用这个）"
    python -m PyInstaller @common --onedir --name $FolderName "$PSScriptRoot\run.py"
    if ($LASTEXITCODE -ne 0) { throw "文件夹版打包失败" }
}

Write-Host "==> [5/5] 产物"
if (Test-Path -LiteralPath $onefileExe) {
    $size = [math]::Round((Get-Item -LiteralPath $onefileExe).Length / 1MB, 1)
    Write-Host ("    单文件：{0}（{1} MB）" -f $onefileExe, $size)
}
if (Test-Path -LiteralPath $onedirExe) {
    $folderBytes = (Get-ChildItem -LiteralPath (Split-Path -Parent $onedirExe) -Recurse -File |
        Measure-Object -Property Length -Sum).Sum
    $size = [math]::Round($folderBytes / 1MB, 1)
    Write-Host ("    文件夹：{0}（合计 {1} MB）" -f $onedirExe, $size)
}

if ($Shortcut) {
    # 桌面是工作区之外的路径，所以这一步必须显式加 -Shortcut 才做
    $target = if (Test-Path -LiteralPath $onedirExe) { $onedirExe } else { $onefileExe }
    if (-not (Test-Path -LiteralPath $target)) { throw "没有可指向的产物" }
    $desktop = [Environment]::GetFolderPath('Desktop')
    $linkPath = Join-Path $desktop "狗头军师.lnk"
    $shell = New-Object -ComObject WScript.Shell
    # 注意：变量不能叫 $shortcut —— PowerShell 的变量名不区分大小写，它会和上面的
    # switch 参数 $Shortcut 撞成同一个变量，赋值时直接报类型错（这次踩过）。
    $link = $shell.CreateShortcut($linkPath)
    $link.TargetPath = $target
    $link.WorkingDirectory = (Split-Path -Parent $PSScriptRoot)
    $link.IconLocation = "$PSScriptRoot\assets\icon.ico"
    $link.Description = "狗头军师：读消息、给建议、刹得住（不自动发送）"
    $link.Save()
    Write-Host "    桌面快捷方式：$linkPath -> $target"
}

Write-Host ""
Write-Host "数据仍在项目根的 关系档案\ 下；exe 本身可以单独复制走（要带 GOUTOU_HOME）。"
