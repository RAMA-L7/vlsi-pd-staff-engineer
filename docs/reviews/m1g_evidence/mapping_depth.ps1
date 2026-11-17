# depth.ps1 - measure combinational depth from sequential/primary-input sources
# to named primary outputs of a Yosys write_json netlist.
# Output pins of each library cell are read from the Liberty file so the
# analysis works for ANY mapped cell set (maj3-based or NAND/AOI-based).
# Usage: powershell -File depth.ps1 <mapped.json> <liberty.lib>
param(
  [Parameter(Mandatory=$true)][string]$JsonPath,
  [Parameter(Mandatory=$true)][string]$LibPath
)

# ---- 1. Parse Liberty: cell type -> set of output pin names -------------------
$outPins = @{}
$curType = $null; $curPin = $null
foreach ($line in [System.IO.File]::ReadLines($LibPath)) {
    if ($line -match '^\s*cell\s*\(\s*"([^"]+)"\s*\)') { $curType = $Matches[1]; $curPin = $null; $outPins[$curType] = @{}; continue }
    if ($line -match '^\s*pg_pin\s*\(' -or $line -match '^\s*pin\s*\(\s*"([^"]+)"\s*\)') {
        if ($line -match 'pin\s*\(\s*"([^"]+)"\s*\)') { $curPin = $Matches[1] }
        continue
    }
    if ($null -ne $curType -and $null -ne $curPin -and $line -match 'direction\s*:\s*"?output"?\s*;') {
        $outPins[$curType][$curPin] = $true
    }
}

# ---- 2. Load netlist ----------------------------------------------------------
$json = Get-Content -Raw -LiteralPath $JsonPath | ConvertFrom-Json
$mod = $null; $topName = $null
foreach ($p in $json.modules.PSObject.Properties) {
    if ($p.Name -like '*soc_ctrl*') { $mod = $p.Value; $topName = $p.Name; break }
}
if ($null -eq $mod) { Write-Error "soc_ctrl module not found"; exit 2 }

# ---- 3. Build wire-bit -> driver map -----------------------------------------
$driver  = @{}   # wire-bit -> cell object (combinational driver)
$seqBits = @{}   # wire-bit -> $true if driven by a sequential cell
foreach ($c in $mod.cells.PSObject.Properties) {
    $cell = $c.Value
    $type = [string]$cell.type
    $isSeq = ($type -match 'dff|dfxtp|fdff|latch')
    $pins = $outPins[$type]
    foreach ($conn in $cell.connections.PSObject.Properties) {
        $port = $conn.Name
        $isOut = ($null -ne $pins -and $pins.ContainsKey($port))
        foreach ($b in $conn.Value) {
            if ($b -is [string]) { continue }   # constant 0/1/x/z
            $wb = [int]$b
            if ($isOut) {
                if ($isSeq) { $seqBits[$wb] = $true } else { $driver[$wb] = $cell }
            }
        }
    }
}

# ---- 4. Recursive depth with memoization -------------------------------------
$memo = @{}
function Get-Depth([object]$b) {
    if ($b -is [string]) { return 0 }                 # constant
    $wb = [int]$b
    if ($memo.ContainsKey($wb)) { return $memo[$wb] }
    if ($seqBits.ContainsKey($wb)) { $memo[$wb] = 0; return 0 }      # launch flop
    if (-not $driver.ContainsKey($wb)) { $memo[$wb] = 0; return 0 }  # primary input
    $cell = $driver[$wb]
    if (-not $memo.ContainsKey($wb)) { $memo[$wb] = -1 }             # DAG guard
    $pins = $outPins[[string]$cell.type]
    $maxIn = 0
    foreach ($conn in $cell.connections.PSObject.Properties) {
        $port = $conn.Name
        $isOut = ($null -ne $pins -and $pins.ContainsKey($port))
        if ($isOut) { continue }
        foreach ($ib in $conn.Value) {
            $d = Get-Depth $ib
            if ($d -gt $maxIn) { $maxIn = $d }
        }
    }
    $val = $maxIn + 1
    $memo[$wb] = $val
    return $val
}

function PortBits([string]$name) {
    foreach ($pp in $mod.ports.PSObject.Properties) { if ($pp.Name -eq $name) { return $pp.Value.bits } }
    return @()
}

$sumBits = PortBits 'sum'; $carryBits = PortBits 'carry_out'
$sumDepths = @(); for ($i=0; $i -lt $sumBits.Count; $i++) { $sumDepths += (Get-Depth $sumBits[$i]) }
$carryDepths = @(); for ($i=0; $i -lt $carryBits.Count; $i++) { $carryDepths += (Get-Depth $carryBits[$i]) }
$allOut = @($sumDepths) + @($carryDepths)
$maxDepth = ($allOut | Measure-Object -Maximum).Maximum

Write-Output ("TOP_MODULE=" + $topName)
Write-Output ("SUM_DEPTHS=" + ($sumDepths -join ','))
Write-Output ("CARRY_OUT_DEPTHS=" + ($carryDepths -join ','))
Write-Output ("SUM4_DEPTH=" + $sumDepths[4])
Write-Output ("CARRY_OUT2_DEPTH=" + $carryDepths[2])
Write-Output ("MAX_COMB_DEPTH=" + $maxDepth)
