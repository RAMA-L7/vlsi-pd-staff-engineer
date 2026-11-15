# Repository-Integrated Regression Framework (Phase 18G)

This directory contains the Phase 14 RTL-to-GDSII regression framework integrated into the repository for reproducible multi-design execution.

## Structure

- `run_regression.ps1` - Main dual-domain regression runner (Windows PowerShell orchestration with WSL stages)
- `configs/` - Design configuration files (`test_counter.json`, `soc_ctrl.json`)
- `stages/` - Stage scripts/helpers for the 15-stage flow (PS1/TCL/PY/SH/RB)
- `checks/` - Acceptance and comparison scripts (`acceptance.py`, `compare_runs.py`, `lvs_compare.py`)
- `negative/` - Negative test suite (`run_negative_tests.sh`, N1-N7)
- `workspace/` - Run output directory (generated per execution)

## Current Status (2026-11-14)

- **Integration**: Complete (framework copied/adapted, design-aware generalizations applied).
- **test_counter**: **15/15 stages PASS**, **88/88 acceptance checks PASS**, **OVERALL PASS** (`workspace/run_20261114T150305Z`).
- **soc_ctrl**: **PARTIAL** - stages 01-02 PASS, stage 03 (EQY mapped LEC) FAIL, stages 04-15 NOT_EXECUTED (`workspace/run_20261114T152438Z`).
- **N1-N7 suite**: Preserved; end-to-end execution only partially observed in Phase 18G.
- **Reproducibility rerun/screenshots**: Not completed in Phase 18G.

## Usage

```powershell
# Full regression
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\audit\repo\regression\run_regression.ps1 -Design test_counter
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\audit\repo\regression\run_regression.ps1 -Design soc_ctrl

# Selective stages
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\audit\repo\regression\run_regression.ps1 -Design test_counter -Stages 01_synthesis,02_lec_generic

# Validate existing run
powershell.exe -NoProfile -ExecutionPolicy Bypass -File D:\audit\repo\regression\run_regression.ps1 -Design test_counter -ValidateOnly -RunDir D:\audit\repo\regression\workspace\run_20261114T150305Z
```

## 15-Stage Flow

1. Synthesis (Yosys)
2. Generic LEC
3. Technology-mapped LEC (EQY)
4. Floorplan
5. Placement
6. CTS
7. PDN
8. Power connectivity
9. Global route
10. Detailed route
11. Extraction
12. STA
13. GDSII
14. DRC
15. LVS

## Evidence

See `D:\audit\phase18G_regression_integration\PHASE18G_FINAL_REPORT.md` and related reports for detailed execution evidence, blockers, and reproduction commands.
