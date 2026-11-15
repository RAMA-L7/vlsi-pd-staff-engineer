<#
.SYNOPSIS
    Phase 14 - automated RTL-to-GDSII regression for the Sky130 test_counter design.

.DESCRIPTION
    One command runs the complete regression:

        01 synthesis        -> 02 generic LEC      -> 03 technology-mapped LEC
        04 floorplan        -> 05 placement        -> 06 CTS
        07 PDN              -> 08 power connectivity
        09 global route     -> 10 detailed route   -> 11 extraction
        12 multi-corner STA -> 13 GDSII            -> 14 DRC -> 15 LVS

    Every stage has its OWN independent acceptance checks.  A stage is only
    reported PASS when all of its checks pass - a zero exit code from the tool
    is never sufficient.  The final JSON report carries the per-stage status,
    the per-check results, artifact paths and artifact hashes.

    The 15 stages map 1:1 onto the 15 verification points of the Phase 14
    mission.

.PARAMETER Stages
    Stage names to run (default: all, in dependency order).  The named stages
    are executed; their dependencies are assumed to be present already.

.PARAMETER RunId
    Identifier for this run.  Default: a UTC timestamp.

.PARAMETER Workspace
    Root for the run directories.
    Default: <phase14 root>/workspace

.PARAMETER RunDir
    Use an existing run directory instead of creating a new one.  Used by the
    negative-test harness to re-run a single stage against a copied workspace.

.PARAMETER Resume
    Skip stages that already completed with a PASS verdict and whose artifacts
    are present.

.PARAMETER Clean
    Remove the run directory before starting.

.PARAMETER ValidateOnly
    Re-run only the acceptance checks against an existing run directory.

.PARAMETER NegativeTest
    Run the fault-injection suite instead of the main regression.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File run_regression.ps1
    powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Stages 01_synthesis,02_lec_generic
    powershell -ExecutionPolicy Bypass -File run_regression.ps1 -Resume
    powershell -ExecutionPolicy Bypass -File run_regression.ps1 -ValidateOnly -RunDir <dir>
#>
[CmdletBinding()]
param(
  [string[]]$Stages = @(),
  [string]$RunId = '',
  [string]$Workspace = '',
  [string]$RunDir = '',
  [switch]$Resume,
  [switch]$Clean,
  [switch]$ValidateOnly,
  [switch]$NegativeTest,
  [string]$Design = 'test_counter',
  [string]$RtlOverride = '',
  [string]$SdcOverride = ''
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# --------------------------------------------------------------------------- paths
$Root        = Split-Path -Parent $MyInvocation.MyCommand.Path
$StageDir    = Join-Path $Root 'stages'
$CheckDir    = Join-Path $Root 'checks'
$NegDir      = Join-Path $Root 'negative'
$EvidenceDir = Join-Path $Root 'evidence'
if (-not $Workspace) { $Workspace = Join-Path $Root 'workspace' }
New-Item -ItemType Directory -Force -Path $Workspace, $EvidenceDir | Out-Null

if (-not $RunId) { $RunId = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') }
if (-not $RunDir) { $RunDir = Join-Path $Workspace "run_$RunId" }
if ($Clean -and (Test-Path $RunDir)) {
  Remove-Item -Recurse -Force $RunDir
}
New-Item -ItemType Directory -Force -Path $RunDir | Out-Null
$LogDir = Join-Path $RunDir 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

# --------------------------------------------------------------------------- tools
$Tools      = 'D:\tools'
$Oss        = Join-Path $Tools 'oss-cad-suite'
$Platform   = if ($env:REG_PLATFORM_SKY130HD) { $env:REG_PLATFORM_SKY130HD } else { 'D:\rta-real-world-benchmark\02_projects\OpenROAD-flow-scripts\flow\platforms\sky130hd' }
$PlatformHS = if ($env:REG_PLATFORM_SKY130HS) { $env:REG_PLATFORM_SKY130HS } else { 'D:\rta-real-world-benchmark\02_projects\OpenROAD-flow-scripts\flow\platforms\sky130hs' }
$LibDir     = if ($env:REG_LIB_DIR) { $env:REG_LIB_DIR } else { 'D:\tools\src\OpenROAD\src\sta\test\sky130hd' }
# Runtime roots: configurable via environment to remove workstation-specific assumptions.
# REG_REPO / REG_TOOLS / REG_OSS_CAD / REG_PYTHON override defaults; relative defaults preserved where possible.
$Repo       = if ($env:REG_REPO) { $env:REG_REPO } else { 'D:\audit\repo' }
$Python     = if ($env:REG_PYTHON) { $env:REG_PYTHON } else { 'D:\tools\venv\Scripts\python.exe' }
if ($env:REG_TOOLS) { $Tools = $env:REG_TOOLS }
if ($env:REG_OSS_CAD) { $Oss = $env:REG_OSS_CAD }
$YosysWin   = Join-Path $Oss 'bin\yosys.exe'

# --------------------------------------------------------------------------- design configuration
# Maps design names to their RTL/SDC paths and artifact name prefix.
# Each entry supplies: rtl_rel, sdc_rel, art_prefix.
# Artifact prefixes are prepended to the stage table Art[] entries so that
# different designs produce distinct artifact paths under the same run dir.
$DesignConfig = @{
  test_counter = @{ rtl_rel = 'examples\test-counter\rtl\test_counter.v';
                     sdc_rel = 'examples\test-counter\rtl\test_counter.sdc';
                     art_prefix = 'test_counter' }
  soc_ctrl     = @{ rtl_rel = 'examples\soc_ctrl\rtl\soc_ctrl.v';
                    sdc_rel = 'examples\soc_ctrl\rtl\soc_ctrl.sdc';
                    art_prefix = 'soc_ctrl' }
}
if (-not $Design -or $Design -notin $DesignConfig.Keys) {
  throw "Unknown design '$Design'. Known: $($DesignConfig.Keys -join ', ')"
}
$DesignInfo = $DesignConfig[$Design]
$Rtl = Join-Path $Repo $DesignInfo.rtl_rel
$Sdc = Join-Path $Repo $DesignInfo.sdc_rel
$ArtPrefix = $DesignInfo.art_prefix
# Optional candidate-aware overrides (Phase 18K): keep design/top/artifact naming
# unchanged while sourcing RTL/SDC from an isolated candidate location.
if ($RtlOverride) {
  if ([System.IO.Path]::IsPathRooted($RtlOverride)) { $Rtl = $RtlOverride } else { $Rtl = Join-Path $Repo $RtlOverride }
}
if ($SdcOverride) {
  if ([System.IO.Path]::IsPathRooted($SdcOverride)) { $Sdc = $SdcOverride } else { $Sdc = Join-Path $Repo $SdcOverride }
}

# The PDN configuration validated in Phase 13 (official sky130hd/pdn.tcl plus the
# platform vias pinned).  It lives inside stages/ so the regression never
# depends on the Phase 13 directory.
$PdnTcl = Join-Path $StageDir 'pdn_phase13.tcl'

# --------------------------------------------------------------------------- path flavours
# The regression spans two execution domains:
#   * Windows-native : Yosys 0.69 (oss-cad-suite) and EQY.  Phase 13's validated
#     netlist was produced by Yosys 0.69, and this regression reproduces it
#     bit-for-bit, so the Windows toolchain is the one that is used.
#   * WSL-native : OpenROAD, OpenSTA and KLayout (all Linux ELF builds).
# The two domains therefore need different path spellings for the same files.
function ConvertTo-WslPath([string]$p) {
  if ($p -match '^([A-Za-z]):[\\/](.*)$') {
    $drive = $Matches[1].ToLower()
    return "/mnt/$drive/" + ($Matches[2] -replace '\\', '/')
  }
  return ($p -replace '\\', '/')
}
function ConvertTo-WinPath([string]$p) {
  return ($p -replace '\\', '/')
}

$runWsl = ConvertTo-WslPath $RunDir
$stageWsl = ConvertTo-WslPath $StageDir
$checkWsl = ConvertTo-WslPath $CheckDir
$envWslFileWsl = ConvertTo-WslPath (Join-Path $RunDir 'env_wsl.env')
$envWslWslPath = $envWslFileWsl

$MappedNetlistWin = Join-Path $RunDir "synth\$($ArtPrefix)_synth.v"
$MappedNetlistWsl = ConvertTo-WslPath $MappedNetlistWin

$EnvWsl = @{
  REG_RUN         = $runWsl
  REG_STAGE_DIR   = $stageWsl
  REG_CHECK_DIR   = $checkWsl
  REG_REPO        = (ConvertTo-WslPath $Repo)
  REG_RTL         = (ConvertTo-WslPath $Rtl)
  REG_SDC         = (ConvertTo-WslPath $Sdc)
  REG_PLATFORM    = (ConvertTo-WslPath $Platform)
  REG_PLATFORM_HS = (ConvertTo-WslPath $PlatformHS)
  REG_LIBDIR      = (ConvertTo-WslPath $LibDir)
  REG_LIB_TT      = (ConvertTo-WslPath (Join-Path $LibDir 'sky130_fd_sc_hd__tt_025C_1v80.lib'))
  REG_LIB_SS      = (ConvertTo-WslPath (Join-Path $LibDir 'sky130_fd_sc_hd__ss_n40C_1v40.lib'))
  REG_LIB_FF      = (ConvertTo-WslPath (Join-Path $LibDir 'sky130_fd_sc_hd__ff_n40C_1v95.lib'))
  REG_CDL         = (ConvertTo-WslPath (Join-Path $Platform 'cdl\sky130hd.cdl'))
  REG_DESIGN      = $Design
  REG_CELLS_GDS   = (ConvertTo-WslPath (Join-Path $Platform 'gds\sky130_fd_sc_hd.gds'))
  REG_PDN_TCL     = (ConvertTo-WslPath $PdnTcl)
  REG_PDK         = (ConvertTo-WslPath (Join-Path $Tools 'pdk\skywater-pdk-complete\sky130-pdk-libs-sky130_fd_sc_hd'))
  REG_KLAYOUT     = (ConvertTo-WslPath 'D:\tools\KLayout\bin\klayout.sh')
  REG_OPENROAD    = '/usr/local/bin/openroad'
  REG_STA         = '/mnt/d/tools/src/OpenSTA/build/sta'
  REG_LVS_DECK    = (ConvertTo-WslPath (Join-Path $Platform 'lvs\sky130hd.lylvs'))
  REG_DRC_DECK    = (ConvertTo-WslPath (Join-Path $Platform 'drc\sky130hd.lydrc'))
  REG_NETLIST_MAPPED = $MappedNetlistWsl
  REG_OSS            = (ConvertTo-WslPath $Oss)
}

$EnvWin = @{
  REG_RUN         = (ConvertTo-WinPath $RunDir)
  REG_STAGE_DIR   = (ConvertTo-WinPath $StageDir)
  REG_CHECK_DIR   = (ConvertTo-WinPath $CheckDir)
  REG_REPO        = (ConvertTo-WinPath $Repo)
  REG_RTL         = (ConvertTo-WinPath $Rtl)
  REG_SDC         = (ConvertTo-WinPath $Sdc)
  REG_PLATFORM    = (ConvertTo-WinPath $Platform)
  REG_PLATFORM_HS = (ConvertTo-WinPath $PlatformHS)
  REG_LIBDIR      = (ConvertTo-WinPath $LibDir)
  REG_LIB_TT      = (ConvertTo-WinPath (Join-Path $LibDir 'sky130_fd_sc_hd__tt_025C_1v80.lib'))
  REG_LIB_SS      = (ConvertTo-WinPath (Join-Path $LibDir 'sky130_fd_sc_hd__ss_n40C_1v40.lib'))
  REG_LIB_FF      = (ConvertTo-WinPath (Join-Path $LibDir 'sky130_fd_sc_hd__ff_n40C_1v95.lib'))
  REG_CDL         = (ConvertTo-WinPath (Join-Path $Platform 'cdl\sky130hd.cdl'))
  REG_CELLS_GDS   = (ConvertTo-WinPath (Join-Path $Platform 'gds\sky130_fd_sc_hd.gds'))
  REG_PDN_TCL     = (ConvertTo-WinPath $PdnTcl)
  REG_PDK         = (ConvertTo-WinPath (Join-Path $Tools 'pdk\skywater-pdk-complete\sky130-pdk-libs-sky130_fd_sc_hd'))
  REG_OSS         = (ConvertTo-WinPath $Oss)
  REG_PYTHON      = (ConvertTo-WinPath $Python)
  REG_YOSYS       = (ConvertTo-WinPath $YosysWin)
  REG_DESIGN      = $Design
  REG_NETLIST_MAPPED = (ConvertTo-WinPath $MappedNetlistWin)
  REG_ROOT        = (ConvertTo-WinPath $Root)
  REG_PDK_ROOT    = if ($env:REG_PDK_ROOT) { (ConvertTo-WinPath $env:REG_PDK_ROOT) } else { '' }
  # WSL-side paths, so that the Windows stage can hand the preparation over to
  # WSL python3 (the D: volume is reliably readable through WSL).
  REG_STAGE_DIR_WSL    = $stageWsl
  REG_EQY_DIR_WSL      = (ConvertTo-WslPath (Join-Path $RunDir 'eqy'))
  REG_PDK_WSL          = $EnvWsl.REG_PDK
  REG_RTL_WSL          = $EnvWsl.REG_RTL
  REG_OSS_WSL          = $EnvWsl.REG_OSS
  REG_NETLIST_MAPPED_WSL = $EnvWsl.REG_NETLIST_MAPPED
  REG_PREP_WSL         = $envWslWslPath
}

# --------------------------------------------------------------------------- stage table
# Kind:
#   win       - Windows-native PowerShell stage script
#   wsl-bash  - bash script executed inside WSL
#   wsl-or    - OpenROAD TCL script executed inside WSL
$StageTable = @(
  @{ N='00_stage00';        Deps=@();               Kind='win';      File='00_stage00.ps1';        Desc='Stage-00 pre-flight: RTL/SDC/tool/environment/config checks (mandatory gate)'
     Art=@('stage00/stage00_result.json','stage00/stage00_report.txt','stage00/stage00.log') }
  @{ N='01_synthesis';       Deps=@('00_stage00');   Kind='win';      File='01_synthesis.ps1';       Desc='Yosys 0.69 synthesis: generic + Sky130 technology-mapped netlists'
     Art=@("synth/generic_${ArtPrefix}_synth.v","synth/${ArtPrefix}_synth.v") }
  @{ N='02_lec_generic';     Deps=@('01_synthesis');Kind='win';      File='02_lec_generic.ps1';     Desc='Generic-netlist LEC (RTL vs generic netlist) + negative control'
     Art=@('lec_generic/lec_generic.log','lec_generic/lec_generic_negative.log') }
  @{ N='03_lec_mapped';      Deps=@('01_synthesis');Kind='win';      File='03_lec_mapped.ps1';      Desc='Technology-mapped LEC (EQY, official OSS CAD preprocessing) + negative control'
     Art=@('eqy/eqy_positive.log','eqy/eqy_negative.log') }
  @{ N='04_floorplan';       Deps=@('01_synthesis');Kind='wsl-or';   File='04_floorplan.tcl';       Desc='OpenROAD floorplan 120x60 um, rows/tracks, pins, well-tie grid'
     Art=@('04_floorplan.odb') }
  @{ N='05_placement';       Deps=@('04_floorplan');Kind='wsl-or';   File='05_placement.tcl';       Desc='Contiguous functional-cell placement behind the first well tie + detailed placement'
     Art=@('05_place.odb') }
  @{ N='06_cts';             Deps=@('05_placement');Kind='wsl-or';   File='06_cts.tcl';             Desc='Clock tree synthesis (before the PDN)'
     Art=@('06_cts.odb') }
  @{ N='07_pdn';             Deps=@('06_cts');      Kind='wsl-or';   File='07_pdn.tcl';             Desc='PDN generation (official pdn.tcl + pinned platform vias) + global connect'
     Art=@('07_pdn.odb','07_pdn.def') }
  @{ N='08_power_connectivity';Deps=@('07_pdn');      Kind='wsl-or';   File='08_power_connectivity.tcl';Desc='Independent power-grid connectivity check on VDD and VSS'
     Art=@('08_power_connectivity.txt') }
  @{ N='09_global_route';    Deps=@('07_pdn');      Kind='wsl-or';   File='09_global_route.tcl';    Desc='Global routing'
     Art=@('09_groute.odb','global_route.guide') }
  @{ N='10_detailed_route';  Deps=@('09_global_route');Kind='wsl-or';File='10_detailed_route.tcl';  Desc='Detailed routing'
     Art=@('10_route.odb','10_route.def') }
  @{ N='11_extract';         Deps=@('10_detailed_route');Kind='wsl-or';File='11_extract.tcl';       Desc='Parasitic extraction (SPEF) and final export'
     Art=@('11_final.odb','11_final.def','11_final.v','11_final.sdc','11_final.spef') }
  @{ N='12_sta';             Deps=@('11_extract');   Kind='wsl-bash'; File='12_sta.sh';             Desc='Multi-corner STA (TT / SS / FF) with SPEF parasitics'
     Art=@('sta/sta_all.log','sta/sta_summary.txt') }
  @{ N='13_gds';             Deps=@('11_extract');   Kind='wsl-bash'; File='13_gds.sh';             Desc='DEF to GDSII conversion'
     Art=@("${ArtPrefix}_pdn.gds") }
  @{ N='14_drc';             Deps=@('13_gds');       Kind='wsl-bash'; File='14_drc.sh';             Desc='Official Sky130 DRC deck + report database save/reopen'
     Art=@('drc/drc.log','drc/drc_classification.txt','tests/sky130hd_drc_report.rpt') }
  @{ N='15_lvs';             Deps=@('13_gds','11_extract');Kind='wsl-bash';File='15_lvs.sh';          Desc='Official Sky130 LVS deck with a reference netlist generated from the routed ODB'
     Art=@('15_lvs/15_lvs.lvsdb','lvs_compare.txt') }
)

$Mandatory = @($StageTable | ForEach-Object { $_.N })

# --------------------------------------------------------------------------- helpers
function Write-Json($obj, $path) {
  $json = $obj | ConvertTo-Json -Depth 12
  [System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-Sha256([string]$p) {
  if (-not (Test-Path $p)) { return $null }
  return (Get-FileHash -Algorithm SHA256 $p).Hash.ToLower()
}

function Write-EnvFile([hashtable]$map, [string]$path, [string]$style) {
  $lines = @("# Phase 14 regression environment ($style)")
  $lines += "# generated: $((Get-Date).ToUniversalTime().ToString('o'))"
  foreach ($k in ($map.Keys | Sort-Object)) {
    if ($style -eq 'wsl') { $lines += "export $k=$($map[$k])" }
    else { $lines += "$k=$($map[$k])" }
  }
  if ($style -eq 'wsl') {
    [System.IO.File]::WriteAllText($path, ($lines -join "`n") + "`n")
  } else {
    [System.IO.File]::WriteAllLines($path, $lines)
  }
}

# Write the environment files that are part of the evidence.
$envWslFile = Join-Path $RunDir 'env_wsl.env'
$envWinFile = Join-Path $RunDir 'env_win.env'
Write-EnvFile $EnvWsl $envWslFile 'wsl'
Write-EnvFile $EnvWin $envWinFile 'windows'

# --------------------------------------------------------------------------- report skeleton
$report = [ordered]@{
  schema_version   = 'phase14-regression-1.0'
  run_id           = $RunId
  timestamp_utc    = (Get-Date).ToUniversalTime().ToString('o')
  finished_utc     = ''
  git_branch       = ''
  git_head         = ''
  git_status_sha256= ''
  workspace        = $Workspace
  run_dir          = $RunDir
  inputs           = @{}
  tools            = @{}
  pdk_revisions    = @{}
  stages           = @()
  artifacts        = @{}
  overall          = 'INCONCLUSIVE'
  not_executed     = @()
  failure_messages = @()
}

Push-Location $Repo
$report.git_branch = (git rev-parse --abbrev-ref HEAD).Trim()
$report.git_head   = (git rev-parse HEAD).Trim()
$st = (git status --short) -join "`n"
$report.git_status_sha256 = [BitConverter]::ToString(
  [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($st))).Replace('-','').ToLower()
Pop-Location

Write-Host "==========================================================="
Write-Host " Phase 14 regression   run=$RunId"
Write-Host " run dir: $RunDir"
Write-Host "==========================================================="

# --------------------------------------------------------------------------- input hashes
$inputFiles = @(
  $Rtl, $Sdc,
  (Join-Path $Platform 'cdl\sky130hd.cdl'),
  (Join-Path $Platform 'lvs\sky130hd.lylvs'),
  (Join-Path $Platform 'drc\sky130hd.lydrc'),
  (Join-Path $Platform 'pdn.tcl'),
  (Join-Path $Platform 'setRC.tcl'),
  (Join-Path $Platform 'make_tracks.tcl'),
  (Join-Path $Platform 'sky130hd.lyt'),
  (Join-Path $Platform 'lef\sky130_fd_sc_hd.tlef'),
  (Join-Path $Platform 'lef\sky130_fd_sc_hd_merged.lef'),
  (Join-Path $Platform 'gds\sky130_fd_sc_hd.gds'),
  (Join-Path $LibDir 'sky130_fd_sc_hd__tt_025C_1v80.lib'),
  (Join-Path $LibDir 'sky130_fd_sc_hd__ss_n40C_1v40.lib'),
  (Join-Path $LibDir 'sky130_fd_sc_hd__ff_n40C_1v95.lib'),
  (Join-Path $Platform 'lib\sky130_fd_sc_hd__tt_025C_1v80.lib'),
  $PdnTcl
)
foreach ($f in $inputFiles) {
  $report.inputs[$f] = if (Test-Path $f) { Get-Sha256 $f } else { 'MISSING' }
}

# --------------------------------------------------------------------------- tool versions
$toolProbe = [ordered]@{
  yosys        = "cmd /c `"call `"$Oss\environment.bat`" >nul 2>&1 && `"$YosysWin`" -V`""
  openroad    = "wsl -d Ubuntu-24.04 -- /usr/local/bin/openroad -version"
  opensta     = "wsl -d Ubuntu-24.04 -- /mnt/d/tools/src/OpenSTA/build/sta -version"
  klayout     = "wsl -d Ubuntu-24.04 -- /mnt/d/tools/KLayout/bin/klayout.sh -v"
  python      = "& `"$Python`" --version"
  eqy         = "cmd /c `"call `"$Oss\environment.bat`" >nul 2>&1 && `"$Oss\bin\eqy.exe`" --help`""
  make        = "cmd /c `"where make`""
  wsl_bash    = "wsl -d Ubuntu-24.04 -- bash --version"
}
foreach ($k in $toolProbe.Keys) {
  $v = ''
  try {
    $v = ((Invoke-Expression $toolProbe[$k] 2>&1 | Select-Object -First 4) -join ' | ').Trim()
  } catch { $v = 'ERROR: ' + $_.Exception.Message }
  if (-not $v) { $v = 'no output' }
  $report.tools[$k] = $v
}

# --------------------------------------------------------------------------- PDK revisions
foreach ($d in (Get-ChildItem 'D:\tools\pdk\skywater-pdk-complete' -Directory)) {
  $r = (& git -C $d.FullName rev-parse HEAD 2>$null)
  $report.pdk_revisions[$d.Name] = if ($r) { $r.Trim() } else { 'unknown' }
}

# --------------------------------------------------------------------------- stage execution
$statusFile = Join-Path $RunDir 'stage_status.json'
$status = [ordered]@{ stages = @() }
if (Test-Path $statusFile) {
  try { $status = Get-Content $statusFile -Raw | ConvertFrom-Json } catch { $status = [ordered]@{ stages = @() } }
}
$prevStatus = @{}
foreach ($s in $status.stages) { $prevStatus[$s.name] = $s }

$order = $Mandatory
if ($Stages.Count -gt 0) {
  # When the script is invoked through `powershell -File`, a comma separated
  # list arrives as ONE string.  Split it here so both forms work.
  $flat = @()
  foreach ($s in $Stages) { $flat += ($s -split ',') }
  $flat = @($flat | Where-Object { $_.Trim() -ne '' } | ForEach-Object { $_.Trim() })
  $order = $Mandatory | Where-Object { $flat -contains $_ }
  if ($order.Count -eq 0) { throw "No known stage matched -Stages. Known: $($Mandatory -join ', ')" }
}

$stageResults = @()
foreach ($name in $order) {
  $s = $StageTable | Where-Object { $_.N -eq $name }
  $logName = "$name.log"
  $logWin = Join-Path $LogDir $logName
  $logWsl = ConvertTo-WslPath $logWin

  # --- resume support -------------------------------------------------------
  if ($Resume -and $prevStatus.ContainsKey($name)) {
    $ps = $prevStatus[$name]
    $artifactsPresent = $true
    foreach ($a in $s.Art) {
      if (-not (Test-Path (Join-Path $RunDir $a))) { $artifactsPresent = $false; break }
    }
    if ($ps.executed -and $ps.status -eq 'PASS' -and $artifactsPresent) {
      Write-Host ""
      Write-Host "--------- stage $name : SKIPPED (resume, already PASS)"
      $stageResults += [ordered]@{
        name = $name; description = $s.Desc; kind = $s.Kind; domain = ''
        executed = $true; skipped = $true; exit_code = $ps.exit_code
        started = $ps.started; ended = $ps.ended; duration_s = $ps.duration_s
        log = $logWin; artifacts = @($s.Art); checks = @()
        status = 'PASS'
      }
      continue
    }
  }

  Write-Host ""
  Write-Host "--------- stage $name : $($s.Desc)"
  $t0 = Get-Date
  $rc = 1
  $domain = ''
  try {
    switch ($s.Kind) {
      'win' {
        $domain = 'windows'
        foreach ($k in $EnvWin.Keys) { Set-Item -Path "Env:$k" -Value $EnvWin[$k] }
        $ps1 = Join-Path $StageDir $s.File
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ps1
        $rc = $LASTEXITCODE
      }
      'wsl-bash' {
        $domain = 'wsl'
        $scriptWsl = ConvertTo-WslPath (Join-Path $StageDir $s.File)
        $cmd = "source '$envWslWslPath' && bash '$scriptWsl' > '$logWsl' 2>&1"
        & wsl.exe -d Ubuntu-24.04 -- bash -c $cmd
        $rc = $LASTEXITCODE
      }
      'wsl-or' {
        $domain = 'wsl'
        $scriptWsl = ConvertTo-WslPath (Join-Path $StageDir $s.File)
        $cmd = "source '$envWslWslPath' && /usr/local/bin/openroad -no_init -exit '$scriptWsl' > '$logWsl' 2>&1"
        & wsl.exe -d Ubuntu-24.04 -- bash -c $cmd
        $rc = $LASTEXITCODE
      }
      default { throw "unknown stage kind '$($s.Kind)'" }
    }
  } catch {
    ($_ | Out-String) | Out-File -FilePath $logWsl -Append
    $rc = 90
  }
  $t1 = Get-Date
  Write-Host ("  exit={0}  elapsed={1:N1}s" -f $rc, ($t1 - $t0).TotalSeconds)
  if (Test-Path $logWin) { Get-Content $logWin -Tail 10 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" } }

  $stageResults += [ordered]@{
    name = $name; description = $s.Desc; kind = $s.Kind; domain = $domain
    executed = $true; skipped = $false; exit_code = $rc
    started = $t0.ToUniversalTime().ToString('o'); ended = $t1.ToUniversalTime().ToString('o')
    duration_s = [math]::Round(($t1 - $t0).TotalSeconds, 1)
    log = $logWin; artifacts = @($s.Art); checks = @()
    status = 'PENDING'
  }

  # persist the execution status after every stage (crash-resume friendly)
  $status.stages = @($stageResults | ForEach-Object {
    [ordered]@{ name=$_.name; executed=$_.executed; exit_code=$_.exit_code
                started=$_.started; ended=$_.ended; duration_s=$_.duration_s
                status=$_.status }
  })
  Write-Json $status $statusFile

  if ($rc -ne 0) {
    Write-Host "  stage FAILED (exit $rc) - remaining stages are skipped"
    break
  }
}

# --------------------------------------------------------------------------- acceptance checks
$accJson = Join-Path $RunDir 'acceptance.json'
$accTxt = Join-Path $RunDir 'acceptance.txt'
if (-not $ValidateOnly) {
  Write-Host ""
  Write-Host "--------- independent acceptance checks"
  $accCmd = "source '$envWslWslPath' && python3 '$checkWsl/acceptance.py' '$runWsl' '$runWsl/logs' > '$runWsl/acceptance.txt' 2>&1"
  & wsl.exe -d Ubuntu-24.04 -- bash -c $accCmd
  $accRc = $LASTEXITCODE
  if (Test-Path $accTxt) { Get-Content $accTxt | ForEach-Object { Write-Host "    $_" } }
  Write-Host "  acceptance exit=$accRc"
}

# --------------------------------------------------------------------------- merge results
$accResults = @()
if (Test-Path $accJson) {
  try { $accResults = (Get-Content $accJson -Raw | ConvertFrom-Json).checks } catch { $accResults = @() }
}
$byStage = @{}
foreach ($c in $accResults) {
  if (-not $byStage.ContainsKey($c.stage)) { $byStage[$c.stage] = @() }
  $byStage[$c.stage] += $c
}

foreach ($sr in $stageResults) {
  $checks = @()
  if ($byStage.ContainsKey($sr.name)) { $checks = @($byStage[$sr.name]) }
  $failed  = @($checks | Where-Object { $_.status -eq 'FAIL' })
  $pending = @($checks | Where-Object { $_.status -eq 'NOT_TESTED' })
  if ($checks.Count -eq 0) {
    $sr.status = 'INCONCLUSIVE'
  } elseif ($failed.Count -gt 0) {
    $sr.status = 'FAIL'
  } elseif ($pending.Count -gt 0) {
    $sr.status = 'INCONCLUSIVE'
  } else {
    $sr.status = 'PASS'
  }
  $sr['checks'] = $checks
}
$report.stages = $stageResults

# --------------------------------------------------------------------------- artifact manifest
$manifest = [ordered]@{}
foreach ($s in $StageTable) {
  foreach ($a in $s.Art) {
    $p = Join-Path $RunDir $a
    $manifest[$a] = [ordered]@{ path = $p; exists = (Test-Path $p); sha256 = (Get-Sha256 $p); bytes = $(if (Test-Path $p) { (Get-Item $p).Length } else { 0 }) }
  }
}
foreach ($extra in @('report.json','acceptance.json','stage_status.json','env_wsl.env','env_win.env')) {
  $p = Join-Path $RunDir $extra
  $manifest[$extra] = [ordered]@{ path = $p; exists = (Test-Path $p); sha256 = (Get-Sha256 $p); bytes = $(if (Test-Path $p) { (Get-Item $p).Length } else { 0 }) }
}
$report.artifacts = $manifest

# --------------------------------------------------------------------------- overall verdict
$executed = @($stageResults | Where-Object { $_.executed } | ForEach-Object { $_.name })
$missing = @($Mandatory | Where-Object { $executed -notcontains $_ })
$notPass = @($stageResults | Where-Object { $_.status -ne 'PASS' })
$report.not_executed = $missing

$failMsgs = @()
foreach ($sr in $stageResults) {
  foreach ($c in @($sr.checks)) {
    if ($c.status -ne 'PASS') { $failMsgs += "$($sr.name)/$($c.name): $($c.status) - $($c.detail)" }
  }
  if ($sr.exit_code -ne 0) { $failMsgs += "$($sr.name): stage exit code $($sr.exit_code)" }
}
$report.failure_messages = $failMsgs

if ($missing.Count -gt 0) {
  $report.overall = 'INCONCLUSIVE'
} elseif ($notPass.Count -gt 0) {
  $report.overall = 'FAIL'
} else {
  $report.overall = 'PASS'
}
$report.finished_utc = (Get-Date).ToUniversalTime().ToString('o')

# persist the final per-stage status
$status.stages = @($stageResults | ForEach-Object {
  [ordered]@{ name=$_.name; executed=$_.executed; exit_code=$_.exit_code
              started=$_.started; ended=$_.ended; duration_s=$_.duration_s
              status=$_.status }
})
Write-Json $status $statusFile

Write-Json $report (Join-Path $RunDir 'report.json')
Copy-Item (Join-Path $RunDir 'report.json') (Join-Path $EvidenceDir "report_$RunId.json") -Force
if (Test-Path $accTxt) { Copy-Item $accTxt (Join-Path $EvidenceDir "acceptance_$RunId.txt") -Force }
Copy-Item (Join-Path $RunDir 'acceptance.json') (Join-Path $EvidenceDir "acceptance_$RunId.json") -Force -ErrorAction SilentlyContinue

# --------------------------------------------------------------------------- markdown summary
$md = @()
$md += "# Phase 14 regression run $RunId"
$md += ''
$md += "- overall: **$($report.overall)**"
$md += "- run dir: ``$RunDir``"
$md += "- git: ``$($report.git_branch)`` @ ``$($report.git_head)``"
$md += "- started: $($report.timestamp_utc)"
$md += "- finished: $($report.finished_utc)"
$md += ''
$md += '| # | Stage | Status | Exit | Checks | Duration |'
$md += '|---:|---|---|---:|---|---:|'
$i = 0
foreach ($sr in $stageResults) {
  $i++
  $ck = @($sr.checks)
  $md += "| $i | $($sr.name) | $($sr.status) | $($sr.exit_code) | $($ck.Count) checks, $(@($ck | Where-Object { $_.status -ne 'PASS' }).Count) not passing | $($sr.duration_s)s |"
}
$md += ''
$md += '## Checks not passing'
$anyFail = $false
foreach ($sr in $stageResults) {
  foreach ($c in @($sr.checks)) {
    if ($c.status -ne 'PASS') { $anyFail = $true; $md += "- ``$($sr.name)`` / $($c.name): **$($c.status)** - $($c.detail)" }
  }
}
foreach ($m in $failMsgs) { if ($m -notmatch '/') { $anyFail = $true; $md += "- $m" } }
if (-not $anyFail) { $md += '- none' }
$md += ''
$md += '## Tool versions'
foreach ($k in $report.tools.Keys) { $md += "- **$k**: $($report.tools[$k])" }
$md += ''
$md += '## Artifact manifest'
$md += '| Artifact | Exists | Bytes | SHA-256 |'
$md += '|---|:-:|---:|---|'
foreach ($k in $manifest.Keys) {
  $m = $manifest[$k]
  $md += "| ``$k`` | $($m.exists) | $($m.bytes) | $($m.sha256) |"
}
$md | Out-File -FilePath (Join-Path $RunDir 'summary.md') -Encoding utf8
Copy-Item (Join-Path $RunDir 'summary.md') (Join-Path $EvidenceDir "summary_$RunId.md") -Force

Write-Host ""
Write-Host "==========================================================="
Write-Host " OVERALL: $($report.overall)"
Write-Host " report : $(Join-Path $RunDir 'report.json')"
Write-Host " summary: $(Join-Path $RunDir 'summary.md')"
Write-Host "==========================================================="
if ($report.overall -ne 'PASS') { exit 1 }
exit 0
