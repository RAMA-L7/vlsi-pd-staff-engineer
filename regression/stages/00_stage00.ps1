# Stage 00 - Pre-flight (Phase 31 integration)
$ErrorActionPreference = 'Continue'
$Run  = $env:REG_RUN
$Repo = $env:REG_REPO
$Rtl  = $env:REG_RTL
$Sdc  = $env:REG_SDC
$Des  = $env:REG_DESIGN
$Py   = $env:REG_PYTHON
if (-not $Des) { $Des = 'test_counter' }
if (-not $Py) { $Py = 'python' }
$Out = Join-Path $Run 'stage00'
New-Item -ItemType Directory -Force -Path $Out | Out-Null
# Locate stage00_preflight.py
$preflight = Join-Path $env:REG_ROOT 'checks\stage00_preflight.py'
if (-not (Test-Path $preflight)) { $preflight = Join-Path $env:REG_ROOT 'regression\checks\stage00_preflight.py' }
if (-not (Test-Path $preflight)) { Write-Host "ERROR: stage00_preflight.py not found"; exit 2 }
# Build run-specific config
$cfg = [ordered]@{
  project_name = $Des
  top_module = $Des
  rtl_sources = @($Rtl)
  sdc = $Sdc
  pdk_root = $env:REG_PDK_ROOT
  liberty = @()
  lef = @()
}
$cfgPath = Join-Path $Out 'stage00_config.json'
$cfg | ConvertTo-Json -Depth 6 | Set-Content -Encoding utf8 $cfgPath
Write-Host "=== stage 00 pre-flight ==="
Write-Host "  config: $cfgPath"
& $Py $preflight $cfgPath --out-dir $Out 2>&1 | Tee-Object -FilePath (Join-Path $Out 'stage00.log')
$rc = $LASTEXITCODE
Write-Host "  stage00 exit=$rc"
exit $rc
