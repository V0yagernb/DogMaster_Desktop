# 包装脚本：把命令行入口包一层，方便技能直接调
# 注意：本文件必须保持纯 ASCII（Windows PowerShell 5.1 会把无 BOM 的 .ps1 按 ANSI 解码，
# 脚本里写中文会变乱码路径）。中文一律从命令行参数传进来。
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('health', 'sessions', 'read', 'advice', 'brain-test', 'ui')]
    [string]$Action,

    [string]$Name = '',
    [int]$Limit = 20,
    [string]$AppRoot = 'F:\goutoujunshi\app'
)

$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $AppRoot)) {
    throw "app root not found: $AppRoot"
}
Push-Location $AppRoot
try {
    switch ($Action) {
        'health' { $argsList = @('--health') }
        'sessions' { $argsList = @('--sessions') }
        'read' { $argsList = @('--read', $Name, '--limit', "$Limit") }
        'advice' { $argsList = @('--advice', $Name, '--limit', "$Limit") }
        'brain-test' { $argsList = @('--brain-test') }
        'ui' { $argsList = @() }
    }
    & python -m goutou_chat @argsList
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
