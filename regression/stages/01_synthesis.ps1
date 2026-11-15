# ============================================================================
# Phase 14 regression - STAGE 01: SYNTHESIS
#
# Exact Phase 12/13 validated procedure, executed with the Windows-native
# Yosys 0.69 from the OSS CAD Suite.  This is the same tool that produced the
# Phase 13 validated netlist, so the regression reproduces the validated design
# exactly.  The design name is supplied by the orchestrator via
# REG_DESIGN (e.g. 'test_counter' or 'soc_ctrl').
#
# Environment (set by the orchestrator):
#   REG_RUN, REG_RTL, REG_LIB_TT, REG_DESIGN, REG_YOSYS, REG_OSS
# ============================================================================
$ErrorActionPreference = 'Continue'

$Run  = $env:REG_RUN
$Rtl  = $env:REG_RTL
$Lib  = $env:REG_LIB_TT
$Yosys = $env:REG_YOSYS
$Des  = $env:REG_DESIGN
if (-not $Des) { $Des = 'test_counter' }
$Top = $Des
$Oss  = $env:REG_OSS

foreach ($v in @('REG_RUN','REG_RTL','REG_LIB_TT','REG_YOSYS')) {
  if (-not (Get-Item "Env:$v" -ErrorAction SilentlyContinue)) { Write-Host "ERROR: $v not set"; exit 10 }
}

$Out = Join-Path $Run 'synth'
New-Item -ItemType Directory -Force -Path $Out | Out-Null

Write-Host "=== stage 01 synthesis ==="
Write-Host "  RTL  : $Rtl"
Write-Host "  LIB  : $Lib"
Write-Host "  DESIGN: $Top"

# --- generic synthesis ------------------------------------------------------
$genericYs = Join-Path $Out 'generic_synth.ys'
$genericNetlist = Join-Path $Out "generic_${Top}_synth.v"
$genericContent = @"
read_verilog $Rtl
synth -top $Top
stat
write_verilog -noattr $genericNetlist
"@
[System.IO.File]::WriteAllText($genericYs, $genericContent, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "--- generic synthesis ---"
$genericLog = Join-Path $Out 'generic_synth.log'
cmd /c "call `"$Oss\environment.bat`" >nul 2>&1 && `"$Yosys`" -s `"$genericYs`" > `"$genericLog`" 2>&1"
$rc = $LASTEXITCODE
Write-Host "  yosys exit=$rc"
if ($rc -ne 0) { Write-Host "ERROR: generic synthesis failed"; exit 10 }

# --- Sky130 technology mapping ----------------------------------------------
$mappedYs = Join-Path $Out 'synth_mapped.ys'
$mappedNetlist = Join-Path $Out "${Top}_synth.v"
$mappedContent = @"
read_verilog $Rtl
hierarchy -check -top $Top

synth -top $Top -flatten

dfflibmap -liberty $Lib
abc -liberty $Lib

setundef -zero
opt_clean -purge
splitnets -ports
opt_clean -purge

stat

write_verilog -noattr -noexpr $mappedNetlist
"@
[System.IO.File]::WriteAllText($mappedYs, $mappedContent, (New-Object System.Text.UTF8Encoding($false)))

Write-Host "--- Sky130 technology mapping ---"
$mappedLog = Join-Path $Out 'synth_mapped.log'
cmd /c "call `"$Oss\environment.bat`" >nul 2>&1 && `"$Yosys`" -s `"$mappedYs`" > `"$mappedLog`" 2>&1"
$rc = $LASTEXITCODE
Write-Host "  yosys exit=$rc"
if ($rc -ne 0) { Write-Host "ERROR: technology mapping failed"; exit 11 }

# --- extract the final cell statistics from the Yosys stat blocks -----------
$summaryKeywords = @('wires','wire bits','public wires','public wire bits',
                      'ports','port bits','cells','processes','memories',
                      'memory bits','netlist inputs','netlist outputs')

function Get-LastStatBlock([string]$log) {
  $lines = Get-Content $log
  $starts = @()
  for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match '^=== \w+ ===\s*$') { $starts += $i }
  }
  if ($starts.Count -eq 0) { return @() }
  return $lines[$starts[-1]..($lines.Count - 1)]
}

function Get-CellCounts([string]$log) {
  $counts = [ordered]@{}
  foreach ($l in (Get-LastStatBlock $log)) {
    if ($l -match '^\s+(\d+)\s+([A-Za-z_$][A-Za-z0-9_$]*)\s*$') {
      $n = $Matches[2]
      if ($summaryKeywords -notcontains $n) { $counts[$n] = [int]$Matches[1] }
    } elseif ($l -match '^\s+([A-Za-z_$][A-Za-z0-9_$]*)\s+(\d+)\s*$') {
      $n = $Matches[1]
      if ($summaryKeywords -notcontains $n) { $counts[$n] = [int]$Matches[2] }
    }
  }
  return $counts
}

$mappedCells = Get-CellCounts $mappedLog
$genericCells = Get-CellCounts $genericLog

$mappedInst = @($mappedCells.Keys | Where-Object { $_ -like 'sky130_fd_sc_hd__*' } | Sort-Object)
$mapTotal = 0
foreach ($c in $mappedInst) { $mapTotal += $mappedCells[$c] }

Write-Host "--- cell statistics (mapped, final stat block) ---"
foreach ($c in $mappedInst) { Write-Host ("  {0,-42} {1}" -f $c, $mappedCells[$c]) }
Write-Host "  Sky130 cell types: $($mappedInst.Count), total instances: $mapTotal"
Write-Host "--- cell statistics (generic, final stat block) ---"
foreach ($c in ($genericCells.Keys | Sort-Object)) { Write-Host ("  {0,-42} {1}" -f $c, $genericCells[$c]) }

# --- machine-readable cell-count record -------------------------------------
$rec = [ordered]@{
  tool         = 'yosys'
  tool_version = ((cmd /c "call `"$Oss\environment.bat`" >nul 2>&1 && `"$Yosys`" -V" 2>&1) -join ' ').Trim()
  rtl          = $Rtl
  rtl_sha256   = (Get-FileHash $Rtl -Algorithm SHA256).Hash.ToLower()
  liberty      = $Lib
  liberty_sha256 = (Get-FileHash $Lib -Algorithm SHA256).Hash.ToLower()
  generic_netlist_sha256 = (Get-FileHash $genericNetlist -Algorithm SHA256).Hash.ToLower()
  mapped_netlist_sha256  = (Get-FileHash $mappedNetlist -Algorithm SHA256).Hash.ToLower()
  mapped_cells = [ordered]@{}
  generic_cells = [ordered]@{}
}
foreach ($c in $mappedInst) { $rec.mapped_cells[$c] = $mappedCells[$c] }
foreach ($c in ($genericCells.Keys | Sort-Object)) { $rec.generic_cells[$c] = $genericCells[$c] }
$json = $rec | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText((Join-Path $Out 'cell_counts.json'), $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "  cell counts: $(Join-Path $Out 'cell_counts.json')"

Write-Host "STAGE01_DONE"
exit 0