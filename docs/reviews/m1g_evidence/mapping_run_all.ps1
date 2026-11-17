$ErrorActionPreference = 'Stop'

$Lib     = 'D:/tools/src/OpenROAD/src/sta/test/sky130hd/sky130_fd_sc_hd__tt_025C_1v80.lib'
$LibWin  = 'D:\tools\src\OpenROAD\src\sta\test\sky130hd\sky130_fd_sc_hd__tt_025C_1v80.lib'
$RTL     = 'D:/audit/m1g_mapping_20261117/examples/soc_ctrl/rtl/soc_ctrl.v'
$EvRoot  = 'D:\audit\m1g_evidence_20261117\mapping'
$Runs    = Join-Path $EvRoot 'runs'
$DepthPs = Join-Path $EvRoot 'depth.ps1'

# ABC timing-constraint file (does NOT touch the design SDC)
$constrDir = Join-Path $EvRoot 'constr'
New-Item -ItemType Directory -Force -Path $constrDir | Out-Null
$constr = Join-Path $constrDir 'abc_timing.constr'
Set-Content -Path $constr -Value @(
  'set_driving_cell sky130_fd_sc_hd__buf_1',
  'set_load 5'
)
$constrFwd = $constr -replace '\\','/'

$experiments = @(
  [pscustomobject]@{ Name='baseline';                    Abc="-liberty $Lib" },
  [pscustomobject]@{ Name='dontuse_maj3_star';           Abc="-liberty $Lib -dont_use sky130_fd_sc_hd__maj3_*" },
  [pscustomobject]@{ Name='dontuse_maj3_1';              Abc="-liberty $Lib -dont_use sky130_fd_sc_hd__maj3_1" },
  [pscustomobject]@{ Name='dontuse_maj3_all3';           Abc="-liberty $Lib -dont_use sky130_fd_sc_hd__maj3_1 -dont_use sky130_fd_sc_hd__maj3_2 -dont_use sky130_fd_sc_hd__maj3_4" },
  [pscustomobject]@{ Name='script_strash_refactor_rw_map'; Abc="-liberty $Lib -script `"+strash; refactor; rewrite; map`"" },
  [pscustomobject]@{ Name='script_fraig_rewriteZ_mapB';  Abc="-liberty $Lib -script `"+strash; fraig; rewrite -z; map -B 0.05`"" },
  [pscustomobject]@{ Name='script_map_areaA10';          Abc="-liberty $Lib -script `"+strash; refactor; rewrite; map -A 10`"" },
  [pscustomobject]@{ Name='constr_timing';               Abc="-liberty $Lib -constr $constrFwd" },
  [pscustomobject]@{ Name='delay_D50';                   Abc="-liberty $Lib -D 50" },
  [pscustomobject]@{ Name='delay_D500';                  Abc="-liberty $Lib -D 500" }
)

function Get-CellStr($logtext) {
  $cells = @{}
  foreach ($ln in ($logtext -split "`r?`n")) {
    if ($ln -match '^\s*(\d+)\s+[\d.]+\s+(sky130_fd_sc_hd__\S+)') {
      $cells[$Matches[2]] = [int]$Matches[1]
    }
  }
  if ($cells.Count -eq 0) { return 'NA' }
  $keys = $cells.Keys | Sort-Object
  return (($keys | ForEach-Object { "$_=$($cells[$_])" }) -join ';')
}

$results = @()
foreach ($e in $experiments) {
  $run = Join-Path $Runs $e.Name
  New-Item -ItemType Directory -Force -Path $run | Out-Null
  $runFwd = $run -replace '\\','/'
  $ys = @"
read_liberty -ignore_miss_func $Lib
read_verilog $RTL
synth -top soc_ctrl
dfflibmap -liberty $Lib
abc $($e.Abc)
opt_clean -purge
write_verilog -noattr -noexpr $runFwd/mapped.v
write_json $runFwd/mapped.json
stat -liberty $Lib
stat
"@
  Set-Content -Path (Join-Path $run 'run.ys') -Value $ys

  $log = Join-Path $run 'yosys.log'
  cmd /c "call D:\tools\oss-cad-suite\environment.bat >nul 2>&1 && yosys -s `"$run\run.ys`"" *> $log
  $exit = $LASTEXITCODE
  Set-Content -Path (Join-Path $run 'exit.txt') -Value "$exit"

  $sha = 'NA'; $area = 'NA'; $cellstr = 'NA'; $depth = 'NA'
  if ((Test-Path (Join-Path $run 'mapped.v')) -and $exit -eq 0) {
    $sha = (Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $run 'mapped.v')).Hash.ToLower()
    $text = Get-Content -Raw -LiteralPath $log
    if ($text -match "Chip area for module '\\soc_ctrl': ([\d.]+)") { $area = $Matches[1] }
    $cellstr = Get-CellStr $text
    $depth = (powershell -NoProfile -File $DepthPs (Join-Path $run 'mapped.json') $LibWin | Out-String).Trim() -replace "`r?`n",' | '
  }
  $results += [pscustomobject]@{
    Name=$e.Name; Exit=$exit; SHA=$sha; Area=$area; Cells=$cellstr; Depth=$depth; AbcArgs=$e.Abc
  }
  Write-Output ("DONE {0}: exit={1} sha={2} area={3}" -f $e.Name,$exit,$sha.Substring(0,[Math]::Min(12,$sha.Length)),$area)
}

$results | Export-Csv -NoTypeInformation -Encoding utf8 -Path (Join-Path $EvRoot 'results.csv')
$results | ConvertTo-Json -Depth 4 | Out-File -Encoding utf8 (Join-Path $EvRoot 'results.json')
Write-Output '=== SUMMARY TABLE ==='
$results | Format-Table -AutoSize -Wrap | Out-String -Width 240
