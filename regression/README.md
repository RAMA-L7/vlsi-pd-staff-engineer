# Repository-Integrated Regression Framework (Phase 18G)

This directory contains the Phase 14 RTL-to-GDSII regression framework integrated into the repository for reproducible multi-design execution.

## Structure

- `run_regression.ps1` - Main dual-domain regression runner (Windows PowerShell orchestration with WSL stages)
- `configs/` - Design configuration files (`test_counter.json`, `soc_ctrl.json`)
- `stages/` - Stage scripts/helpers for the 15-stage flow (PS1/TCL/PY/SH/RB)
- `checks/` - Acceptance and comparison scripts (`acceptance.py`, `compare_runs.py`, `lvs_compare.py`)
- `negative/` - Negative test suite (`run_negative_tests.sh`, N1-N7)
- `workspace/` - Run output directory (generated per execution)

## Status (authoritative matrix in root README.md)

The single current qualification matrix is `README.md` → "Authoritative
qualification matrix — this checkout" (commit `0a9eb57e…`, tree `ab0bcf96…`).
It supersedes the snapshot below, which is retained as **dated history**.

- **Integration**: complete (framework copied/adapted, design-aware generalizations applied).
- **Historical 2026-11-14 (Phase 18G)**: test_counter 15/15 stages PASS, 88/88 checks (`workspace/run_20261114T150305Z`); soc_ctrl PARTIAL — stages 01–02 PASS, stage 03 EQY FAIL, 04–15 NOT_EXECUTED (`workspace/run_20261114T152438Z`).
- **Later recorded runs**: test_counter baseline `baseline_20261116` / `_r2` (16/16 stages, 95/95 checks; commit `6c4a264`); soc_ctrl full physical `run_pub_m1d_soc_ctrl_full` (14/16, retained SS-timing and LVS-gate failures). Neither is a fresh run against `0a9eb57`.
- **N1–N7 suite**: preserved; end-to-end detection recorded in Phase 15 (2026-11-14).
- **Reproducibility**: two-run comparison and timestamp stripping documented in `docs/REGRESSION.md` and `docs/reviews/remediation-report.md`.

## Usage

Use `$env:REG_REPO` (or `-Repo`) rather than a hard-coded checkout path. The
project interpreter avoids the Microsoft Store `python` alias stub.

```powershell
# Preflight — exact missing-dependency report (see docs/REGRESSION.md)
& D:\tools\venv\Scripts\python.exe regression\checks\stage00_preflight.py `
    regression\project_config.template.json --out-dir <isolated-dir>

# One documented baseline command (isolated output dir per invocation)
& D:\tools\venv\Scripts\python.exe -m assistant baseline --design test_counter --run-id <ID>
& D:\tools\venv\Scripts\python.exe -m assistant baseline --design soc_ctrl --run-id <ID>

# Direct runner (equivalent; -Design forwarded by the CLI baseline command)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File regression\run_regression.ps1 -Design test_counter
powershell.exe -NoProfile -ExecutionPolicy Bypass -File regression\run_regression.ps1 -Design soc_ctrl

# Selective stages
powershell.exe -NoProfile -ExecutionPolicy Bypass -File regression\run_regression.ps1 -Design test_counter -Stages 01_synthesis,02_lec_generic

# Validate existing run
powershell.exe -NoProfile -ExecutionPolicy Bypass -File regression\run_regression.ps1 -Design test_counter -ValidateOnly -RunDir regression\workspace\run_20261114T150305Z
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
