<#
  process-chat-tool :: scripts/pct.ps1

  Drive ANY desktop chat window from an agent: find a target process/window,
  capture regions with PrintWindow (correct even while occluded), wait for
  changes cheaply by hashing, switch conversations, and send messages.

  Targets are described by JSON profiles in ../targets/*.json. WeChat ships as a
  built-in profile; an unknown app still works through -Hwnd/-Target plus a raw
  -Region "x,y,w,h", so nothing here is WeChat-specific.

  ASCII-only on purpose: Windows PowerShell 5.1 decodes a BOM-less .ps1 as ANSI,
  so any non-ASCII literal in this file would corrupt. Chinese lives in the
  profiles' "notes" and in the markdown docs.

  Examples:
    pct.ps1 -Action list
    pct.ps1 -Action targets
    pct.ps1 -Action status  -Target wechat
    pct.ps1 -Action shot    -Target wechat -Region rail
    pct.ps1 -Action shot    -Target wechat -Region "314,40,565,378"
    pct.ps1 -Action wait    -Target wechat -Since <hash> -TimeoutSec 150
    pct.ps1 -Action switch  -Target wechat -To "Alice"
    pct.ps1 -Action send    -Target wechat -Text "hello"
    pct.ps1 -Action watch   -Targets wechat,qq -TimeoutSec 120
#>
[CmdletBinding()]
param(
  [Parameter(Mandatory = $true)]
  [ValidateSet('list', 'targets', 'status', 'regions', 'shot', 'wait', 'send',
               'sendwait', 'switch', 'click', 'click-row', 'focus', 'restore', 'probe', 'scroll', 'watch')]
  [string]$Action,

  # profile name ("wechat"), a window title substring, or a numeric hwnd
  [string]$Target = '',
  # comma separated targets for -Action watch
  [string]$Targets = '',

  # named region from the profile, or raw "x,y,w,h" in client coordinates
  # (negative w/h mean "counted from the right/bottom edge")
  [string]$Region = '',

  # print | screen  (print == PrintWindow, works while occluded)
  [ValidateSet('print', 'screen')]
  [string]$Method = 'print',

  [string]$Out = '',
  [int]$TimeoutSec = 150,
  [int]$IntervalSec = 8,
  [string]$Since = '',
  # Optimistic-concurrency guard for send/sendwait: the pane hash your draft was
  # based on. If the conversation moved on since, the send is REFUSED (exit 4)
  # and a fresh capture is returned - so a stale draft never goes out.
  [string]$ExpectHash = '',
  [string]$Text = '',
  [string]$To = '',
  # "x,y" click point in client coordinates; combined with -Region it becomes
  # region-relative, which is how you click a conversation row in the rail
  [string]$At = '',
  # 1-based conversation row in the target's rail, resolved through the
  # profile's layout.rowGeometry. Read the rail image to learn the index.
  [int]$RowIndex = 0,
  [ValidateSet('up', 'down')]
  [string]$Direction = 'up',
  [int]$Times = 3,

  # explicit window, bypassing profile lookup
  [long]$Hwnd = 0,
  # per-call override when the app's left rail is wider/narrower than the profile
  [int]$RailWidth = 0,
  [int]$HashLen = 16,
  [string]$ProfileDir = '',
  # also consider windows the app has hidden (tray). Needed to find a chat app
  # the user "closed" and to bring it back with -Action restore.
  [switch]$IncludeHidden
)

$ErrorActionPreference = 'Stop'

if (-not ('PctNative' -as [type])) {
  Add-Type -Namespace '' -Name PctNative -MemberDefinition @'
[DllImport("user32.dll")] public static extern bool GetClientRect(IntPtr h, out RECT r);
[DllImport("user32.dll")] public static extern bool ClientToScreen(IntPtr h, ref POINT p);
[DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
[DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
[DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
[DllImport("user32.dll")] public static extern IntPtr SetFocus(IntPtr h);
[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, IntPtr pid);
[DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool attach);
[DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
[DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
[DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT p);
[DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
[DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint flags);
[DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
[DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
[DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
[DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetClassName(IntPtr h, System.Text.StringBuilder s, int max);
[DllImport("user32.dll", CharSet = CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int max);
public struct RECT { public int Left, Top, Right, Bottom; }
public struct POINT { public int X, Y; }
'@
}

if (-not ('PctWin' -as [type])) {
  Add-Type -Namespace '' -Name PctWin -MemberDefinition @'
public delegate bool EnumProc(IntPtr h, IntPtr l);
[DllImport("user32.dll")] static extern bool EnumWindows(EnumProc cb, IntPtr l);
[DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
[DllImport("user32.dll")] static extern bool IsIconic(IntPtr h);
[DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int max);
[DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassName(IntPtr h, System.Text.StringBuilder s, int max);
[DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
[DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h, out RECT r);
public struct RECT { public int Left, Top, Right, Bottom; }
// hwnd|pid|class|minimized|WxH|title   (one line per titled top-level window)
// includeHidden=false lists only visible windows; true also lists ones the app
// has hidden (tray), which is how a "closed" chat app is usually found.
public static string[] List(bool includeHidden) {
  var acc = new System.Collections.Generic.List<string>();
  EnumWindows(delegate(IntPtr h, IntPtr l) {
    bool vis = IsWindowVisible(h);
    if (!vis && !includeHidden) return true;
    var tb = new System.Text.StringBuilder(512);
    GetWindowText(h, tb, 512);
    string title = tb.ToString();
    if (title.Length == 0) return true;
    var cb = new System.Text.StringBuilder(256);
    GetClassName(h, cb, 256);
    uint pid; GetWindowThreadProcessId(h, out pid);
    RECT r; GetWindowRect(h, out r);
    acc.Add(h.ToInt64().ToString() + "|" + pid.ToString() + "|" + cb.ToString() + "|" +
            (IsIconic(h) ? "1" : "0") + "|" + (r.Right - r.Left).ToString() + "x" + (r.Bottom - r.Top).ToString() + "|" +
            (vis ? "ok" : "hid") + "|" + title);
    return true;
  }, IntPtr.Zero);
  return acc.ToArray();
}
'@
}

Add-Type -AssemblyName System.Drawing

# ------------------------------------------------------------------- profiles

if (-not $ProfileDir) { $ProfileDir = Join-Path (Split-Path -Parent $PSScriptRoot) 'targets' }

function Get-Profiles {
  $map = @{}
  if (-not (Test-Path $ProfileDir)) { return $map }
  foreach ($f in (Get-ChildItem -Path $ProfileDir -Filter '*.json' -File)) {
    if ($f.Name.StartsWith('_')) { continue }
    try {
      $j = Get-Content $f.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
      if ($j.name) { $map[[string]$j.name] = $j }
    } catch {
      Write-Warning ('bad profile ' + $f.Name + ': ' + $_.Exception.Message)
    }
  }
  return $map
}

function Get-WindowRows {
  $rows = @()
  foreach ($line in [PctWin]::List([bool]$IncludeHidden)) {
    $parts = $line -split '\|', 7
    $rows += [pscustomobject]@{
      Hwnd      = [long]$parts[0]
      Pid       = [int]$parts[1]
      Class     = $parts[2]
      Minimized = ($parts[3] -eq '1')
      Size      = $parts[4]
      Visible   = ($parts[5] -eq 'ok')
      Title     = $parts[6]
    }
  }
  return $rows
}

# Does a profile's `match` block accept this window row? Single source of truth,
# used both when scanning candidates for a profile-name target and when a raw
# hwnd / title target has already been resolved and we want to know which app
# profile describes it (see Find-ProfileForRow).
function Test-ProfileMatch($p, $r) {
  if ($p.match.processNames -and ($p.match.processNames -notcontains (Get-ProcName $r.Pid))) { return $false }
  if ($p.match.windowClassPattern -and -not ($r.Class -like $p.match.windowClassPattern)) { return $false }
  if ($p.match.titleContains -and -not ($r.Title -like ('*' + $p.match.titleContains + '*'))) { return $false }
  if ($p.match.titleRegex -and -not ($r.Title -match $p.match.titleRegex)) { return $false }
  return $true
}

# `-Target <hwnd>` and `-Target <title substring>` used to fall back to "generic"
# mode, which silently dropped the profile's named regions AND its
# send.clickComposer. On QQ that meant `send` never clicked the input box, so the
# message went nowhere. Match the RESOLVED WINDOW instead of the target string.
function Find-ProfileForRow($r, [hashtable]$profiles) {
  foreach ($k in ($profiles.Keys | Sort-Object)) {
    if (Test-ProfileMatch $profiles[$k] $r) { return $profiles[$k] }
  }
  return $null
}

function Resolve-TargetWindow([string]$t, [hashtable]$profiles) {
  # 1) numeric hwnd
  $asLong = 0
  if ([long]::TryParse($t, [ref]$asLong) -and $asLong -gt 0) {
    foreach ($r in (Get-WindowRows)) { if ($r.Hwnd -eq $asLong) { return $r } }
    return $null
  }
  $rows = Get-WindowRows
  # 2) profile by name
  if ($profiles.ContainsKey($t)) {
    $p = $profiles[$t]
    $cands = @()
    foreach ($r in $rows) {
      if (Test-ProfileMatch $p $r) { $cands += $r }
    }
    if ($cands.Count -eq 0) { return $null }
    # prefer a live, visible window; fall back to minimized, then to hidden
    $live = @($cands | Where-Object { (-not $_.Minimized) -and $_.Visible })
    # More than one live match = AMBIGUOUS. Reading the wrong window is harmless,
    # but SENDING to a guessed one messages the wrong person, so `send` refuses.
    $script:AmbiguousCandidates = @()
    if ($live.Count -gt 1) { $script:AmbiguousCandidates = $live }
    if ($live.Count -gt 0) { return $live[0] }
    $vis = $cands | Where-Object { $_.Visible }
    if ($vis) { return $vis[0] }
    return $cands[0]
  }
  # 3) title substring
  $hit = $rows | Where-Object { $_.Title -like ('*' + $t + '*') }
  if ($hit) { return $hit[0] }
  return $null
}

$script:ProcNameCache = @{}
# NOTE: never name this parameter $pid - $PID is a read-only automatic variable.
function Get-ProcName([int]$procId) {
  if ($script:ProcNameCache.ContainsKey($procId)) { return $script:ProcNameCache[$procId] }
  $n = ''
  try { $n = (Get-Process -Id $procId -ErrorAction Stop).ProcessName } catch { $n = '' }
  $script:ProcNameCache[$procId] = $n
  return $n
}

function Get-TargetProfile([string]$t, [hashtable]$profiles) {
  if ($profiles.ContainsKey($t)) { return $profiles[$t] }
  foreach ($k in $profiles.Keys) {
    $p = $profiles[$k]
    if ($p.match.windowTitleAlias -and ($p.match.windowTitleAlias -contains $t)) { return $p }
  }
  return $null
}

# --------------------------------------------------------------- geometry

function Get-ClientInfo([IntPtr]$hw) {
  $cr = New-Object PctNative+RECT
  [void][PctNative]::GetClientRect($hw, [ref]$cr)
  $pt = New-Object PctNative+POINT
  [void][PctNative]::ClientToScreen($hw, [ref]$pt)
  [pscustomobject]@{
    X = $pt.X; Y = $pt.Y
    W = $cr.Right - $cr.Left
    H = $cr.Bottom - $cr.Top
  }
}

# region spec -> pixel rect inside the client area.
# named regions come from the profile; "x,y,w,h" is accepted raw. Negative x/y
# count from the right/bottom edge, and negative w/h mean "the right/bottom edge
# itself", so a profile survives window resizing.
#
# WARNING: PowerShell variable names are case-insensitive. Never introduce a
# local $w/$h/$x/$y here and never name a parameter $W/$H/$Hw: $w would clobber
# the client width, $h would clobber the window handle. Everything is prefixed.
function Resolve-RegionRect([string]$spec, $profile, [int]$areaW, [int]$areaH) {
  if (-not $spec) { $spec = 'pane' }
  if ($spec -match '^-?\d+\s*,\s*-?\d+\s*,\s*-?\d+\s*,\s*-?\d+$') {
    $v = $spec -split '\s*,\s*'
    $rx = [int]$v[0]; $ry = [int]$v[1]; $rw = [int]$v[2]; $rh = [int]$v[3]
    if ($rx -lt 0) { $rx = $areaW + $rx }
    if ($ry -lt 0) { $ry = $areaH + $ry }
    if ($rw -le 0) { $rw = ($areaW + $rw) - $rx }
    if ($rh -le 0) { $rh = ($areaH + $rh) - $ry }
    return [pscustomobject]@{ X = $rx; Y = $ry; W = $rw; H = $rh; Name = ('raw ' + $spec) }
  }
  $rr = $null
  if ($profile -and $profile.regions -and ($profile.regions.PSObject.Properties.Name -contains $spec)) {
    $rr = $profile.regions.$spec
  }
  if (-not $rr) {
    if ($spec -eq 'full') { return [pscustomobject]@{ X = 0; Y = 0; W = $areaW; H = $areaH; Name = 'full' } }
    return $null
  }
  $rx = 0; $ry = 0; $rw = 0; $rh = 0
  if ($null -ne $rr.x) { $rx = [int]$rr.x }
  if ($null -ne $rr.y) { $ry = [int]$rr.y }
  if ($null -ne $rr.w) { $rw = [int]$rr.w }
  if ($null -ne $rr.h) { $rh = [int]$rr.h }
  # "the pane starts after the left rail" keeps one profile valid across resizes
  if ($rr.xRail -eq $true) { $rx = $RailWidth }
  if ($rx -lt 0) { $rx = $areaW + $rx }
  if ($ry -lt 0) { $ry = $areaH + $ry }
  if ($rr.wRest -eq $true) { $rw = $areaW - $rx }
  if ($rr.hRest -eq $true) { $rh = $areaH - $ry }
  if ($rw -le 0) { $rw = ($areaW + $rw) - $rx }
  if ($rh -le 0) { $rh = ($areaH + $rh) - $ry }
  return [pscustomobject]@{ X = $rx; Y = $ry; W = $rw; H = $rh; Name = $spec }
}

function Restore-Window([IntPtr]$hw) {
  # A minimized window has no rendered client area, so capturing or typing needs
  # it restored first. But un-minimizing MOVES SOMETHING ON THE USER'S SCREEN, and
  # the user may have minimized it on purpose - so never do it silently.
  if ([PctNative]::IsIconic($hw)) {
    [void][PctNative]::ShowWindow($hw, 9)
    Start-Sleep -Milliseconds 600
    Write-Output 'NOTE the target was minimized - restored it with SW_RESTORE (this changed what is on the user''s screen; it was NOT closed and was NOT restarted)'
  }
}

function Set-Foreground([IntPtr]$hw) {
  Restore-Window $hw
  if ([PctNative]::GetForegroundWindow() -eq $hw) { return $true }
  $fg = [PctNative]::GetForegroundWindow()
  $t1 = [PctNative]::GetWindowThreadProcessId($fg, [IntPtr]::Zero)
  $t2 = [PctNative]::GetWindowThreadProcessId($hw, [IntPtr]::Zero)
  $attached = $false
  if ($t1 -ne $t2) { $attached = [PctNative]::AttachThreadInput($t1, $t2, $true) }
  # Windows refuses SetForegroundWindow unless the caller owns the last input
  # event; a synthetic ALT tap satisfies that rule when running from a sandbox.
  [PctNative]::keybd_event(0x12, 0, 0, [UIntPtr]::Zero)
  [PctNative]::keybd_event(0x12, 0, 2, [UIntPtr]::Zero)
  [void][PctNative]::BringWindowToTop($hw)
  [void][PctNative]::ShowWindow($hw, 5)
  [void][PctNative]::SetForegroundWindow($hw)
  [void][PctNative]::SetFocus($hw)
  if ($attached) { [void][PctNative]::AttachThreadInput($t1, $t2, $false) }
  Start-Sleep -Milliseconds 150
  if ([PctNative]::GetForegroundWindow() -eq $hw) { return $true }

  # Second attempt raises the window inside the normal z-order band: pin it
  # topmost, then drop the pin. It ends up above the windows that covered it.
  #
  # DO NOT use the classic "minimize + restore" trick here. Measured on WeChat:
  # SW_MINIMIZE makes it HIDE TO THE SYSTEM TRAY, so that fallback silently
  # pushed the target out of every subsequent window lookup.
  $SWP_NOSIZE = 0x1; $SWP_NOMOVE = 0x2; $SWP_NOACTIVATE = 0x10
  $flags = $SWP_NOSIZE -bor $SWP_NOMOVE -bor $SWP_NOACTIVATE
  [void][PctNative]::SetWindowPos($hw, [IntPtr](-1), 0, 0, 0, 0, $flags)   # HWND_TOPMOST
  Start-Sleep -Milliseconds 120
  [void][PctNative]::SetWindowPos($hw, [IntPtr](-2), 0, 0, 0, 0, $flags)   # HWND_NOTOPMOST
  Start-Sleep -Milliseconds 120
  [void][PctNative]::SetForegroundWindow($hw)
  Start-Sleep -Milliseconds 150
  return ([PctNative]::GetForegroundWindow() -eq $hw)
}

# --------------------------------------------------------------- capture

# A stale hwnd is dangerous: Windows recycles handle values, so capturing a
# dead target can silently return a DIFFERENT window's pixels. Always confirm
# identity (alive + visible + same window class) before trusting a capture.
function Assert-TargetWindow([IntPtr]$hw) {
  if (-not [PctNative]::IsWindow($hw)) {
    throw ('target window is gone (hwnd ' + $hw + ' no longer exists) - it was closed or hidden to the tray; run -Action list and ask the user to reopen it')
  }
  if (-not [PctNative]::IsWindowVisible($hw)) {
    throw ('target window exists but is hidden (hwnd ' + $hw + ') - the app is probably sitting in the system tray; ask the user to reopen it')
  }
  $cls = New-Object System.Text.StringBuilder 256
  [void][PctNative]::GetClassName($hw, $cls, 256)
  $now = $cls.ToString()
  if ($script:TargetRow -and $now -ne $script:TargetRow.Class) {
    throw ('hwnd ' + $hw + ' now belongs to a different window (class "' + $now + '", expected "' +
           $script:TargetRow.Class + '") - the original target closed and its handle was recycled; run -Action list')
  }
}

function Capture-Region([IntPtr]$hw, [string]$spec, [string]$method, [string]$path, $profile) {
  Assert-TargetWindow $hw
  $ci = Get-ClientInfo $hw
  if ($ci.W -le 50 -or $ci.H -le 50) { throw ('client rect too small (' + $ci.W + 'x' + $ci.H + ') - the window is MINIMIZED (or hidden), so it cannot be captured. Fix: -Action list to see min/hid, then -Action restore -Target <hwnd|title> (SW_RESTORE). Never restart the exe.') }
  $rr = Resolve-RegionRect $spec $profile $ci.W $ci.H
  if (-not $rr) { throw ('unknown region "' + $spec + '" for this target - use -Action regions, or pass raw "x,y,w,h"') }
  $x = [Math]::Max(0, $rr.X); $y = [Math]::Max(0, $rr.Y)
  $w = [Math]::Min($rr.W, $ci.W - $x); $h = [Math]::Min($rr.H, $ci.H - $y)
  if ($w -lt 8 -or $h -lt 8) { throw ('crop too small for region ' + $rr.Name + ' (' + $w + 'x' + $h + ')') }

  # PW_CLIENTONLY(1) | PW_RENDERFULLCONTENT(2) == 3 renders the client area at
  # (0,0): byte-identical to CopyFromScreen on Qt/Chromium apps, and it keeps
  # working while the window is occluded, so the user's screen is never hijacked.
  $full = New-Object System.Drawing.Bitmap([int]$ci.W, [int]$ci.H)
  $g = [System.Drawing.Graphics]::FromImage($full)
  if ($method -eq 'print') {
    $hdc = $g.GetHdc()
    $ok = [PctNative]::PrintWindow($hw, $hdc, 3)
    $g.ReleaseHdc($hdc)
    if (-not $ok) { $g.Dispose(); $full.Dispose(); throw 'PrintWindow failed' }
  } else {
    $g.CopyFromScreen($ci.X, $ci.Y, 0, 0, (New-Object System.Drawing.Size([int]$ci.W, [int]$ci.H)))
  }
  $g.Dispose()

  $crop = $full.Clone((New-Object System.Drawing.Rectangle($x, $y, $w, $h)), $full.PixelFormat)
  $crop.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)
  $crop.Dispose(); $full.Dispose()

  $h256 = (Get-FileHash $path -Algorithm SHA256).Hash
  return [pscustomobject]@{ Hash = $h256.Substring(0, $HashLen); Path = $path; W = $w; H = $h; Region = $rr.Name }
}

function New-ShotPath([string]$tag) {  if ($Out) { return $Out }
  $dir = Join-Path $env:TEMP 'process-chat-tool'
  if (-not (Test-Path $dir)) { [void](New-Item -ItemType Directory -Path $dir -Force) }
  $safe = ($tag -replace '[^A-Za-z0-9_.-]', '_')
  return (Join-Path $dir ('pct_' + $safe + '.png'))
}

function Get-SendVk([string]$spec) {
  switch ($spec) {
    'enter'      { return @(0x0D, $false) }
    'ctrl+enter' { return @(0x0D, $true) }
    'alt+s'      { return @(0x53, $true) }
    default      { return @(0x0D, $false) }
  }
}

function Send-Keys([string]$spec) {
  $vk = Get-SendVk $spec
  if ($vk[1]) { [PctNative]::keybd_event(0x11, 0, 0, [UIntPtr]::Zero) }
  [PctNative]::keybd_event([byte]$vk[0], 0, 0, [UIntPtr]::Zero)
  [PctNative]::keybd_event([byte]$vk[0], 0, 2, [UIntPtr]::Zero)
  if ($vk[1]) { [PctNative]::keybd_event(0x11, 0, 2, [UIntPtr]::Zero) }
}

function Paste-Text([string]$text) {
  Set-Clipboard -Value $text
  Start-Sleep -Milliseconds 90
  [PctNative]::keybd_event(0x11, 0, 0, [UIntPtr]::Zero)
  [PctNative]::keybd_event(0x56, 0, 0, [UIntPtr]::Zero)
  [PctNative]::keybd_event(0x56, 0, 2, [UIntPtr]::Zero)
  [PctNative]::keybd_event(0x11, 0, 2, [UIntPtr]::Zero)
}

# Reading a window works while it is occluded (PrintWindow), but CLICKING does
# not: a synthetic click lands on whatever window is TOPMOST at that pixel, and
# being the foreground window does NOT mean being on top (measured: WeChat was
# foreground yet still below two other windows). So pin the target topmost for
# the click, verify the landing point really belongs to it, then unpin and put
# the user's cursor back.
function Click-Client([IntPtr]$hw, [int]$cx, [int]$cy, [switch]$Force) {
  $ci = Get-ClientInfo $hw
  # Negative cx/cy are measured from the right/bottom edge of the client area -
  # the same convention Resolve-RegionRect uses. Without this, a profile's
  # clickComposer is a hard-coded absolute point: it silently lands on the
  # bottom toolbar instead of the input box as soon as the window is shorter
  # than the size it was calibrated at (WeChat 4.1 opens ~68px shorter than the
  # 613px this profile was measured at, so [597,490] hit the emoji button).
  if ($cx -lt 0) { $cx = $ci.W + $cx }
  if ($cy -lt 0) { $cy = $ci.H + $cy }
  $sx = $ci.X + $cx; $sy = $ci.Y + $cy
  $prev = New-Object PctNative+POINT
  [void][PctNative]::GetCursorPos([ref]$prev)

  $SWP_NOSIZE = 0x1; $SWP_NOMOVE = 0x2; $SWP_NOACTIVATE = 0x10
  $flags = $SWP_NOSIZE -bor $SWP_NOMOVE -bor $SWP_NOACTIVATE

  [void][PctNative]::SetCursorPos($sx, $sy)
  Start-Sleep -Milliseconds 60
  $pt = New-Object PctNative+POINT
  $pt.X = $sx; $pt.Y = $sy
  $pinned = $false
  $under = [PctNative]::WindowFromPoint($pt)
  $root = [PctNative]::GetAncestor($under, 2)   # GA_ROOT
  if (($root -ne $hw) -and ($under -ne $hw)) {
    # Pin ONLY when something actually covers the point. Pinning costs ~400ms and
    # the common case (target already on top) does not need it at all.
    [void][PctNative]::SetWindowPos($hw, [IntPtr](-1), 0, 0, 0, 0, $flags)   # HWND_TOPMOST
    Start-Sleep -Milliseconds 180
    $pinned = $true
    $under = [PctNative]::WindowFromPoint($pt)
    $root = [PctNative]::GetAncestor($under, 2)
    if (($root -ne $hw) -and ($under -ne $hw) -and (-not $Force)) {
      [void][PctNative]::SetWindowPos($hw, [IntPtr](-2), 0, 0, 0, 0, $flags)  # NOTOPMOST
      [void][PctNative]::SetCursorPos($prev.X, $prev.Y)
      throw ('refusing to click: screen point ' + $sx + ',' + $sy + ' is still owned by hwnd ' + $root +
             ' even after pinning the target hwnd ' + $hw + ' topmost. Nothing was clicked.')
    }
  }
  [PctNative]::mouse_event(0x0002, 0, 0, 0, [UIntPtr]::Zero)  # LEFTDOWN
  [PctNative]::mouse_event(0x0004, 0, 0, 0, [UIntPtr]::Zero)  # LEFTUP
  Start-Sleep -Milliseconds 110
  if ($pinned) { [void][PctNative]::SetWindowPos($hw, [IntPtr](-2), 0, 0, 0, 0, $flags) }
  [void][PctNative]::SetCursorPos($prev.X, $prev.Y)
}

# ------------------------------------------------------------------- main

$profiles = Get-Profiles

if ($Action -eq 'list') {
  Write-Output 'hwnd|pid|proc|class|min|size|title'
  foreach ($r in (Get-WindowRows | Sort-Object -Property @{ Expression = { $_.Minimized } }, Title)) {
    $state = 'ok'
    if ($r.Minimized) { $state = 'min' } elseif (-not $r.Visible) { $state = 'hid' }
    Write-Output ($r.Hwnd.ToString() + '|' + $r.Pid + '|' + (Get-ProcName $r.Pid) + '|' + $r.Class + '|' +
                  $state + '|' + $r.Size + '|' + $r.Title)
  }
  Write-Output '(hid = the window exists but the app has hidden it, typically to the system tray; add -IncludeHidden to act on it, then -Action restore to show it)'
  exit 0
}

if ($Action -eq 'targets') {
  Write-Output ('profileDir=' + $ProfileDir)
  if ($profiles.Count -eq 0) { Write-Output '(no profiles - raw -Hwnd / -Target <title> still works)' }
  foreach ($k in ($profiles.Keys | Sort-Object)) {
    $p = $profiles[$k]
    $regs = ''
    if ($p.regions) { $regs = ($p.regions.PSObject.Properties.Name -join ',') }
    Write-Output ($k + ' | title=' + $p.match.titleContains + ' | proc=' + ($p.match.processNames -join '/') +
                  ' | class=' + $p.match.windowClassPattern + ' | regions=' + $regs + ' | send=' + $p.send.keys)
  }
  exit 0
}

if ($Action -eq 'watch') {
  # poll several targets at once: this is the "not a single chat bubble" mode
  $list = @()
  if ($Targets) { $list = $Targets -split '\s*,\s*' } elseif ($Target) { $list = @($Target) }
  if ($list.Count -eq 0) { Write-Output 'ERROR -Targets or -Target required'; exit 2 }
  $state = @{}
  foreach ($t in $list) {
    $r = Resolve-TargetWindow $t $profiles
    if (-not $r) { Write-Output ('target ' + $t + ': NOT FOUND'); continue }
    $p = Get-TargetProfile $t $profiles
    $reg = if ($Region) { $Region } elseif ($p -and $p.regions -and $p.regions.pane) { 'pane' } else { 'full' }
    $script:TargetRow = $r
    $c = Capture-Region ([IntPtr]$r.Hwnd) $reg $Method (New-ShotPath ($t + '_' + $reg)) $p
    $state[$t] = [pscustomobject]@{ Row = $r; Hash = $c.Hash; Profile = $p; Region = $reg }
    Write-Output ('baseline ' + $t + ' hwnd=' + $r.Hwnd + ' hash=' + $c.Hash)
  }
  $sw = [Diagnostics.Stopwatch]::StartNew()
  while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
    Start-Sleep -Seconds $IntervalSec
    foreach ($t in $state.Keys) {
      $s = $state[$t]
      if (-not $s) { continue }
      $script:TargetRow = $s.Row
      $c = Capture-Region ([IntPtr]$s.Row.Hwnd) $s.Region $Method (New-ShotPath ($t + '_' + $s.Region)) $s.Profile
      if ($c.Hash -ne $s.Hash) {
        Write-Output ('CHANGED target=' + $t + ' hash=' + $c.Hash + ' img=' + $c.Path + ' elapsed=' + [int]$sw.Elapsed.TotalSeconds + 's')
        $s.Hash = $c.Hash
      }
    }
  }
  Write-Output ('watch ended after ' + $TimeoutSec + 's')
  exit 0
}

if (-not $Target -and $Hwnd -ne 0) { $Target = $Hwnd.ToString() }
if (-not $Target) { Write-Output 'ERROR -Target (profile name / window title / hwnd) is required'; exit 2 }

$row = Resolve-TargetWindow $Target $profiles
if (-not $row) {
  Write-Output ('ERROR no visible window matched target "' + $Target + '"')
  Write-Output 'hint: run -Action list. A chat app whose window is missing is usually minimized to the system tray, not closed - ask the user to reopen it instead of forcing it.'
  exit 2
}
$profile = Get-TargetProfile $Target $profiles
# A target that is NOT a profile name (raw hwnd, or a title substring like
# "a contact name") still resolves to a window - so work out which profile describes that
# window. Without this the caller silently lost the named regions and, worse,
# send.clickComposer (on QQ that means the text never reaches the input box).
if (-not $profile) { $profile = Find-ProfileForRow $row $profiles }
$hwnd = [IntPtr]$row.Hwnd
$script:TargetRow = $row
if (-not $script:AmbiguousCandidates) { $script:AmbiguousCandidates = @() }
if ($RailWidth -eq 0 -and $profile -and $profile.layout -and $profile.layout.railWidth) { $RailWidth = [int]$profile.layout.railWidth }

switch ($Action) {

  'status' {
    $ci = Get-ClientInfo $hwnd
    Write-Output ('target=' + $Target + ' profile=' + $(if ($profile) { $profile.name } else { '(none, generic)' }))
    Write-Output ('hwnd=' + $row.Hwnd + ' pid=' + $row.Pid + ' proc=' + (Get-ProcName $row.Pid) + ' class=' + $row.Class)
    Write-Output ('title=' + $row.Title)
    Write-Output ('minimized=' + [PctNative]::IsIconic($hwnd) + ' foreground=' + ([PctNative]::GetForegroundWindow() -eq $hwnd))
    Write-Output ('client=' + $ci.X + ',' + $ci.Y + ' ' + $ci.W + 'x' + $ci.H + ' railWidth=' + $RailWidth)
  }

  'regions' {
    $ci = Get-ClientInfo $hwnd
    Write-Output ('client=' + $ci.W + 'x' + $ci.H + ' railWidth=' + $RailWidth)
    if (-not $profile) { Write-Output 'no profile: use raw -Region "x,y,w,h"'; exit 0 }
    foreach ($nm in $profile.regions.PSObject.Properties.Name) {
      $rr = Resolve-RegionRect $nm $profile $ci.W $ci.H
      Write-Output ($nm.PadRight(8) + ' = x' + $rr.X + ' y' + $rr.Y + ' ' + $rr.W + 'x' + $rr.H + '   ' + $profile.regions.$nm.note)
    }
    Write-Output 'raw      = pass -Region "x,y,w,h" (negative w/h count from the right/bottom edge)'
  }

  'restore' {
    # Handles BOTH "minimized" and "hidden to the tray". A chat app that looks
    # closed usually still has its window; re-launching the exe is the WRONG fix
    # (WeChat starts a second instance showing a login window instead).
    # Only call this with the user's consent: hiding to the tray is often deliberate.
    $wasHidden = -not [PctNative]::IsWindowVisible($hwnd)
    if ($wasHidden) {
      # Measured on WeChat: SW_SHOW alone leaves it hidden, SW_RESTORE revives it.
      [void][PctNative]::ShowWindow($hwnd, 9)
      Start-Sleep -Milliseconds 600
      if (-not [PctNative]::IsWindowVisible($hwnd)) {
        [void][PctNative]::ShowWindow($hwnd, 5)
        Start-Sleep -Milliseconds 600
      }
    }
    if ([PctNative]::IsIconic($hwnd)) { [void][PctNative]::ShowWindow($hwnd, 9); Start-Sleep -Milliseconds 600 }
    Write-Output ('wasHidden=' + $wasHidden + ' visible=' + [PctNative]::IsWindowVisible($hwnd) + ' minimized=' + [PctNative]::IsIconic($hwnd))
  }

  'focus' { Write-Output ('foreground=' + (Set-Foreground $hwnd)) }

  'shot' {
    Restore-Window $hwnd
    $reg = $Region
    if (-not $reg) { $reg = if ($profile -and $profile.regions.watch) { 'watch' } else { 'pane' } }
    $r = Capture-Region $hwnd $reg $Method (New-ShotPath ($Target + '_' + $reg)) $profile
    Write-Output ('hash=' + $r.Hash)
    Write-Output ('img=' + $r.Path)
    Write-Output ('size=' + $r.W + 'x' + $r.H + ' region=' + $r.Region + ' method=' + $Method)
  }

  'wait' {
    Restore-Window $hwnd
    $reg = $Region
    if (-not $reg) { $reg = if ($profile -and $profile.regions.watch) { 'watch' } else { 'pane' } }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $polls = 0; $cur = ''
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
      Start-Sleep -Seconds $IntervalSec
      $polls++
      $r = Capture-Region $hwnd $reg $Method (New-ShotPath ($Target + '_' + $reg)) $profile
      $cur = $r.Hash
      if ($Since -and $cur -ne $Since) {
        # DEBOUNCE: a frame can differ merely because a bubble, scrollbar or
        # hover effect is still animating (that is exactly how `send`'s old
        # baseline produced phantom "new message" hits). Only report the change
        # once the new frame has held still across a second capture.
        Start-Sleep -Milliseconds 1100
        $polls++
        $r = Capture-Region $hwnd $reg $Method (New-ShotPath ($Target + '_' + $reg)) $profile
        if ($r.Hash -eq $cur) {
          Write-Output ('CHANGED hash=' + $cur + ' elapsed=' + [int]$sw.Elapsed.TotalSeconds + 's polls=' + $polls + ' img=' + $r.Path)
          exit 0
        }
        $cur = $r.Hash
      }
    }
    Write-Output ('UNCHANGED hash=' + $cur + ' elapsed=' + [int]$sw.Elapsed.TotalSeconds + 's polls=' + $polls)
  }

  'probe' {
    Restore-Window $hwnd
    $reg = if ($Region) { $Region } else { 'pane' }
    $a = Capture-Region $hwnd $reg 'print' (New-ShotPath 'probe_print') $profile
    $b = Capture-Region $hwnd $reg 'screen' (New-ShotPath 'probe_screen') $profile
    $ia = [System.Drawing.Bitmap]::FromFile($a.Path)
    $ib = [System.Drawing.Bitmap]::FromFile($b.Path)
    $sum = 0; $n = 0
    for ($y = 0; $y -lt $ia.Height; $y += 6) {
      for ($x = 0; $x -lt $ia.Width; $x += 6) {
        $pa = $ia.GetPixel($x, $y); $pb = $ib.GetPixel($x, $y)
        $sum += [math]::Abs($pa.R - $pb.R) + [math]::Abs($pa.G - $pb.G) + [math]::Abs($pa.B - $pb.B)
        $n += 3
      }
    }
    $ia.Dispose(); $ib.Dispose()
    Write-Output ('meanAbsDiff=' + [math]::Round($sum / $n, 3) + ' (0 = PrintWindow matches the real screen; >5 = the window is occluded, that is expected unless you asked for screen)')
  }

  'click' {
    # Generic "click somewhere" primitive. Combined with a rail screenshot this
    # is the RELIABLE way to change conversation: read rail -> find the row's y
    # -> click. Search-based switching (see 'switch') has side effects.
    if (-not $At) { Write-Output 'ERROR -At "x,y" is required for action click'; exit 2 }
    $parts = $At -split '\s*,\s*'
    $ax = [int]$parts[0]; $ay = [int]$parts[1]
    if ($Region) {
      $ci = Get-ClientInfo $hwnd
      $rr = Resolve-RegionRect $Region $profile $ci.W $ci.H
      if (-not $rr) { Write-Output ('ERROR unknown region ' + $Region); exit 2 }
      $ax = $rr.X + $ax; $ay = $rr.Y + $ay
    }
    $raised = Set-Foreground $hwnd
    if (-not $raised) {
      Write-Output 'WARN could not make the target the foreground window; relying on the topmost pin for the click to land'
    }
    Click-Client $hwnd $ax $ay
    Start-Sleep -Milliseconds 500
    $hdrReg = 'full'
    if ($profile -and ($profile.regions.PSObject.Properties.Name -contains 'header')) { $hdrReg = 'header' }
    $cap = Capture-Region $hwnd $hdrReg $Method (New-ShotPath 'click_header') $profile
    Write-Output ('clicked=' + $ax + ',' + $ay + ' (client coords, foreground=' + ([PctNative]::GetForegroundWindow() -eq $hwnd) + ')')
    Write-Output ('headerImg=' + $cap.Path)
    Write-Output 'VERIFY the header image shows the intended conversation BEFORE sending'
  }

  'click-row' {
    # The VERIFIED way to change conversation on WeChat: read the rail image,
    # count the row, click it by index. Row geometry lives in the profile.
    # Always re-read the rail first: WeChat reorders the list whenever a new
    # message arrives, so any row index older than the last screenshot is stale.
    if ($RowIndex -lt 1) { Write-Output 'ERROR -RowIndex <n> (1-based) is required for action click-row'; exit 2 }
    if (-not $profile -or -not $profile.layout -or -not $profile.layout.rowGeometry) {
      Write-Output 'ERROR this target has no layout.rowGeometry in its profile'; exit 2
    }
    $g = $profile.layout.rowGeometry
    $ay = [int]$g.firstRowTop + ([int]$g.rowHeight * ($RowIndex - 1)) + [int]([int]$g.rowHeight / 2)
    $ax = [int]$g.clickX
    $raised = Set-Foreground $hwnd
    if (-not $raised) { Write-Output 'WARN could not make the target foreground; relying on the topmost pin' }
    Click-Client $hwnd $ax $ay
    Start-Sleep -Milliseconds 600
    $hdrReg = 'full'
    if ($profile.regions.PSObject.Properties.Name -contains 'header') { $hdrReg = 'header' }
    $cap = Capture-Region $hwnd $hdrReg $Method (New-ShotPath 'clickrow_header') $profile
    Write-Output ('clickedRow=' + $RowIndex + ' atClient=' + $ax + ',' + $ay)
    Write-Output ('headerImg=' + $cap.Path)
    Write-Output 'VERIFY the header image shows the intended conversation BEFORE sending'
  }

  'switch' {
    # open the app's conversation search, type the name, open the first hit,
    # then hand back a picture of the header so the agent can VERIFY who it is
    # talking to before sending anything.
    #
    # WeChat caveats measured 2026-09-20:
    #   * Ctrl+F OPENS A SIDE SEARCH PANEL THAT WIDENS THE WINDOW (879 -> 1323),
    #     so every region computed from the old width is wrong until it closes.
    #   * 700ms was not enough for the result list; Enter fired too early and the
    #     conversation did not change. 1200ms works.
    #   * NEVER press Escape to close the panel: in WeChat Esc HIDES THE WHOLE
    #     MAIN WINDOW TO THE TRAY. Click the panel's X, or just complete a switch.
    # Prefer -Action click on the rail when you can read the conversation row.
    if (-not $To) { Write-Output 'ERROR -To <conversation name> is required'; exit 2 }
    if (-not $profile -or -not $profile.switch) { Write-Output 'ERROR this target has no "switch" recipe in its profile'; exit 2 }
    $ciBefore = Get-ClientInfo $hwnd
    $settle = 500
    if ($profile.switch.settleMs) { $settle = [int]$profile.switch.settleMs }
    $null = Set-Foreground $hwnd
    if ($profile.switch.openSearchClick) {
      # Some apps bind the shortcut to a WEB search panel: WeChat 4.x Ctrl+F opens
      # "搜一搜" (AI搜索/视频/文章 tabs), where Enter never opens a chat. Clicking
      # the real conversation-search box at the top of the rail is what works.
      Click-Client $hwnd ([int]$profile.switch.openSearchClick[0]) ([int]$profile.switch.openSearchClick[1])
    } else {
      [PctNative]::keybd_event(0x11, 0, 0, [UIntPtr]::Zero)
      [PctNative]::keybd_event(0x46, 0, 0, [UIntPtr]::Zero)   # F
      [PctNative]::keybd_event(0x46, 0, 2, [UIntPtr]::Zero)
      [PctNative]::keybd_event(0x11, 0, 2, [UIntPtr]::Zero)
    }
    Start-Sleep -Milliseconds $settle
    [void][PctNative]::SetForegroundWindow($hwnd)
    Paste-Text $To
    if ($profile.switch.resultSettleMs) { Start-Sleep -Milliseconds ([int]$profile.switch.resultSettleMs) }
    else { Start-Sleep -Milliseconds 1200 }
    Send-Keys 'enter'
    Start-Sleep -Milliseconds 1200
    $ciAfter = Get-ClientInfo $hwnd
    $hdrReg = 'full'
    if ($profile.regions.PSObject.Properties.Name -contains 'header') { $hdrReg = 'header' }
    $paneReg = 'full'
    if ($profile.regions.PSObject.Properties.Name -contains 'pane') { $paneReg = 'pane' }
    # timestamped so two switches in a row do not overwrite each other's proof
    $stamp = Get-Date -Format 'HHmmss'
    $r = Capture-Region $hwnd $hdrReg $Method (New-ShotPath ('switch_header_' + $stamp)) $profile
    $p = Capture-Region $hwnd $paneReg $Method (New-ShotPath ('switch_pane_' + $stamp)) $profile
    Write-Output ('switched-to=' + $To)
    Write-Output ('clientBefore=' + $ciBefore.W + 'x' + $ciBefore.H + ' clientAfter=' + $ciAfter.W + 'x' + $ciAfter.H)
    Write-Output ('headerImg=' + $r.Path)
    Write-Output ('paneImg=' + $p.Path)
    Write-Output ('hash=' + $p.Hash)
    if ($ciAfter.W -ne $ciBefore.W) {
      Write-Output 'WARN client width changed - a search/panel is probably still open, so region coordinates are off. Do NOT press Escape in WeChat (it hides the window to the tray); click the panel X instead.'
    }
    Write-Output 'VERIFY the header image matches the intended conversation BEFORE calling send'
  }

  'scroll' {
    # load older history, or reach the newest line. Wheel events go to the
    # topmost window under the cursor, exactly like a click, so the target must
    # actually be in front.
    $raised = Set-Foreground $hwnd
    if (-not $raised) {
      Write-Output 'ERROR could not raise the target to the foreground - wheel events would scroll another window. Nothing was scrolled.'
      exit 3
    }
    $ci = Get-ClientInfo $hwnd
    $regName = if ($Region) { $Region } else { 'pane' }
    $rr = Resolve-RegionRect $regName $profile $ci.W $ci.H
    if (-not $rr) { Write-Output ('ERROR unknown region ' + $regName); exit 2 }
    $prev = New-Object PctNative+POINT
    [void][PctNative]::GetCursorPos([ref]$prev)
    [void][PctNative]::SetCursorPos($ci.X + $rr.X + [int]($rr.W / 2), $ci.Y + $rr.Y + [int]($rr.H / 2))
    Start-Sleep -Milliseconds 150
    $delta = if ($Direction -eq 'up') { 120 } else { -120 }   # wheel up = toward the top
    # mouse_event takes the wheel delta as a DWORD, so a negative delta has to be
    # reinterpreted as unsigned: [uint32]-120 throws in PowerShell.
    $wheel = [uint32]($delta -band 0xFFFFFFFFL)
    for ($i = 0; $i -lt $Times; $i++) {
      [PctNative]::mouse_event(0x0800, 0, 0, $wheel, [UIntPtr]::Zero)
      Start-Sleep -Milliseconds 250
    }
    [void][PctNative]::SetCursorPos($prev.X, $prev.Y)
    Write-Output ('scrolled ' + $Direction + ' x' + $Times + ' over region ' + $rr.Name)
  }

  'send' {
    if (-not $Text) { Write-Output 'ERROR -Text is required for action send'; exit 2 }

    # A profile-name target can match several live windows (QQ opens one window
    # per chat). Guessing one risks messaging the WRONG person, so refuse here
    # instead - the caller re-runs with -Target <title> or -Target <hwnd>.
    if ($script:AmbiguousCandidates -and $script:AmbiguousCandidates.Count -gt 1) {
      $names = (@($script:AmbiguousCandidates) | ForEach-Object { $_.Title + '(hwnd ' + $_.Hwnd + ')' }) -join ', '
      Write-Output ('ERROR refusing to send: target "' + $Target + '" matches ' + $script:AmbiguousCandidates.Count + ' live windows: ' + $names)
      Write-Output 'hint: sending to a guessed window messages the wrong person. Re-run with -Target <title substring> or -Target <hwnd>.'
      exit 2
    }

    $raised = Set-Foreground $hwnd
    if (-not $raised) { Write-Output 'WARN could not raise the window; if sent=false use the MCP click/type_text fallback' }

    $regName = if ($Region) { $Region } else { 'pane' }
    $panePath = New-ShotPath ($Target + '_' + $regName)
    $before = Capture-Region $hwnd $regName $Method $panePath $profile

    # ---- optimistic-concurrency guard -------------------------------------
    # The caller read an image at hash H and drafted a reply from it. If the
    # conversation moved on since (they sent more while the agent was reading or
    # thinking), that draft answers a stale picture. Refuse and hand back a fresh
    # capture instead of silently replying to something that is no longer last.
    if ($ExpectHash -and ($before.Hash -ne $ExpectHash)) {
      Write-Output ('STALE expected=' + $ExpectHash + ' actual=' + $before.Hash)
      Write-Output ('img=' + $before.Path)
      Write-Output ('size=' + $before.W + 'x' + $before.H + ' region=' + $before.Region)
      Write-Output 'NOT SENT - the conversation changed after you read it. Read the image above, redraft, then send with the new hash.'
      exit 4
    }

    if ($profile -and $profile.send.clickComposer) {
      Click-Client $hwnd ([int]$profile.send.clickComposer[0]) ([int]$profile.send.clickComposer[1])
    }
    Paste-Text $Text
    Start-Sleep -Milliseconds 220
    Send-Keys $(if ($profile -and $profile.send.keys) { $profile.send.keys } else { 'enter' })

    # The pane keeps repainting while the new bubble animates in. Capturing once
    # right after Enter returns a hash that changes again by itself - and then
    # `wait -Since <that hash>` immediately reports a PHANTOM new message, which
    # makes an agent both waste an image read and reply to nothing. So capture
    # until two consecutive frames agree and report THAT hash as the baseline.
    $after = $null; $prevHash = ''
    for ($i = 0; $i -lt 10; $i++) {
      Start-Sleep -Milliseconds 220
      $after = Capture-Region $hwnd $regName $Method $panePath $profile
      if ($after.Hash -eq $prevHash) { break }
      $prevHash = $after.Hash
    }
    $sent = ($after.Hash -ne $before.Hash)
    Write-Output ('sent=' + $sent)
    Write-Output ('hash=' + $after.Hash)
    Write-Output ('img=' + $after.Path)
    Write-Output ('size=' + $after.W + 'x' + $after.H + ' region=' + $after.Region + ' method=' + $Method)
    if (-not $sent) {
      $dbg = Capture-Region $hwnd $(if ($profile -and ($profile.regions.PSObject.Properties.Name -contains 'composer')) { 'composer' } else { 'full' }) $Method (New-ShotPath 'composer') $profile
      Write-Output ('WARN region unchanged - the message probably did NOT go out. composer=' + $dbg.Path)
    }
  }

  'sendwait' {
    # ONE round trip per conversational turn instead of three: staleness-guarded
    # send, then block until the other side actually says something, then hand
    # back an image path that is READY TO READ. That kills two agent turns
    # (one for the baseline shot, one for the wait) and one extra image read.
    if (-not $Text) { Write-Output 'ERROR -Text is required for action sendwait'; exit 2 }

    if ($script:AmbiguousCandidates -and $script:AmbiguousCandidates.Count -gt 1) {
      $names = (@($script:AmbiguousCandidates) | ForEach-Object { $_.Title + '(hwnd ' + $_.Hwnd + ')' }) -join ', '
      Write-Output ('ERROR refusing to send: target "' + $Target + '" matches ' + $script:AmbiguousCandidates.Count + ' live windows: ' + $names)
      exit 2
    }

    $regName = if ($Region) { $Region } else { 'pane' }
    $panePath = New-ShotPath ($Target + '_' + $regName)
    $null = Set-Foreground $hwnd
    $before = Capture-Region $hwnd $regName $Method $panePath $profile

    if ($ExpectHash -and ($before.Hash -ne $ExpectHash)) {
      Write-Output ('STALE expected=' + $ExpectHash + ' actual=' + $before.Hash)
      Write-Output ('img=' + $before.Path)
      Write-Output ('size=' + $before.W + 'x' + $before.H + ' region=' + $before.Region)
      Write-Output 'NOT SENT - the conversation changed after you read it. Read the image above and redraft.'
      exit 4
    }

    if ($profile -and $profile.send.clickComposer) {
      Click-Client $hwnd ([int]$profile.send.clickComposer[0]) ([int]$profile.send.clickComposer[1])
    }
    Paste-Text $Text
    Start-Sleep -Milliseconds 220
    Send-Keys $(if ($profile -and $profile.send.keys) { $profile.send.keys } else { 'enter' })

    $after = $null; $prevHash = ''
    for ($i = 0; $i -lt 10; $i++) {
      Start-Sleep -Milliseconds 220
      $after = Capture-Region $hwnd $regName $Method $panePath $profile
      if ($after.Hash -eq $prevHash) { break }
      $prevHash = $after.Hash
    }
    Write-Output ('sent=' + ($after.Hash -ne $before.Hash))

    $readReg = 'io'
    if ($profile -and -not ($profile.regions.PSObject.Properties.Name -contains 'io')) { $readReg = $regName }
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $polls = 0
    while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
      Start-Sleep -Seconds $IntervalSec
      $polls++
      $now = Capture-Region $hwnd $regName $Method $panePath $profile
      if ($now.Hash -ne $after.Hash) {
        Start-Sleep -Milliseconds 320          # let the incoming bubble finish animating
        $rd = Capture-Region $hwnd $readReg $Method (New-ShotPath ($Target + '_' + $readReg)) $profile
        Write-Output ('REPLY hash=' + $now.Hash + ' elapsed=' + [int]$sw.Elapsed.TotalSeconds + 's polls=' + $polls)
        Write-Output ('img=' + $rd.Path)
        Write-Output ('size=' + $rd.W + 'x' + $rd.H + ' region=' + $rd.Region)
        exit 0
      }
    }
    Write-Output ('NO_REPLY hash=' + $after.Hash + ' elapsed=' + [int]$sw.Elapsed.TotalSeconds + 's polls=' + $polls)
  }
}
