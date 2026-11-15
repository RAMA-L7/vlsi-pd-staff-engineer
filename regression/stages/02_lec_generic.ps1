# ============================================================================
# Phase 18G regression - STAGE 02: GENERIC-NETLIST LEC
#
# Environment: REG_RUN, REG_RTL, REG_YOSYS, REG_OSS, REG_DESIGN
# ============================================================================
$ErrorActionPreference = 'Continue'

$Run   = $env:REG_RUN
$Rtl   = $env:REG_RTL
$Yosys = $env:REG_YOSYS
$Oss   = $env:REG_OSS
$Des   = $env:REG_DESIGN
if (-not $Des) { $Des = 'test_counter' }
$Top = $Des

foreach ($v in @('REG_RUN','REG_RTL','REG_YOSYS')) {
  if (-not (Get-Item "Env:$v" -ErrorAction SilentlyContinue)) { Write-Host "ERROR: $v not set"; exit 20 }
}

$Out = Join-Path $Run 'lec_generic'
New-Item -ItemType Directory -Force -Path $Out | Out-Null
$Synth = Join-Path $Run "synth\generic_${Top}_synth.v"
if (-not (Test-Path $Synth)) { Write-Host "ERROR: missing $Synth"; exit 20 }

Write-Host "=== stage 02 generic LEC ==="
Write-Host "  RTL    : $Rtl"
Write-Host "  netlist: $Synth"

function New-LecScript([string]$Netlist, [string]$OutYs) {
$content = @"
read_verilog $Rtl
prep -top $Top
async2sync
design -save reference

design -reset
read_verilog $Netlist
prep -top $Top
async2sync
design -save revised

design -load reference
design -copy-from revised -as ${Top}_net $Top

equiv_make $Top ${Top}_net lec_equiv
hierarchy -top lec_equiv
equiv_simple
equiv_induct
equiv_status -assert
"@
[System.IO.File]::WriteAllText($OutYs, $content, (New-Object System.Text.UTF8Encoding($false)))
}

# ---- positive ---------------------------------------------------------------
$posYs = Join-Path $Out 'lec_generic.ys'
New-LecScript $Synth $posYs
$posLog = Join-Path $Out 'lec_generic.log'
cmd /c "call `"$Oss\environment.bat`" >nul 2>&1 && `"$Yosys`" -s `"$posYs`" > `"$posLog`" 2>&1"
$rc = $LASTEXITCODE
Write-Host "  positive exit=$rc"
Select-String -Path $posLog -Pattern 'Equivalence successfully proven|are proven and|unproven|ERROR' |
  Select-Object -Last 6 | ForEach-Object { Write-Host "    $($_.Line)" }
if ($rc -ne 0) { Write-Host "ERROR: generic LEC failed"; exit 21 }

# ---- negative control -------------------------------------------------------
$Broken = Join-Path $Out "generic_${Top}_synth_broken.v"
$text = Get-Content $Synth -Raw
$orig = $null
$bad = $null

# Preferred validated corruption for test_counter
$tc_orig = 'assign _14_[0] = ~count[0];'
$tc_bad  = 'assign _14_[0] = count[0];'
if ($text.Contains($tc_orig)) {
  $orig = $tc_orig; $bad = $tc_bad
} else {
  # Generic fallback: flip first inverter assignment "assign X = ~Y;" -> "assign X = Y;"
  $m = [regex]::Match($text, 'assign\s+([^;=]+?)\s*=\s*~([^;]+?);')
  if ($m.Success) {
    $orig = $m.Value
    $bad = "assign $($m.Groups[1].Value) = $($m.Groups[2].Value);"
  }
}

if (-not $orig) {
  Write-Host "ERROR: could not locate a corruption point in the generic netlist for negative control"
  exit 23
}

$text2 = $text.Replace($orig, $bad)
if ($text2 -eq $text) {
  Write-Host "ERROR: failed to apply corruption for negative control"
  exit 23
}
[System.IO.File]::WriteAllText($Broken, $text2, (New-Object System.Text.UTF8Encoding($false)))
Write-Host "  negative control: '$orig' -> '$bad'"

$negYs = Join-Path $Out 'lec_generic_negative.ys'
New-LecScript $Broken $negYs
$negLog = Join-Path $Out 'lec_generic_negative.log'
cmd /c "call `"$Oss\environment.bat`" >nul 2>&1 && `"$Yosys`" -s `"$negYs`" > `"$negLog`" 2>&1"
$rc = $LASTEXITCODE
Write-Host "  negative exit=$rc (must be non-zero)"
Select-String -Path $negLog -Pattern 'unproven|Equivalence successfully proven|ERROR' |
  Select-Object -Last 5 | ForEach-Object { Write-Host "    $($_.Line)" }
if ($rc -eq 0) {
  Write-Host "ERROR: negative control was NOT detected - the harness is insensitive"
  exit 22
}

Write-Host "STAGE02_DONE"
exit 0
