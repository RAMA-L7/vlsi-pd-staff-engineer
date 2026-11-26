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
  [string]$SdcOverride = '',
  # TR-04 test seams. Defaults preserve the production control flow exactly.
  [string]$WslDistro = 'Ubuntu-24.04',
  [string]$EvidenceDirOverride = '',
  [string]$StageExecutor = '',
  [string]$AcceptanceExecutor = '',
  [string]$ManifestExecutor = '',
  [switch]$SkipToolProbe
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'

# --------------------------------------------------------------------------- paths
$Root        = Split-Path -Parent $MyInvocation.MyCommand.Path
$StageDir    = Join-Path $Root 'stages'
$CheckDir    = Join-Path $Root 'checks'
$NegDir      = Join-Path $Root 'negative'
$EvidenceDir = if ($EvidenceDirOverride) { $EvidenceDirOverride } else { Join-Path $Root 'evidence' }
if (-not $Workspace) { $Workspace = Join-Path $Root 'workspace' }
New-Item -ItemType Directory -Force -Path $Workspace, $EvidenceDir | Out-Null

if ($RunDir) {
  if (-not $RunId) { $RunId = Split-Path -Leaf $RunDir }
} else {
  if (-not $RunId) { $RunId = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ') }
  $RunDir = Join-Path $Workspace "run_$RunId"
}
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

# --- STA path resolution (BEGIN) ---------------------------------------------
# Precedence: explicit env override (ASSISTANT_STA, then REG_STA) -> the
# validated recorded binary -> the historical legacy path.  Recorded /mnt paths
# are stat'ed through their Windows <drive>:\ spelling so a stale default cannot
# silently block stage 12.  OpenSTA also needs its CUDD shared library at run
# time, so the validated lib dir is emitted as LD_LIBRARY_PATH (appendable).
# Self-contained and injectable so the contract is directly testable without
# running the whole regression.
$RegStaValidated = '/mnt/d/tools/opensta/bin/sta'
$RegStaLegacy    = '/mnt/d/tools/src/OpenSTA/build/sta'
$RegStaLibDefault = '/mnt/d/tools/opensta/lib'

function Test-RegStaCandidate([string]$Path) {
  if ($Path -match '^/mnt/([A-Za-z])/(.*)$') {
    $win = "$($Matches[1].ToUpper()):\$($Matches[2] -replace '/', '\')"
    return (Test-Path -LiteralPath $win)
  }
  # WSL-native paths cannot be stat'ed from Windows; leave them to WSL itself.
  return $true
}

function Resolve-RegStaPath {
  param(
    [string]$Override = '',
    [scriptblock]$Exists = $null
  )
  $probe = if ($null -eq $Exists) { ${function:Test-RegStaCandidate} } else { $Exists }
  if (-not [string]::IsNullOrWhiteSpace($Override)) {
    $p = $Override.Trim()
    if (-not (& $probe $p)) {
      Write-Warning "STA path override '$p' is not visible from Windows; using it anyway (it may be WSL-only)."
    }
    return [pscustomobject]@{ Path = $p; Source = 'env-override' }
  }
  foreach ($c in @(
      @{ Path = $RegStaValidated; Source = 'validated-recorded' },
      @{ Path = $RegStaLegacy; Source = 'legacy' })) {
    if (& $probe $c.Path) {
      return [pscustomobject]@{ Path = $c.Path; Source = $c.Source }
    }
  }
  throw "OpenSTA executable not found. Looked for '$RegStaValidated' then '$RegStaLegacy'. Set ASSISTANT_STA (or REG_STA) to the WSL path of the sta binary, e.g. ASSISTANT_STA=/mnt/d/tools/opensta/bin/sta"
}

function Resolve-RegStaLibPath {
  param(
    [string]$Override = '',
    [string]$Base = '',
    [scriptblock]$Exists = $null
  )
  $probe = if ($null -eq $Exists) { ${function:Test-RegStaCandidate} } else { $Exists }
  $dir = if ([string]::IsNullOrWhiteSpace($Override)) { $RegStaLibDefault } else { $Override.Trim() }
  if (-not (& $probe $dir)) {
    Write-Warning "STA library directory '$dir' is not visible from Windows; using it anyway (it may be WSL-only)."
  }
  $value = if ([string]::IsNullOrWhiteSpace($Base)) { $dir } else { "$Base`:$dir" }
  return [pscustomobject]@{ Value = $value; Dir = $dir }
}
# --- STA path resolution (END) -----------------------------------------------

$StaOverride = ''
$StaOverrideVar = ''
foreach ($staVar in @('ASSISTANT_STA', 'REG_STA')) {
  $staVal = [Environment]::GetEnvironmentVariable($staVar)
  if (-not [string]::IsNullOrWhiteSpace($staVal)) {
    $StaOverride = $staVal.Trim()
    $StaOverrideVar = $staVar
    break
  }
}
$StaResolution = Resolve-RegStaPath -Override $StaOverride
$StaPath = $StaResolution.Path
if ($StaOverrideVar) {
  Write-Host "STA path: $StaPath (source: $StaOverrideVar)"
} else {
  Write-Host "STA path: $StaPath (source: $($StaResolution.Source))"
}

$StaLibOverride = ''
$StaLibOverrideVar = ''
foreach ($staLibVar in @('ASSISTANT_STA_LIB_DIR', 'REG_STA_LIB_DIR')) {
  $staLibVal = [Environment]::GetEnvironmentVariable($staLibVar)
  if (-not [string]::IsNullOrWhiteSpace($staLibVal)) {
    $StaLibOverride = $staLibVal.Trim()
    $StaLibOverrideVar = $staLibVar
    break
  }
}
$StaLibResolution = Resolve-RegStaLibPath -Override $StaLibOverride -Base $env:LD_LIBRARY_PATH
$StaLibDir = $StaLibResolution.Dir
$StaLdLibraryPath = $StaLibResolution.Value
Write-Host "STA LD_LIBRARY_PATH: $StaLdLibraryPath (source: $(if ($StaLibOverrideVar) { $StaLibOverrideVar } else { 'default' }))"

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
  REG_STA         = $StaPath
  # OpenSTA's ELF needs libcudd-3.0.0.so.0 from its build lib dir at run time.
  LD_LIBRARY_PATH = $StaLdLibraryPath
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

# --------------------------------------------------------------------------- SR-02 gate policy
# Explicit dependency table + authoritative required-acceptance checks per stage.
# This mirrors the pure policy model in checks/acceptance.py (STAGE_DEPS,
# REQUIRED_CHECKS, STAGE_INPUT_TAGS).  No stage is authorised to run until every
# dependency has fresh engineering acceptance AND its evidence is persisted.
$RequiredChecks = @{
  '00_stage00' = @('stage00_result_exists','stage00_report_exists','stage00_overall_pass','stage00_no_mandatory_failures')
  '01_synthesis' = @('generic_netlist_exists','mapped_netlist_exists','netlists_parse','cell_counts_recorded','sky130_cells_present_after_mapping','sequential_cell_mapped','no_unresolved_modules','yosys_reported_no_errors')
  '02_lec_generic' = @('positive_proved','no_unproven_points','compare_points_generated','negative_control_detected','negative_control_netlist_differs')
  '03_lec_mapped' = @('genuine_sky130_models_used','official_preprocessor_used','all_partitions_proved','designs_equivalent','negative_control_rejected')
  '04_floorplan' = @('die_area_valid','rows_and_sites_defined','pins_assigned','tapcells_inserted','odb_written')
  '05_placement' = @('functional_cells_contiguous','detailed_placement_completed','no_placement_violations','odb_written')
  '06_cts' = @('clock_sinks_identified','clock_tree_built','clock_connectivity_reported','odb_written')
  '07_pdn' = @('power_and_ground_nets_correct','power_grid_geometry_generated','specialnets_contains_routed_power','odb_and_def_written')
  '08_power_connectivity' = @('check_power_grid_vdd','check_power_grid_vss','stdcell_power_pins_connected','welltie_connectivity_verified','no_disconnected_power_terminals','specialnets_contains_routed_power')
  '09_global_route' = @('global_routing_completed','guide_file_written','odb_written')
  '10_detailed_route' = @('detailed_routing_completed','router_violations_zero','routed_def_and_odb_written')
  '11_extract' = @('spef_generated','parasitic_segments_nonzero','resistance_segments_nonzero','extracted_design_matches_routed','spef_covers_design_nets')
  '12_sta' = @('all_corners_reported','libraries_loaded','constraints_applied','spef_loaded','setup_reports_generated','paths_analysed_per_corner','no_timing_violators','wns_recorded_per_corner','wns_non_negative','no_unconstrained_paths')
  '13_gds' = @('gds_generated','topcell_name_correct','stdcell_geometry_merged','nonempty_geometry','via_cut_layers_present','power_geometry_present','real_top_level_port_labels_preserved')
  '14_drc' = @('all_rule_categories_execute','zero_actual_violations','helper_regions_excluded','report_db_saved','report_db_reopens','stored_totals_agree_with_log','drc_deck_identity')
  '15_lvs' = @('official_deck_used','lvs_deck_identity','genuine_sky130_cdl_used','reference_from_physical_database','topcell_configured','verdict_match','lvs_database_written','extracted_netlist_written','device_counts_match','no_unmatched_devices','no_unmatched_nets','no_unmatched_subcircuits','power_connectivity_correct','power_structure_verified','independent_structure_match')
}
foreach ($n in $Mandatory) {
  $rc = @($RequiredChecks[$n])
  if ($rc.Count -eq 0) { throw "SR-02: stage $n has no required acceptance checks - empty gate is illegal" }
}
$StageDeps = @{}
$StageArt = @{}
foreach ($s in $StageTable) { $StageDeps[$s.N] = @($s.Deps); $StageArt[$s.N] = @($s.Art) }

# Provenance tags whose change invalidates a stage (and its descendants).
$StageTags = @{
  '00_stage00' = @('RTL','SDC','TOOLS','CONFIG')
  '01_synthesis' = @('RTL','SDC','TOOLS','CONFIG')
  '02_lec_generic' = @('RTL','SDC','TOOLS','CONFIG')
  '03_lec_mapped' = @('RTL','SDC','PDK','DECK','TOOLS','CONFIG')
  '04_floorplan' = @('RTL','SDC','PDK','LIB','PDN','TOOLS','CONFIG')
  '05_placement' = @('RTL','SDC','PDK','LIB','PDN','DECK','TOOLS','CONFIG')
  '06_cts' = @('RTL','SDC','PDK','LIB','PDN','DECK','TOOLS','CONFIG')
  '07_pdn' = @('RTL','SDC','PDK','LIB','PDN','DECK','TOOLS','CONFIG')
  '08_power_connectivity' = @('RTL','SDC','PDK','LIB','PDN','DECK','TOOLS','CONFIG')
  '09_global_route' = @('RTL','SDC','PDK','LIB','PDN','DECK','TOOLS','CONFIG')
  '10_detailed_route' = @('RTL','SDC','PDK','LIB','PDN','DECK','TOOLS','CONFIG')
  '11_extract' = @('RTL','SDC','PDK','LIB','PDN','DECK','TOOLS','CONFIG')
  '12_sta' = @('SDC','PDK','LIB','DECK','TOOLS','CONFIG')
  '13_gds' = @('PDK','LIB','DECK','TOOLS','CONFIG')
  '14_drc' = @('PDK','DECK','TOOLS','CONFIG')
  '15_lvs' = @('PDK','DECK','LIB','TOOLS','CONFIG')
}
$TagField = @{
  RTL='context_rtl'; SDC='context_sdc'; LIB='context_lib'; PDK='context_pdk'
  DECK='context_deck'; PDN='context_pdn'; TOOLS='context_tools'; CONFIG='context_config'
}

# --------------------------------------------------------------------------- helpers
function Write-Json($obj, $path) {
  $json = $obj | ConvertTo-Json -Depth 12
  [System.IO.File]::WriteAllText($path, $json, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-Sha256([string]$p) {
  if (-not (Test-Path $p)) { return $null }
  return (Get-FileHash -Algorithm SHA256 $p).Hash.ToLower()
}

function Get-Sha256Text([string]$text) {
  return [BitConverter]::ToString(
    [Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($text))).Replace('-','').ToLower()
}

# F-26: defence in depth for acceptance provenance.  When a provider emits
# run_id/design (top level or per check) they MUST match this invocation.  When
# it emits neither, freshness is still verified by the caller, which deletes
# every stale acceptance file before invoking the provider.  Mismatches fail
# closed: they downgrade qualification and block all consumers / the verdict.
function Test-AcceptanceBinding($doc) {
  $reasons = @()
  $expected = @{ run_id = $RunId; design = $Design }
  foreach ($field in @('run_id','design')) {
    $prop = $doc.PSObject.Properties[$field]
    if ($null -ne $prop -and $null -ne $prop.Value -and "$($prop.Value)" -ne '') {
      if ("$($prop.Value)" -ne "$($expected[$field])") {
        $reasons += "acceptance $field '$($prop.Value)' does not match this run ('$($expected[$field])')"
      }
    }
  }
  foreach ($c in @($doc.checks)) {
    foreach ($field in @('run_id','design')) {
      $prop = $c.PSObject.Properties[$field]
      if ($null -ne $prop -and $null -ne $prop.Value -and "$($prop.Value)" -ne '' -and
          "$($prop.Value)" -ne "$($expected[$field])") {
        $reasons += "check $($c.name) $field '$($prop.Value)' does not match this run ('$($expected[$field])')"
      }
    }
  }
  return @{ ok = ($reasons.Count -eq 0); reasons = $reasons }
}

function Get-TransitiveDeps([string]$name) {
  # Iterative: a nested function would shadow $out in its own scope.
  $out = @()
  $queue = @($StageDeps[$name])
  while ($queue.Count -gt 0) {
    $d = $queue[0]
    $queue = @($queue | Select-Object -Skip 1)
    if ($out -notcontains $d) {
      $out += $d
      $queue += @($StageDeps[$d])
    }
  }
  return @($out)
}

function Get-Descendants([string]$name) {
  $out = @()
  foreach ($n in $Mandatory) {
    if ((Get-TransitiveDeps $n) -contains $name) { $out += $n }
  }
  return @($out)
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
  # SR-02: three distinct verdicts.  process_completion = did the tool run;
  # engineering_acceptance = did independent checks prove the result;
  # qualification = both of the above plus persistent, verifiable provenance.
  process_completion       = 'INCOMPLETE'
  engineering_acceptance   = 'NOT_TESTED'
  qualification            = 'INCONCLUSIVE'
  evidence_persistence_failures = @()
  gate_schema           = 'sr02-stage-gating-1.0'
  gate_ledger           = $LedgerPath
  gate_policy           = @{ stage_deps = $StageDeps; required_checks = $RequiredChecks }
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
  openroad    = "wsl -d $WslDistro -- /usr/local/bin/openroad -version"
  opensta     = "wsl -d $WslDistro -- env LD_LIBRARY_PATH=$StaLdLibraryPath $StaPath -version"
  klayout     = "wsl -d $WslDistro -- /mnt/d/tools/KLayout/bin/klayout.sh -v"
  python      = "& `"$Python`" --version"
  eqy         = "cmd /c `"call `"$Oss\environment.bat`" >nul 2>&1 && `"$Oss\bin\eqy.exe`" --help`""
  make        = "cmd /c `"where make`""
  wsl_bash    = "wsl -d $WslDistro -- bash --version"
}
if ($SkipToolProbe) {
  foreach ($k in $toolProbe.Keys) { $report.tools[$k] = 'SIMULATED: tool probe skipped' }
} else {
  foreach ($k in $toolProbe.Keys) {
    $v = ''
    try {
      $v = ((Invoke-Expression $toolProbe[$k] 2>&1 | Select-Object -First 4) -join ' | ').Trim()
    } catch { $v = 'ERROR: ' + $_.Exception.Message }
    if (-not $v) { $v = 'no output' }
    $report.tools[$k] = $v
  }
}

# --------------------------------------------------------------------------- PDK revisions
$pdkRoot = if ($env:REG_PDK_COMPLETE_ROOT) { $env:REG_PDK_COMPLETE_ROOT } else { 'D:\tools\pdk\skywater-pdk-complete' }
if ($SkipToolProbe) { $report.pdk_revisions['SIMULATED'] = 'tool probe skipped' }
elseif (Test-Path $pdkRoot) {
  foreach ($d in (Get-ChildItem $pdkRoot -Directory)) {
    $r = (& git -C $d.FullName rev-parse HEAD 2>$null)
    $report.pdk_revisions[$d.Name] = if ($r) { $r.Trim() } else { 'unknown' }
  }
}

# --------------------------------------------------------------------------- SR-02 provenance context
# A context value is captured for each tag.  Resume / partial runs must match
# these values against the persisted gate ledger; any mismatch invalidates the
# stage and all of its descendants.
function ContextHashOf([string[]]$paths) {
  $parts = @()
  foreach ($p in $paths) {
    $h = if ($report.inputs.ContainsKey($p)) { $report.inputs[$p] } else { (Get-Sha256 $p) }
    $parts += "$p=$h"
  }
  return (Get-Sha256Text ($parts -join ';'))
}
$CurrentContext = @{
  RTL   = if ($report.inputs.ContainsKey($Rtl)) { $report.inputs[$Rtl] } else { (Get-Sha256 $Rtl) }
  SDC   = if ($report.inputs.ContainsKey($Sdc)) { $report.inputs[$Sdc] } else { (Get-Sha256 $Sdc) }
  LIB   = ContextHashOf @((Join-Path $LibDir 'sky130_fd_sc_hd__tt_025C_1v80.lib'), (Join-Path $LibDir 'sky130_fd_sc_hd__ss_n40C_1v40.lib'), (Join-Path $LibDir 'sky130_fd_sc_hd__ff_n40C_1v95.lib'), (Join-Path $Platform 'lib\sky130_fd_sc_hd__tt_025C_1v80.lib'))
  PDK   = Get-Sha256Text ((($report.pdk_revisions | ConvertTo-Json -Compress -Depth 6)) + ';' + (ContextHashOf @((Join-Path $Platform 'lef\sky130_fd_sc_hd.tlef'), (Join-Path $Platform 'lef\sky130_fd_sc_hd_merged.lef'), (Join-Path $Platform 'gds\sky130_fd_sc_hd.gds'))))
  DECK  = ContextHashOf @((Join-Path $Platform 'cdl\sky130hd.cdl'), (Join-Path $Platform 'lvs\sky130hd.lylvs'), (Join-Path $Platform 'drc\sky130hd.lydrc'), (Join-Path $Platform 'setRC.tcl'), (Join-Path $Platform 'make_tracks.tcl'), (Join-Path $Platform 'sky130hd.lyt'))
  PDN   = ContextHashOf @($PdnTcl, (Join-Path $Platform 'pdn.tcl'))
  TOOLS = Get-Sha256Text ($report.tools | ConvertTo-Json -Compress -Depth 6)
  CONFIG = Get-Sha256Text ((@($Design,$Platform,$PlatformHS,$LibDir,$Repo,$Oss,$YosysWin,$Python,$PdnTcl) -join '|'))
}
$CurrentScriptHashes = @{}
foreach ($s in $StageTable) {
  $sp = Join-Path $StageDir $s.File
  $CurrentScriptHashes[$s.N] = Get-Sha256Text "$($s.File)|$(if (Test-Path $sp) { Get-Sha256 $sp } else { 'MISSING' })"
}

$LedgerPath = Join-Path $RunDir 'gate_ledger.json'
function Load-Ledger {
  if (Test-Path $LedgerPath) {
    try {
      $obj = Get-Content $LedgerPath -Raw | ConvertFrom-Json
      $h = @{}
      foreach ($p in $obj.stages.PSObject.Properties) { $h[$p.Name] = $p.Value }
      return @{ schema_version = $obj.schema_version; stages = $h }
    } catch { Write-Host "  gate ledger unreadable: $($_.Exception.Message)" }
  }
  return @{ schema_version = 'sr02-stage-gating-1.0'; stages = @{} }
}
function Save-Ledger($ledger) {
  try {
    # Convert nested ordered hashtables/arrays to a plain object graph.
    $stagesObj = [ordered]@{}
    foreach ($k in ($ledger.stages.Keys | Sort-Object)) { $stagesObj[$k] = $ledger.stages[$k] }
    Write-Json ([ordered]@{ schema_version = 'sr02-stage-gating-1.0'; stages = $stagesObj }) $LedgerPath
    return (Test-Path $LedgerPath)
  } catch { Write-Host "  gate ledger write failed: $($_.Exception.Message)"; return $false }
}
$ledger = Load-Ledger

function Get-LedgerEntry([string]$name) {
  if ($ledger.stages.ContainsKey($name)) { return $ledger.stages[$name] }
  return $null
}

# Revalidation: a prior PASS + existing artifacts is NOT sufficient.
function Test-StageResumeValid([string]$name) {
  $reasons = @()
  $entry = Get-LedgerEntry $name
  if ($null -eq $entry) {
    return @{ ok = $false; reasons = @('no prior accepted gate-ledger entry') }
  }
  if ($entry.qualification -ne 'QUALIFIED_PASS') { $reasons += 'prior qualification is not QUALIFIED_PASS' }
  if (-not $entry.evidence_persisted) { $reasons += 'prior evidence/provenance persistence failed' }
  if ($entry.script_hash -ne $CurrentScriptHashes[$name]) { $reasons += 'stage script changed' }
  foreach ($tag in @($StageTags[$name])) {
    $field = $TagField[$tag]
    if ($entry.$field -ne $CurrentContext[$tag]) { $reasons += "provenance changed: $tag" }
  }
  foreach ($a in @($StageArt[$name])) {
    $p = Join-Path $RunDir $a
    $h = Get-Sha256 $p
    $old = $null
    if ($entry.artifacts) { foreach ($ar in $entry.artifacts) { if ($ar.path -eq $a) { $old = $ar.sha256 } } }
    if (-not $h) { $reasons += "artifact missing: $a" }
    elseif ($old -ne $h) { $reasons += "artifact modified: $a" }
  }
  return @{ ok = ($reasons.Count -eq 0); reasons = $reasons }
}

# Compute invalidation once, before any stage is authorised.
$invalidated = @{}
foreach ($name in $Mandatory) {
  $rv = Test-StageResumeValid $name
  if (-not $rv.ok) { $invalidated[$name] = $rv.reasons }
}
# Invalidation propagates to every descendant.
foreach ($name in $Mandatory) {
  if ($invalidated.ContainsKey($name)) {
    foreach ($d in (Get-Descendants $name)) { $invalidated[$d] = @("invalidated by ancestor $name") }
  }
}
if ($Resume -and $invalidated.Count -gt 0) {
  Write-Host "SR-02: invalidated stages (re-execution required): $(($invalidated.Keys | Sort-Object) -join ', ')"
}

function Test-StagePrereqs([string]$name, $doneMap) {
  $reasons = @()
  # F-14: ValidateOnly revalidates with VALIDATION_PASS instead of QUALIFIED_PASS.
  # Accept that verdict ONLY in ValidateOnly mode; normal production runs still
  # require QUALIFIED_PASS, so no production gate is weakened.
  $allowedQual = if ($ValidateOnly) { @('QUALIFIED_PASS','VALIDATION_PASS') } else { @('QUALIFIED_PASS') }
  foreach ($d in (Get-TransitiveDeps $name)) {
    if ($doneMap.ContainsKey($d)) {
      $dr = $doneMap[$d]
      if ($dr.qualification_status -notin $allowedQual -or
          $dr.process_status -notin @('COMPLETED','VALIDATION_ONLY') -or
          -not $dr.evidence_persisted) {
        $reasons += "dependency $d has no fresh authoritative acceptance"
      }
    } else {
      # Partial-stage run: dependency must have valid prior evidence.
      if ($Resume) {
        $rv = Test-StageResumeValid $d
        # F-15: an invalidated dependency (or any of its ancestors) has stale
        # evidence even if its own ledger entry happens to look valid.
        if ($invalidated.ContainsKey($d)) {
          $rv = @{ ok = $false; reasons = @(@($rv.reasons + @($invalidated[$d])) | Select-Object -Unique) }
        }
        if (-not $rv.ok) { $reasons += "dependency $d has no valid prior evidence: $($rv.reasons -join '; ')" }
      } else {
        $reasons += "dependency $d not executed in this run (partial-stage run requires prerequisites)"
      }
    }
  }
  return @{ ok = ($reasons.Count -eq 0); reasons = $reasons }
}

# --------------------------------------------------------------------------- manifest lifecycle (Phase 33)
$ManifestMgr = Join-Path $CheckDir 'manifest_manager.py'
$RunManifest = Join-Path $RunDir 'run_manifest.json'
function Invoke-Manifest([string[]]$margs) {
  if (-not (Test-Path $ManifestMgr)) { return $null }
  try {
    if ($ManifestExecutor) {
      $out = & $ManifestExecutor @margs 2>&1
    } else {
      $out = & $Python $ManifestMgr @margs 2>&1
    }
    return @{ rc = $LASTEXITCODE; out = ($out -join "`n") }
  } catch {
    return @{ rc = 99; out = $_.Exception.Message }
  }
}
# initialize run manifest with identity/tooling
$toolsJson = ($report.tools | ConvertTo-Json -Compress -Depth 6)
$initArgs = @('init', $RunDir, '--run-id', $RunId, '--project-name', $Design, '--project-id', $Design, '--top-module', $Design, '--tool-versions-json', $toolsJson)
if (-not $ValidateOnly) {
  $mi = Invoke-Manifest $initArgs
  if ($mi) { Write-Host "  manifest init rc=$($mi.rc)" }
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
if ($ValidateOnly -and $Stages.Count -gt 0) {
  # Fresh validation of a partial scope still needs its prerequisites validated.
  $expanded = @()
  foreach ($n in $order) {
    if ($expanded -notcontains $n) { $expanded += $n }
    foreach ($d in (Get-TransitiveDeps $n)) { if ($expanded -notcontains $d) { $expanded += $d } }
  }
  $order = @($Mandatory | Where-Object { $expanded -contains $_ })
}

$stageResults = @()
$doneMap = @{}
$persistFailures = @()
$blockedRemaining = $false
$accJson = Join-Path $RunDir 'acceptance.json'
$accTxt = Join-Path $RunDir 'acceptance.txt'
# F-25: a final acceptance.json left by a previous run must never be reused.
# Per-stage acceptance files are deleted inside the loop; do the same for the
# final snapshot up front so any final file present at the end was written by
# THIS invocation.
Remove-Item $accJson -Force -ErrorAction SilentlyContinue
Remove-Item $accTxt -Force -ErrorAction SilentlyContinue
$statusTarget = if ($ValidateOnly) { Join-Path $RunDir 'validation_status.json' } else { $statusFile }
if ($ValidateOnly) { $status = [ordered]@{ stages = @() } }

foreach ($name in $order) {
  $s = $StageTable | Where-Object { $_.N -eq $name }
  $logName = "$name.log"
  $logWin = Join-Path $LogDir $logName
  $logWsl = ConvertTo-WslPath $logWin
  $result = [ordered]@{
    name = $name; description = $s.Desc; kind = $s.Kind; domain = ''
    executed = $false; skipped = $false; exit_code = $null
    started = ''; ended = ''; duration_s = 0
    log = $logWin; artifacts = @($s.Art); checks = @()
    deps = @($s.Deps); required_checks = @($RequiredChecks[$name])
    process_status = 'NOT_RUN'; engineering_status = 'NOT_TESTED'
    qualification_status = 'NOT_QUALIFIED'; evidence_persisted = $false
    status = 'NOT_TESTED'; block_reasons = @(); resume_reasons = @()
  }

  if ($blockedRemaining) {
    $result.block_reasons += 'blocked by an earlier stage without authoritative acceptance'
    $stageResults += $result
    Write-Host ""
    Write-Host "--------- stage $name : NOT RUN (downstream blocked)"
    continue
  }

  # SR-02 step 1: dependencies must have fresh acceptance BEFORE this stage runs.
  $pre = Test-StagePrereqs $name $doneMap
  if (-not $pre.ok) {
    $result.block_reasons += $pre.reasons
    $stageResults += $result
    Write-Host ""
    Write-Host "--------- stage $name : BLOCKED (prerequisites not accepted)"
    foreach ($r in $pre.reasons) { Write-Host "    $r" }
    $blockedRemaining = $true
    continue
  }

  # SR-02 step 2: resume is a revalidation decision, not a prior PASS lookup.
  $skip = $false
  if ($ValidateOnly) {
    $skip = $true
    $result.skipped = $true
    $result.process_status = 'VALIDATION_ONLY'
    Write-Host ""
    Write-Host "--------- stage $name : VALIDATE ONLY (fresh checks, no execution)"
  } elseif ($Resume) {
    $rv = Test-StageResumeValid $name
    # F-15: $invalidated already contains this stage whenever it OR any ancestor
    # failed revalidation.  Consume it here instead of merely printing it, so
    # stale ledger/evidence can never be reused for an invalidated descendant.
    if ($invalidated.ContainsKey($name)) {
      $rv = @{ ok = $false; reasons = @(@($rv.reasons + @($invalidated[$name])) | Select-Object -Unique) }
    }
    if ($rv.ok) {
      $skip = $true
      $result.skipped = $true
      $old = Get-LedgerEntry $name
      $result.exit_code = $old.exit_code
      $result.started = $old.started_utc; $result.ended = $old.finished_utc
      $result.duration_s = $old.duration_s
      $result.process_status = 'COMPLETED'
      Write-Host ""
      Write-Host "--------- stage $name : SKIPPED (resume; inputs/outputs/tools/config/deps revalidated)"
    } else {
      $result.resume_reasons = @($rv.reasons)
      Write-Host ""
      Write-Host "--------- stage $name : $($s.Desc) (resume rejected: $($rv.reasons -join '; '))"
    }
  } else {
    Write-Host ""
    Write-Host "--------- stage $name : $($s.Desc)"
  }

  # SR-02 step 3: process completion - deliberately separate from acceptance.
  if (-not $skip) {
    $t0 = Get-Date
    $rc = 1
    $domain = ''
    try {
      if ($StageExecutor) {
        # TR-04: injected fake tool executable; the SR-02 loop below is unchanged.
        & $StageExecutor $name $s.Kind $logWsl
        $rc = $LASTEXITCODE
        $domain = if ($s.Kind -eq 'win') { 'windows' } else { 'wsl' }
      } else {
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
          & wsl.exe -d $WslDistro -- bash -c $cmd
          $rc = $LASTEXITCODE
        }
        'wsl-or' {
          $domain = 'wsl'
          $scriptWsl = ConvertTo-WslPath (Join-Path $StageDir $s.File)
          $cmd = "source '$envWslWslPath' && /usr/local/bin/openroad -no_init -exit '$scriptWsl' > '$logWsl' 2>&1"
          & wsl.exe -d $WslDistro -- bash -c $cmd
          $rc = $LASTEXITCODE
        }
        default { throw "unknown stage kind '$($s.Kind)'" }
      }
      }
    } catch {
      ($_ | Out-String) | Out-File -FilePath $logWsl -Append
      $rc = 90
    }
    $t1 = Get-Date
    $result.executed = $true
    $result.exit_code = $rc
    $result.domain = $domain
    $result.started = $t0.ToUniversalTime().ToString('o')
    $result.ended = $t1.ToUniversalTime().ToString('o')
    $result.duration_s = [math]::Round(($t1 - $t0).TotalSeconds, 1)
    $result.process_status = if ($rc -eq 0) { 'COMPLETED' } else { 'FAILED' }
    Write-Host ("  exit={0}  elapsed={1:N1}s" -f $rc, ($t1 - $t0).TotalSeconds)
    if (Test-Path $logWin) { Get-Content $logWin -Tail 10 -ErrorAction SilentlyContinue | ForEach-Object { Write-Host "    $_" } }
  }

  # SR-02 step 4: FRESH independent acceptance for this stage, before any
  # consumer stage is authorised.  The stage file is deleted first so a stale
  # acceptance.json can never be mistaken for a fresh validation.
  $stageAccJson = Join-Path $RunDir "acceptance_${name}.json"
  $stageAccTxt = Join-Path $RunDir "acceptance_${name}.txt"
  Remove-Item $stageAccJson -Force -ErrorAction SilentlyContinue
  Remove-Item $stageAccTxt -Force -ErrorAction SilentlyContinue
  if ($AcceptanceExecutor) {
    & $AcceptanceExecutor $name $stageAccJson
    $stageAccRc = $LASTEXITCODE
  } else {
    $accCmd = "source '$envWslWslPath' && python3 '$checkWsl/acceptance.py' '$runWsl' '$runWsl/logs' --out '$runWsl/acceptance_${name}.json' > '$runWsl/acceptance_${name}.txt' 2>&1"
    & wsl.exe -d $WslDistro -- bash -c $accCmd
    $stageAccRc = $LASTEXITCODE
  }
  if (Test-Path $stageAccTxt) { Get-Content $stageAccTxt | ForEach-Object { Write-Host "    $_" } }

  $stageChecks = @()
  $accEvidenceOk = $false
  $accBinding = @{ ok = $true; reasons = @() }
  if (Test-Path $stageAccJson) {
    try {
      $parsed = Get-Content $stageAccJson -Raw | ConvertFrom-Json
      $stageChecks = @($parsed.checks)
      $accEvidenceOk = ($stageChecks.Count -gt 0)
      if ($accEvidenceOk) { $accBinding = Test-AcceptanceBinding $parsed }
    } catch { $accEvidenceOk = $false; $accBinding = @{ ok = $false; reasons = @('acceptance document unreadable') } }
  }
  if (-not $accEvidenceOk) {
    $persistFailures += "stage ${name}: fresh acceptance evidence not persisted/readable (rc=$stageAccRc)"
  }

  # Engineering acceptance is computed from the required check statuses only;
  # a zero tool exit code is never sufficient.
  $byName = @{}
  foreach ($c in $stageChecks) { if ($c.stage -eq $name) { $byName[$c.name] = $c } }
  $fails = @(); $incon = @()
  foreach ($rn in @($RequiredChecks[$name])) {
    if (-not $byName.ContainsKey($rn)) { $incon += "missing:$rn" }
    elseif ($byName[$rn].status -eq 'FAIL') { $fails += $rn }
    elseif ($byName[$rn].status -ne 'PASS') { $incon += "${rn}:$($byName[$rn].status)" }
  }
  if ($fails.Count -gt 0) { $eng = 'FAIL' }
  elseif ($incon.Count -gt 0 -or -not $accEvidenceOk) { $eng = 'INCONCLUSIVE' }
  else { $eng = 'PASS' }
  if ($result.process_status -eq 'FAILED') { $eng = 'FAIL' }
  elseif ($result.process_status -eq 'NOT_RUN') { $eng = 'INCONCLUSIVE' }
  $result.engineering_status = $eng
  $result.status = $eng
  $result.checks = @(@($RequiredChecks[$name]) | ForEach-Object {
    if ($byName.ContainsKey($_)) { $byName[$_] }
    else { [ordered]@{ name = $_; stage = $name; status = 'NOT_TESTED'; detail = 'required check not emitted' } }
  })

  # SR-02 step 5: persist the stage result and its provenance BEFORE authorising
  # consumers.  Any persistence failure downgrades acceptance to INCONCLUSIVE.
  $status.stages = @(($stageResults + @($result)) | ForEach-Object {
    [ordered]@{ name=$_.name; executed=$_.executed; skipped=$_.skipped; exit_code=$_.exit_code
                started=$_.started; ended=$_.ended; duration_s=$_.duration_s
                process_status=$_.process_status; engineering_status=$_.engineering_status
                status=$_.status }
  })
  $evOk = $true
  try { Write-Json $status $statusTarget; if (-not (Test-Path $statusTarget)) { $evOk = $false } }
  catch { $evOk = $false }
  if (-not $accEvidenceOk) { $evOk = $false }
  if (-not $accBinding.ok) {
    # F-26: a cross-run/cross-design acceptance document is not authoritative.
    $evOk = $false
    $persistFailures += "stage ${name}: acceptance run/design binding failure: $($accBinding.reasons -join '; ')"
  }

  if (-not $ValidateOnly -and (Test-Path $ManifestMgr)) {
    try {
      $outArts = @()
      foreach ($a in @($s.Art)) {
        $ap = Join-Path $RunDir $a
        $exists = Test-Path $ap
        $outArts += [ordered]@{
          path = $ap; artifact_path = $ap; required = $true; exists = $exists
          sha256 = if ($exists) { (Get-Sha256 $ap) } else { $null }
          size = if ($exists) { (Get-Item $ap).Length } else { 0 }
        }
      }
      $logArts = @()
      if (Test-Path $logWin) {
        $logArts += [ordered]@{ path = $logWin; required = $false; exists = $true; sha256 = (Get-Sha256 $logWin); size = (Get-Item $logWin).Length }
      }
      $stageStatus = if ($eng -eq 'PASS' -and $result.process_status -eq 'COMPLETED') { 'PASS' } else { 'FAIL' }
      $srRec = [ordered]@{
        stage_id = $name; stage = $name; run_id = $RunId
        stage_status = $stageStatus; status = $stageStatus
        stage_exit_code = $result.exit_code; exit_code = $result.exit_code
        start_timestamp = $result.started; started_utc = $result.started
        end_timestamp = $result.ended; finished_utc = $result.ended
        tool_versions = $report.tools
        input_artifacts = @(); output_artifacts = $outArts; logs = $logArts
        evidence_class = if ($skip) { 'REVALIDATED_RESUME' } else { 'FRESH_EXECUTION' }
        engineering_acceptance = $eng; qualification_status = 'PENDING'
      }
      $srTmp = Join-Path $RunDir "._manifest_${name}.json"
      Write-Json $srRec $srTmp
      $mu = Invoke-Manifest @('upsert-stage', $RunDir, '--stage-json-file', $srTmp)
      Remove-Item $srTmp -Force -ErrorAction SilentlyContinue
      if ($null -eq $mu -or $mu.rc -ne 0) { $evOk = $false }
      Write-Host "  manifest update rc=$($mu.rc)"
    } catch {
      $evOk = $false
      Write-Host "  manifest update error: $($_.Exception.Message)"
    }
  }

  $entry = [ordered]@{
    name = $name; qualification = 'PENDING'; process_status = $result.process_status
    engineering_status = $eng; evidence_persisted = $false
    exit_code = $result.exit_code; started_utc = $result.started; finished_utc = $result.ended
    duration_s = $result.duration_s; script_hash = $CurrentScriptHashes[$name]
    context_rtl = $CurrentContext.RTL; context_sdc = $CurrentContext.SDC
    context_lib = $CurrentContext.LIB; context_pdk = $CurrentContext.PDK
    context_deck = $CurrentContext.DECK; context_pdn = $CurrentContext.PDN
    context_tools = $CurrentContext.TOOLS; context_config = $CurrentContext.CONFIG
    artifacts = @(); required_checks = @($RequiredChecks[$name])
    checks = @($result.checks | ForEach-Object { [ordered]@{ name=$_.name; status=$_.status } })
  }
  foreach ($a in @($s.Art)) {
    $p = Join-Path $RunDir $a
    $entry.artifacts += [ordered]@{ path = $a; sha256 = (Get-Sha256 $p) }
  }
  if (-not $ValidateOnly) {
    $ledger.stages[$name] = $entry
    if (-not (Save-Ledger $ledger)) { $evOk = $false }
  }

  if (-not $evOk) {
    $persistFailures += "stage ${name}: evidence/provenance persistence failure"
    if ($eng -eq 'PASS') { $eng = 'INCONCLUSIVE' }
    $result.engineering_status = $eng; $result.status = $eng
    $entry.engineering_status = $eng
  }
  $result.evidence_persisted = $evOk
  $entry.evidence_persisted = $evOk

  if ($ValidateOnly) {
    $q = if ($eng -eq 'PASS' -and $evOk) { 'VALIDATION_PASS' } else { 'NOT_QUALIFIED' }
  } elseif ($eng -eq 'PASS' -and $result.process_status -eq 'COMPLETED' -and $evOk) {
    $q = 'QUALIFIED_PASS'
  } else {
    $q = 'NOT_QUALIFIED'
  }
  $result.qualification_status = $q
  $entry.qualification = $q
  if (-not $ValidateOnly) {
    $ledger.stages[$name] = $entry
    if (-not (Save-Ledger $ledger)) {
      $evOk = $false; $result.evidence_persisted = $false
      $result.qualification_status = 'NOT_QUALIFIED'
      $persistFailures += "stage ${name}: gate ledger write failure"
    }
  }
  $stageResults += $result
  $doneMap[$name] = $result
  Write-Host "  process=$($result.process_status)  engineering=$eng  qualification=$($result.qualification_status)  evidence_persisted=$($result.evidence_persisted)"

  # SR-02 step 6: only an accepted stage authorises its consumers.
  if ($result.qualification_status -notin @('QUALIFIED_PASS','VALIDATION_PASS')) {
    Write-Host "  stage $name NOT accepted - all dependent stages are blocked"
    $blockedRemaining = $true
  }
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
$extraFiles = @('report.json','acceptance.json','stage_status.json','validation_status.json','gate_ledger.json','env_wsl.env','env_win.env')
foreach ($n in $order) { $extraFiles += "acceptance_${n}.json" }
foreach ($extra in $extraFiles) {
  $p = Join-Path $RunDir $extra
  $manifest[$extra] = [ordered]@{ path = $p; exists = (Test-Path $p); sha256 = (Get-Sha256 $p); bytes = $(if (Test-Path $p) { (Get-Item $p).Length } else { 0 }) }
}
$report.artifacts = $manifest

# --------------------------------------------------------------------------- final fresh full acceptance
# A fresh global acceptance run is the authoritative evidence snapshot.  It is
# produced even in ValidateOnly mode (the previous code skipped it entirely).
# F-25/F-26: delete any final file written earlier in this run as well, so the
# snapshot parsed below is provably the output of the final invocation.
Remove-Item $accJson -Force -ErrorAction SilentlyContinue
Remove-Item $accTxt -Force -ErrorAction SilentlyContinue
if ($AcceptanceExecutor) {
  & $AcceptanceExecutor '__final__' $accJson
  $fullAccRc = $LASTEXITCODE
} else {
  $accCmdFull = "source '$envWslWslPath' && python3 '$checkWsl/acceptance.py' '$runWsl' '$runWsl/logs' > '$runWsl/acceptance.txt' 2>&1"
  & wsl.exe -d $WslDistro -- bash -c $accCmdFull
  $fullAccRc = $LASTEXITCODE
}

# The final document is authoritative: parse it, not just its exit code.
$finalChecks = @()
$finalAccOk = $false
$finalBinding = @{ ok = $true; reasons = @() }
$finalFailMsgs = @()
if (Test-Path $accJson) {
  try {
    $finalParsed = Get-Content $accJson -Raw | ConvertFrom-Json
    $finalChecks = @($finalParsed.checks)
    $finalAccOk = ($finalChecks.Count -gt 0)
    if ($finalAccOk) { $finalBinding = Test-AcceptanceBinding $finalParsed }
  } catch { $finalAccOk = $false }
}
if (-not $finalAccOk) {
  $persistFailures += "final acceptance.json not persisted/readable/empty (rc=$fullAccRc)"
} else {
  if (-not $finalBinding.ok) {
    $persistFailures += "final acceptance run/design binding failure: $($finalBinding.reasons -join '; ')"
  }
  # Any FAIL / INCONCLUSIVE / NOT_TESTED check anywhere in the final snapshot
  # blocks PASS, including checks outside the mandatory per-stage set.
  foreach ($c in $finalChecks) {
    if ($c.status -in @('FAIL','INCONCLUSIVE','NOT_TESTED')) {
      $finalFailMsgs += "final acceptance: $($c.stage)/$($c.name) = $($c.status)"
    }
  }
  # A mandatory check that is absent from the final snapshot is unverifiable.
  foreach ($n in $Mandatory) {
    $finalNames = @($finalChecks | Where-Object { $_.stage -eq $n } | ForEach-Object { $_.name })
    foreach ($rn in @($RequiredChecks[$n])) {
      if ($finalNames -notcontains $rn) { $finalFailMsgs += "final acceptance: missing required check $n/$rn" }
    }
  }
}

# --------------------------------------------------------------------------- manifest finalize (status must be truthful)
$allQualified = ($stageResults.Count -eq $Mandatory.Count) -and
                @($stageResults | Where-Object { $_.qualification_status -ne 'QUALIFIED_PASS' }).Count -eq 0
if (-not $ValidateOnly -and (Test-Path $ManifestMgr)) {
  $finalStatus = if ($allQualified) { 'PASS' } else { 'PARTIAL' }
  $mf = Invoke-Manifest @('finalize', $RunDir, '--run-status', $finalStatus)
  if ($mf) {
    Write-Host "  manifest finalize rc=$($mf.rc)"
    if ($mf.rc -ne 0) { $persistFailures += 'manifest finalize returned non-zero' }
  } else { $persistFailures += 'manifest finalize did not run' }
}

# --------------------------------------------------------------------------- overall verdict (three distinct fields)
$qualified = @($stageResults | Where-Object { $_.qualification_status -in @('QUALIFIED_PASS','VALIDATION_PASS') } | ForEach-Object { $_.name })
$missing = @($Mandatory | Where-Object { $qualified -notcontains $_ })
$notPass = @($stageResults | Where-Object { $_.engineering_status -ne 'PASS' })
$anyFail = @($stageResults | Where-Object { $_.engineering_status -eq 'FAIL' })
$report.not_executed = $missing
$report.evidence_persistence_failures = $persistFailures
$report.final_acceptance_failures = $finalFailMsgs

$failMsgs = @()
foreach ($sr in $stageResults) {
  foreach ($c in @($sr.checks)) {
    if ($c.status -ne 'PASS') { $failMsgs += "$($sr.name)/$($c.name): $($c.status) - $($c.detail)" }
  }
  if ($sr.exit_code -ne 0) { $failMsgs += "$($sr.name): stage exit code $($sr.exit_code)" }
  foreach ($r in @($sr.block_reasons)) { $failMsgs += "$($sr.name): BLOCKED - $r" }
}
foreach ($p in $persistFailures) { $failMsgs += "evidence persistence: $p" }
foreach ($m in $finalFailMsgs) { $failMsgs += $m }
$report.failure_messages = $failMsgs

# F-25: per-check statuses from the fresh final acceptance are part of the
# verdict contract - a FAIL/INCONCLUSIVE/NOT_TESTED or missing check blocks PASS
# even when the provider exits zero.
$clean = ($missing.Count -eq 0 -and $notPass.Count -eq 0 -and $persistFailures.Count -eq 0 -and
          $fullAccRc -eq 0 -and $finalFailMsgs.Count -eq 0)
if ($ValidateOnly) {
  $report.process_completion = 'NOT_RUN_VALIDATION_ONLY'
} elseif ($missing.Count -gt 0) {
  $report.process_completion = 'INCOMPLETE'
} else {
  $report.process_completion = 'COMPLETE'
}
if ($anyFail.Count -gt 0) { $report.engineering_acceptance = 'FAIL' }
elseif ($notPass.Count -gt 0) { $report.engineering_acceptance = 'INCONCLUSIVE' }
else { $report.engineering_acceptance = 'PASS' }

if ($clean) {
  $report.qualification = if ($ValidateOnly) { 'VALIDATION_PASS' } else { 'QUALIFIED_PASS' }
  $report.overall = if ($ValidateOnly) { 'VALIDATION_PASS' } else { 'PASS' }
} else {
  $report.qualification = 'NOT_QUALIFIED'
  $report.overall = if ($missing.Count -gt 0) { 'INCONCLUSIVE' } else { 'FAIL' }
}
$report.finished_utc = (Get-Date).ToUniversalTime().ToString('o')

# persist the final per-stage status
$status.stages = @($stageResults | ForEach-Object {
  [ordered]@{ name=$_.name; executed=$_.executed; skipped=$_.skipped; exit_code=$_.exit_code
              started=$_.started; ended=$_.ended; duration_s=$_.duration_s
              process_status=$_.process_status; engineering_status=$_.engineering_status
              qualification_status=$_.qualification_status; evidence_persisted=$_.evidence_persisted
              status=$_.status }
})
Write-Json $status $statusTarget

Write-Json $report (Join-Path $RunDir 'report.json')
Copy-Item (Join-Path $RunDir 'report.json') (Join-Path $EvidenceDir "report_$RunId.json") -Force
if (Test-Path $accTxt) { Copy-Item $accTxt (Join-Path $EvidenceDir "acceptance_$RunId.txt") -Force }
Copy-Item (Join-Path $RunDir 'acceptance.json') (Join-Path $EvidenceDir "acceptance_$RunId.json") -Force -ErrorAction SilentlyContinue
Copy-Item $statusTarget (Join-Path $EvidenceDir "stage_status_$RunId.json") -Force -ErrorAction SilentlyContinue
Copy-Item $LedgerPath (Join-Path $EvidenceDir "gate_ledger_$RunId.json") -Force -ErrorAction SilentlyContinue

# --------------------------------------------------------------------------- markdown summary
$md = @()
$md += "# Phase 14 regression run $RunId"
$md += ''
$md += "- overall: **$($report.overall)**"
$md += "- process completion: **$($report.process_completion)**"
$md += "- engineering acceptance: **$($report.engineering_acceptance)**"
$md += "- qualification: **$($report.qualification)**"
$md += "- run dir: ``$RunDir``"
$md += "- git: ``$($report.git_branch)`` @ ``$($report.git_head)``"
$md += "- started: $($report.timestamp_utc)"
$md += "- finished: $($report.finished_utc)"
$md += ''
$md += '| # | Stage | Process | Engineering | Qualification | Exit | Checks | Duration |'
$md += '|---:|---|---|---|---|---:|---|---:|'
$i = 0
foreach ($sr in $stageResults) {
  $i++
  $ck = @($sr.checks)
  $md += "| $i | $($sr.name) | $($sr.process_status) | $($sr.engineering_status) | $($sr.qualification_status) | $($sr.exit_code) | $($ck.Count) checks, $(@($ck | Where-Object { $_.status -ne 'PASS' }).Count) not passing | $($sr.duration_s)s |"
}
$md += ''
$md += '## Gate decisions (SR-02)'
$gateAny = $false
foreach ($sr in $stageResults) {
  foreach ($r in @($sr.block_reasons)) { $gateAny = $true; $md += "- ``$($sr.name)`` BLOCKED: $r" }
  foreach ($r in @($sr.resume_reasons)) { $gateAny = $true; $md += "- ``$($sr.name)`` resume revalidation rejected: $r" }
}
foreach ($p in $persistFailures) { $gateAny = $true; $md += "- evidence persistence: $p" }
if (-not $gateAny) { $md += '- none' }
$md += ''
$md += '## Checks not passing'
$anyFail = $false
foreach ($sr in $stageResults) {
  foreach ($c in @($sr.checks)) {
    if ($c.status -ne 'PASS') { $anyFail = $true; $md += "- ``$($sr.name)`` / $($c.name): **$($c.status)** - $($c.detail)" }
  }
}
foreach ($m in $failMsgs) { if ($m -notmatch '/') { $anyFail = $true; $md += "- $m" } }
foreach ($m in $finalFailMsgs) { $anyFail = $true; $md += "- $m" }
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
Write-Host " process=$($report.process_completion)  engineering=$($report.engineering_acceptance)  qualification=$($report.qualification)"
Write-Host " report : $(Join-Path $RunDir 'report.json')"
Write-Host " summary: $(Join-Path $RunDir 'summary.md')"
Write-Host "==========================================================="
if ($report.overall -notin @('PASS','VALIDATION_PASS')) { exit 1 }
exit 0
