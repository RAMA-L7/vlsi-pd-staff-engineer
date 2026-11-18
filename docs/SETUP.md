# Setup guide — supported platform, versions and reproduction (SR-08)

This is the **supported setup path** for the deterministic regression. The
"Create the GitHub repository" notes that formerly occupied this file are
preserved at the end as historical context; they describe repository creation,
not how to run the regression.

## 1. Supported platform (explicit)

The pins in `assistant/pins.json` were recorded on one validated developer host.
They describe that host; they are **not** a claim of portability or signoff on
any other machine.

| Item | Supported / recorded value |
|---|---|
| Host OS | Windows 10/11 x86-64 |
| Shell | PowerShell 5.1+ (Windows) for the runner; `bash` for `test_framework.sh` |
| Linux side | WSL 2 with **Ubuntu-24.04** (override: `ASSISTANT_WSL_DISTRO`) |
| Python | **3.10.11 minimum** (validated); project venv at `D:\tools\venv\Scripts\python.exe`. The `python`/`python3` entries in `WindowsApps` are Microsoft Store alias stubs and are rejected by preflight — set `REG_PYTHON` to a real interpreter. |
| pytest | 9.2.0 or compatible (for the targeted test suite) |
| Git | any modern Git with `git` on PATH (preflight requires it) |

## 2. Minimum / recorded tool versions

| Tool | Recorded version (minimum floor) | Location / invocation |
|---|---|---|
| Yosys (native) | `0.69+154` (OSS-CAD, ≥0.69) | `D:\tools\oss-cad-suite\bin\yosys.exe`; must be launched via `environment.bat` (sets `YOSYSHQ_ROOT`, `PATH=bin;lib`) |
| Yosys (WSL) | `0.33` (≥0.33) | `/usr/bin/yosys` in the WSL distro; used by `examples/test-counter/test_framework.sh` |
| EQY | `v0.69-1-g7a92d84` (≥0.69) | `D:\tools\oss-cad-suite\bin\eqy.exe`; needs LF line endings and plugins in the run directory |
| OpenROAD | no version stamp (`bazel-nostamp`) | `/usr/local/bin/openroad` in WSL; **pin by ORFS commit** `25b6429c1644a8682852646bebf19ff164402c76`, not by a version string |
| OpenSTA | 3.1.0 (recorded) | WSL binary under the OpenSTA build tree; used by stage 12 |
| KLayout | `0.30.13` (recorded) | `/mnt/d/tools/KLayout/bin/klayout.sh` (WSL). Windows native is blocked (missing Qt5 libs); override with `ASSISTANT_KLAYOUT_WSL` |
| Netgen | `0.6.2401` (recorded) | `D:\tools\netgen\bin\netgen` (native) / WSL path; no batch version flag — pinned by binary SHA-256 |
| OpenROAD-flow-scripts | commit `25b6429c…` | `D:\rta-real-world-benchmark\02_projects\OpenROAD-flow-scripts` (override: `ASSISTANT_ORFS_DIR` / `ORFS_DIR`) |
| Sky130 PDK | commit `ac7fb61f…` | `D:\tools\pdk\skywater-pdk-complete\skywater-pdk-libs-sky130_fd_sc_hd` (override: `ASSISTANT_PDK_ROOT`) |

A different host must re-probe with `python -m assistant provenance` and report
drift against `assistant/pins.json`; do not silently accept a mismatch.

## 3. Validated configuration overrides (no hard-coded roots required)

`assistant/tools.py::resolve_environment_config` resolves these in order: first
non-empty environment variable in the list, then the explicit `tools_root` API
argument (for `tools_root`), then the recorded default. Values are validated
(non-empty string, no control characters; whitespace-only is treated as unset)
and their existence is reported in `provenance.config.validation` — never
silently assumed.

| Setting | Override variables | Recorded default |
|---|---|---|
| tools root | `ASSISTANT_TOOLS_ROOT`, `REG_TOOLS` | `D:\tools` |
| WSL distro | `ASSISTANT_WSL_DISTRO` | `Ubuntu-24.04` |
| ORFS checkout | `ASSISTANT_ORFS_DIR`, `ORFS_DIR` | `D:\rta-real-world-benchmark\02_projects\OpenROAD-flow-scripts` |
| Liberty directory | `REG_LIB_DIR`, `ASSISTANT_LIB_DIR` | `<tools>\src\OpenROAD\src\sta\test\sky130hd` |
| PDK library repo | `ASSISTANT_PDK_ROOT` | `<tools>\pdk\skywater-pdk-complete\skywater-pdk-libs-sky130_fd_sc_hd` |
| Sky130HD platform | `REG_PLATFORM_SKY130HD`, `ASSISTANT_PLATFORM_HD` | `<ORFS>\flow\platforms\sky130hd` |
| Sky130HS platform | `REG_PLATFORM_SKY130HS`, `ASSISTANT_PLATFORM_HS` | `<ORFS>\flow\platforms\sky130hs` |
| KLayout (WSL path) | `ASSISTANT_KLAYOUT_WSL` | `/mnt/d/tools/KLayout/bin/klayout.sh` |
| OpenROAD (WSL path) | `ASSISTANT_OPENROAD_WSL` | `/usr/local/bin/openroad` |
| Netgen binary | `ASSISTANT_NETGEN` | `<tools>\netgen\bin\netgen` |

Preflight-specific overrides: `REG_PYTHON`, `ASSISTANT_YOSYS`, `ASSISTANT_EQY`,
`REG_OSS_CAD`, `ASSISTANT_KLAYOUT_WSL` (see `docs/REGRESSION.md`).

## 3A. Portable config source of truth (WORKSTREAM 2)

`assistant/pins.json` → `portable_config` is the single source of truth for
host-default resolution and platform-collateral identity. It is consumed by
`assistant.tools.resolve_platform_config`, by `assistant.baseline.run_baseline`
(which injects the resolved overrides into the regression subprocess), and by
`regression/checks/stage00_preflight.py` (platform-collateral checks).

**Fallback precedence** (identical to `config_overrides`): first non-empty
environment variable in the table above → explicit `tools_root` API argument →
`portable_config.fallbacks` → recorded developer-host default. A fallback is
applied **only** when the recorded default path is absent, no caller override
was supplied, and the fallback itself exists; it is recorded in provenance as
source `portable_fallback`. Explicit caller overrides always win. These values
were **validated on one host only** (ORFS git HEAD
`25b6429c1644a8682852646bebf19ff164402c76`) and are **not** a portability or
signoff claim — a different host must re-probe and report drift.

**Mandatory vs optional collateral.** `portable_config.collateral` pins each
file with an exact SHA-256 and a `required` flag. `required: true` files are
consumed by the wrapped 16-stage regression (runner `inputFiles` / stage
scripts / provenance context hashes); `required: false` files exist in ORFS but
are not consumed by the wrapped regression. A missing required file or a hash
mismatch makes preflight FAIL; an absent optional file is reported only.

| Required collateral (`<platform_hd>` unless noted) | SHA-256 (first 12) | Consumed by |
|---|---|---|
| `lef/sky130_fd_sc_hd.tlef` | `0cdae79b7225` | `common.tcl`, extraction, GDS, runner |
| `lef/sky130_fd_sc_hd_merged.lef` | `184691398936` | `common.tcl`, GDS, runner |
| `gds/sky130_fd_sc_hd.gds` | `15240e34c50e` | stage 13 GDS (`REG_GDS_CELLS`) |
| `sky130hd.lyt` | `832ff141d65e` | stage 13 GDS (`REG_GDS_TECH`) |
| `setRC.tcl` | `39e4f6e3d09f` | stage 11 extraction, runner DECK |
| `rcx_patterns.rules` (`<platform_hs>`) | `9d7c81762235` | stage 11 extraction (HS copy; HD copy is a pointer) |
| `make_tracks.tcl` | `9b3421af6417` | stage 04 floorplan, runner DECK |
| `pdn.tcl` | `ac98412b92b3` | PDN provenance, runner inputFiles |
| `cdl/sky130hd.cdl` | `ea3e86abfc92` | stage 15 LVS, deck identity |
| `drc/sky130hd.lydrc` | `89cb0a6677c8` | stage 14 DRC, deck identity |
| `lvs/sky130hd.lylvs` | `2c90698c902e` | stage 15 LVS, deck identity |
| `lib/sky130_fd_sc_hd__tt_025C_1v80.lib` | `ec0e1067a35c` | runner LIB context (modified-upstream copy) |

The three Liberty files actually used for synthesis/STA live in `REG_LIB_DIR`
and are pinned under `assistant/pins.json:libraries` (`92eb4e93…`, `bbc8d071…`,
`3924c861…`); they are distinct from the modified platform-tree copy above.
Full 64-character hashes are in `pins.json` and in the preflight JSON report.

With this source of truth, the one documented baseline command no longer needs
`ASSISTANT_ORFS_DIR` / `REG_PLATFORM_SKY130HD` / `REG_PLATFORM_SKY130HS` to be
supplied on the validated host: the wrapper resolves and injects them. Supplying
them explicitly still overrides the fallback. `python -m assistant preflight`
reports the exact resolved sources (`env` / `default` / `portable_fallback`) and
every collateral file's identity in `platform_config`.

## 4. One documented baseline command

```powershell
# Preflight first (supported command; uses the validated repo root and portable config):
& D:\tools\venv\Scripts\python.exe -m assistant preflight
# Advanced: the raw checker can be invoked directly, but a filled project config is required;
# the template contains placeholders (top.v/top.sdc) and will fail if used unchanged.

# Then the baseline:
& D:\tools\venv\Scripts\python.exe -m assistant baseline --design test_counter --run-id <ID>
#   --clean       reuse a run id in a fresh isolated output directory
#   --no-isolate  legacy shared output directory
#   --dry-run     record provenance but never return success (exit 1)
#soc_ctrl equivalent: --design soc_ctrl
```

Each invocation writes its log and manifest to
`<--out>/baseline_<design>_<run-id>/` (default `<repo>/regression/evidence/`).
The regression itself still uses its own `regression/workspace/run_<RunId>/`.

## 5. Modified upstream collateral — legally distributable reproduction (SR-08)

`assistant/pins.json` records the ORFS checkout at commit
`25b6429c1644a8682852646bebf19ff164402c76` as **dirty**, with
`flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib` modified
(observed SHA-256 `ec0e1067a35c8bf20b11e58d1e8ac53326067e4dac84a125cc1b917a3518d0d9`).

**What was observed:** `git status --porcelain` reported the file as modified
while `git diff --numstat` was empty and the working-tree file differed from the
HEAD blob by one byte per line — consistent with a **CRLF/LF line-ending
normalization**, not a semantic Liberty change. This was a host-local state; the
file must be re-verified on any other host. **No portable signoff is claimed.**

**Legally distributable reproduction recipe** (no proprietary or large binary
is committed to this repository):

1. OpenROAD-flow-scripts and the SkyWater open-PDK libraries are published
   under permissive open-source licenses (verify the `LICENSE` files in your
   own checkout before redistribution). The exact binary need not be shipped.
2. Clone the upstream ORFS repository and check out the pinned commit:
   ```bash
   git clone <upstream OpenROAD-flow-scripts URL> openroad-flow-scripts
   cd openroad-flow-scripts
   git checkout 25b6429c1644a8682852646bebf19ff164402c76
   ```
3. Reproduce the line-ending state instead of distributing a `.lib` patch:
   ```bash
   # The developer host had core.autocrlf=true; materialise the working copy
   # with LF endings (one byte per line removed) to match the recorded hash.
   python - <<'PY'
   from pathlib import Path
   p = Path('flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib')
   data = p.read_bytes().replace(b'\r\n', b'\n')
   p.write_bytes(data)
   PY
   ```
4. Verify the recorded identity before use:
   ```bash
   sha256sum flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib
   # expected: ec0e1067a35c8bf20b11e58d1e8ac53326067e4dac84a125cc1b917a3518d0d9
   ```
5. If the hash differs, the host has different collateral: record the difference
   and obtain the exact file from the upstream host through the normal review
   channel. Do **not** relabel the run as portable or treat it as signoff.

The regression actually consumes the Liberty files from `REG_LIB_DIR` (hashes
`92eb4e93…`, `bbc8d071…`, `3924c861…`), not this platform-tree copy; both are
recorded so the distinction is auditable.

## Historical note — repository creation (superseded)

The original content of this file described creating a GitHub repository and
pushing a tarball. It is retained here only as provenance of the project's
origin and does not describe the supported regression setup above.

```bash
git init
git add -A
git commit -m "Initial commit: VLSI PD staff engineer multi-agent framework"
git branch -M main
git remote add origin https://github.com/<YOUR-USERNAME>/vlsi-pd-staff-engineer.git
# Publication is authorized only by explicit instruction; see PUBLICATION_REPORT.md
```
