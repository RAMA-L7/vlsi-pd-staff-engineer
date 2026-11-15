# ============================================================================
# Phase 18G regression - STAGE 03: TECHNOLOGY-MAPPED LEC (EQY)
#
# Environment: REG_RUN, REG_OSS, REG_STAGE_DIR, REG_PYTHON, REG_PDK, REG_RTL,
#             REG_DESIGN, and WSL helper vars set by orchestrator.
# ============================================================================
$ErrorActionPreference = 'Continue'

$Run      = $env:REG_RUN
$Oss      = $env:REG_OSS
$StageDir = $env:REG_STAGE_DIR
$Py       = $env:REG_PYTHON
$Des      = $env:REG_DESIGN
if (-not $Des) { $Des = 'test_counter' }
$Top = $Des

foreach ($v in @('REG_RUN','REG_OSS','REG_STAGE_DIR','REG_PYTHON','REG_PDK','REG_RTL')) {
  if (-not (Get-Item "Env:$v" -ErrorAction SilentlyContinue)) { Write-Host "ERROR: $v not set"; exit 130 }
}

$EqyDir = Join-Path $Run 'eqy'
New-Item -ItemType Directory -Force -Path $EqyDir | Out-Null

Write-Host "=== stage 03 technology-mapped LEC (EQY) ==="
Write-Host "  DESIGN: $Top"

# --- prepare the inputs (models, preprocessed file, netlists, .eqy files) ----
Write-Host "--- preparing inputs ---"
$prepLog = Join-Path $EqyDir 'eqy_prep.log'
$prepWsl = $env:REG_PREP_WSL
$cmd = "source '$prepWsl' && python3 '$env:REG_STAGE_DIR_WSL/03_eqy_prep.py' " +
       "'$env:REG_EQY_DIR_WSL' '$env:REG_PDK_WSL' '$env:REG_RTL_WSL' " +
       "'$env:REG_NETLIST_MAPPED_WSL' '$env:REG_OSS_WSL' > '$env:REG_EQY_DIR_WSL/eqy_prep.log' 2>&1"
& wsl.exe -d Ubuntu-24.04 -- bash -c $cmd
$prepRc = $LASTEXITCODE
if (Test-Path $prepLog) { Get-Content $prepLog | ForEach-Object { Write-Host "    $_" } }
if ($prepRc -ne 0) {
  Write-Host "ERROR: EQY input preparation failed (exit $prepRc)"
  exit 130
}

# --- stage the plugins, eqy-script.py and the LF-forcing wrapper -------------
Copy-Item (Join-Path $Oss 'share\yosys\plugins\eqy_*.so') $EqyDir -Force
Copy-Item (Join-Path $Oss 'bin\eqy-script.py') $EqyDir -Force
Copy-Item (Join-Path $StageDir '03_eqy_lf.py') $EqyDir -Force

# Project-local SBY shim for EQY-generated Bash strategy execution (Phase 18J).
# Generated strategies invoke bare `sby`; OSS CAD Suite provides `sby.exe` only.
# Placed in EqyDir and prepended to PATH so bash/make resolves it without
# global PATH changes or modifying the SBY installation.
$sbyShim = Join-Path $EqyDir 'sby'
$sbyExeWin = (Join-Path $Oss 'bin\sby.exe').Replace('\', '/')
$drive = $sbyExeWin.Substring(0,1).ToLower()
$sbyExeWsl = '/mnt/' + $drive + $sbyExeWin.Substring(2)
@(
  '#!/bin/sh'
  "SBY_WSL='$sbyExeWsl'"
  "SBY_WIN='$sbyExeWin'"
  'if [ -x "$SBY_WSL" ]; then exec "$SBY_WSL" "$@"; fi'
  'exec "$SBY_WIN" "$@"'
) -join "`n" | Set-Content -NoNewline -Encoding ascii $sbyShim
try {
  $sbyShimWsl = $sbyShim -replace '^([A-Za-z]):', { '/mnt/' + $_.Groups[1].Value.ToLower() } -replace '\\','/'
  wsl.exe -d Ubuntu-24.04 -- chmod +x $sbyShimWsl 2>$null
} catch { }

# --- environment -------------------------------------------------------------
$envBat = Join-Path $Oss 'environment.bat'
if (Test-Path $envBat) {
  $envLines = cmd /c "call `"$envBat`" >nul 2>&1 && set" 2>&1
  foreach ($line in $envLines) {
    if ($line -match '^([^=]+)=(.*)$') {
      [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process')
    }
  }
}
$env:PATH = "$EqyDir;$env:PATH;D:\tools\mingw64\bin;C:\Program Files\Git\usr\bin"
$env:SHELL = 'sh.exe'
$env:PYTHONPATH = Join-Path $Oss 'share\yosys\python3'

function Invoke-Eqy {
  param([string]$Config, [string]$WorkDir, [string]$Log)
  if (Test-Path $WorkDir) { Remove-Item -Recurse -Force $WorkDir }
  Push-Location $EqyDir
  & $Py (Join-Path $EqyDir '03_eqy_lf.py') -f $Config -d $WorkDir *> $Log
  $rc = $LASTEXITCODE
  Pop-Location
  return $rc
}

$posCfg = "$Top.eqy"
$negCfg = "${Top}_broken.eqy"
$posLog = Join-Path $EqyDir 'eqy_positive.log'
$negLog = Join-Path $EqyDir 'eqy_negative.log'

Write-Host "--- positive case ($posCfg) ---"
$rcPos = Invoke-Eqy $posCfg 'work' $posLog
Write-Host "  positive exit=$rcPos"
if (Test-Path $posLog) {
  Select-String -Path $posLog -Pattern 'Proved equivalence|Successfully proved|Could not prove|ERROR' |
    ForEach-Object { Write-Host "    $($_.Line)" }
}

Write-Host "--- negative control ($negCfg) ---"
$rcNeg = Invoke-Eqy $negCfg 'work_broken' $negLog
Write-Host "  negative exit=$rcNeg (must be non-zero)"
if (Test-Path $negLog) {
  Select-String -Path $negLog -Pattern 'Proved equivalence|Successfully proved|Could not prove|ERROR' |
    ForEach-Object { Write-Host "    $($_.Line)" }
}

Write-Host "  EQY_POSITIVE_ACTUAL_EXIT=$rcPos"
Write-Host "  EQY_NEGATIVE_ACTUAL_EXIT=$rcNeg"
if ($rcPos -ne 0) {
  Write-Host "ERROR: technology-mapped LEC did not prove equivalence (actual exit=$rcPos)"
  exit $rcPos
}
if ($rcNeg -eq 0) {
  Write-Host "ERROR: negative control was NOT detected - the harness is insensitive (actual exit=$rcNeg)"
  exit 132
}
if (-not (Select-String -Path $posLog -Pattern 'Successfully proved designs equivalent' -Quiet)) {
  Write-Host "ERROR: no 'Successfully proved designs equivalent' in the positive log (actual exit=$rcPos)"
  exit 133
}
Write-Host "STAGE03_DONE"
exit 0
