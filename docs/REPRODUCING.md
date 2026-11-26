# Reproducing the release-candidate audit claims

**Recorded UTC:** 2026-11-25T13:59:20Z
**Document type:** documentation/audit only. No source code was changed by this pass.
**Supported scope:** supervised academic PD workflow; simulated-tool integration tests and scripted EDA execution where available. No production signoff/tapeout claim, no numerical rating.

> **Evidence boundary.** Every path under `D:\audit` (including this working copy's `GOVERNANCE/`, `EVIDENCE/`, and `REPORTS/` directories) is a **local audit artifact**, not a public evidence bundle. No public RC commit is asserted in `GOVERNANCE/STARTING_IDENTITY.json` — the RC is pinned only by local file hashes. An external reviewer must re-run the commands below in their own clean room and publish their own artifacts. Historical results in `docs/reviews/` and `README.md` describe earlier identities and must not be presented as RC results.

## 1. Clean-room setup

1. Obtain the source: for the reviewed baseline use `git clone` of the project and `git checkout fa2bc751b3add19d4db250d01dac8de4f88ca347` (recorded `origin/main` and reviewed commit). The RC content is the reviewed source plus the R7.2 remediation delta; as no public RC commit is recorded here, request the exact RC source bundle from the release coordinator and verify it with §5 before trusting any RC-specific claim.
2. Create a **real** Python interpreter (the `python`/`python3` entries under `WindowsApps` are Store aliases and are rejected by preflight). Recorded: Python **3.10.11** venv at `D:\tools\venv\Scripts\python.exe`, pytest **9.2.0** (both verified on this host).
3. Install/verify the EDA stack per §2 and set the overrides in §3. Pins are host observations, not portability requirements: run `python -m assistant provenance` on the new host and report drift against `assistant/pins.json`; never silently accept a mismatch.
4. Keep output isolated: use a fresh `--run-id` with `--clean` for each baseline; never reuse a run directory as evidence of a second run.

## 2. Supported platform and recorded versions

| Item | Recorded / supported value | Notes |
|---|---|---|
| Host OS | Windows 10/11 x86-64 | Verified Windows host for the native runner |
| Shell | PowerShell 5.1+ for the runner; `bash` for `examples/test-counter/test_framework.sh` | — |
| Linux side | WSL 2, Ubuntu-24.04 | Override `ASSISTANT_WSL_DISTRO` |
| Python | 3.10.11 minimum validated; `D:\tools\venv\Scripts\python.exe` | `REG_PYTHON` selects a real interpreter |
| pytest | 9.2.0 or compatible | Verified: `python -m pytest --version` -> 9.2.0 |
| Git | modern Git on PATH | Preflight requires it |
| Yosys (native) | `0.69+154` (OSS-CAD); `D:\tools\oss-cad-suite\bin\yosys.exe` | Launch via `D:\tools\oss-cad-suite\environment.bat`; direct exe start fails with missing DLL |
| Yosys (WSL) | `0.33`, `/usr/bin/yosys` | Used by the framework shell script |
| Icarus Verilog / SymbiYosys / nextpnr | `iverilog.exe`, `sby.exe`, `nextpnr-*.exe` present in `D:\tools\oss-cad-suite\bin` | Presence verified; no RC run performed |
| OpenROAD | WSL `/usr/local/bin/openroad`, no version stamp; pin by ORFS commit `25b6429c1644a8682852646bebf19ff164402c76` | **Not on PATH on this host** (`Get-Command openroad,sta,OpenSTA` returned nothing) |
| OpenSTA | 3.1.0 recorded, WSL build tree | **Not on PATH on this host** |
| KLayout | 0.30.13, WSL `/mnt/d/tools/KLayout/bin/klayout.sh` | Windows native blocked by missing Qt5 libs |
| Netgen | no batch version flag; pinned by binary SHA-256 in `assistant/pins.json` | — |
| ORFS checkout | commit `25b6429c…`; fallback `D:\tools\OpenROAD-flow-scripts` | Directory existence verified; git HEAD/dirty state **not re-verified by this pass** |
| Sky130 PDK | `sky130_fd_sc_hd` commit `ac7fb61f…` | Host-specific; re-probe on another host |

## 3. Configurable paths (from `assistant/pins.json` → `config_overrides`)

Precedence: first non-empty variable in each list → explicit `tools_root` API argument (for `tools_root`) → recorded default (`portable_config.fallbacks` only when the recorded default path is absent). Values are validated; existence is reported in provenance, never assumed.

| Setting | Environment variables | Recorded default |
|---|---|---|
| Tools root | `ASSISTANT_TOOLS_ROOT`, `REG_TOOLS` | `D:\tools` |
| WSL distro | `ASSISTANT_WSL_DISTRO` | `Ubuntu-24.04` |
| ORFS checkout | `ASSISTANT_ORFS_DIR`, `ORFS_DIR` | `D:\tools\OpenROAD-flow-scripts` (fallback) |
| Liberty directory | `REG_LIB_DIR`, `ASSISTANT_LIB_DIR` | `<tools>\src\OpenROAD\src\sta\test\sky130hd` |
| PDK library repo | `ASSISTANT_PDK_ROOT` | `<tools>\pdk\skywater-pdk-complete\skywater-pdk-libs-sky130_fd_sc_hd` |
| Sky130HD / Sky130HS platform | `REG_PLATFORM_SKY130HD` / `ASSISTANT_PLATFORM_HD`; `REG_PLATFORM_SKY130HS` / `ASSISTANT_PLATFORM_HS` | `<ORFS>\flow\platforms\sky130hd` / `sky130hs` |
| KLayout (WSL path) | `ASSISTANT_KLAYOUT_WSL` | `/mnt/d/tools/KLayout/bin/klayout.sh` |
| OpenROAD (WSL path) | `ASSISTANT_OPENROAD_WSL` | `/usr/local/bin/openroad` |
| Netgen | `ASSISTANT_NETGEN` | `<tools>\netgen\bin\netgen` |
| Ledger key (only for cross-process persisted bindings) | `ASSISTANT_LEDGER_KEY` | unset -> in-memory ephemeral key; persisted derivation fails closed |

`required: true` collateral in `portable_config.collateral` is hash-checked by preflight; a missing file or hash mismatch fails. `required: false` entries are informational only. The modified-upstream Liberty file (`ec0e1067…`) has a documented LF-normalization reproduction recipe in `docs/SETUP.md` §5; it is host-local state with **no portable signoff claim**.

## 4. One baseline command

From the working-copy root (PowerShell). Preflight is the supported CLI form; the raw checker with `project_config.template.json` is *not* runnable unchanged (placeholder `top.v`/`top.sdc`).

```powershell
Set-Location D:\audit\release_candidate_20261125\WORKING_COPY
& D:\tools\venv\Scripts\python.exe -m assistant preflight
& D:\tools\venv\Scripts\python.exe -m assistant baseline --design test_counter --run-id <UNIQUE_ID> --clean
# soc_ctrl equivalent: --design soc_ctrl
```

The baseline writes its log and manifest to `<--out>/baseline_<design>_<run-id>/` (default `<repo>/regression/evidence/`); the regression itself uses `regression/workspace/run_<RunId>/`. `--dry-run` records provenance but exits 1 and must not be reported as a run. **The baseline was NOT re-executed for this RC by this documentation pass**; the command above is the documented procedure, not a new result.

## 5. Evidence-verification command

Verify the recorded per-file hashes and re-derive the recorded tree SHA-256. The recorded value is `67dff40684d1da6f83402a3a300193fa14a33a6025cd6394810d35f51cb76c8b8b` for 231 files (`GOVERNANCE/STARTING_IDENTITY.json`); `GOVERNANCE/STARTING_SHA256.txt` has one `<sha256>  <posix-relative-path>` line per file, and the tree hash is `sha256(json.dumps({path: hash}, sort_keys=True).encode())`.

```powershell
& D:\tools\venv\Scripts\python.exe -c "import hashlib,json,pathlib; r=pathlib.Path(r'D:\audit\release_candidate_20261125\WORKING_COPY'); m={}; [m.__setitem__(rel,h) for h,rel in (l.split('  ',1) for l in pathlib.Path(r'D:\audit\release_candidate_20261125\GOVERNANCE\STARTING_SHA256.txt').read_text(encoding='utf-8').splitlines() if l.strip())]; bad=[rel for rel,h in m.items() if not (r/rel).is_file() or hashlib.sha256((r/rel).read_bytes()).hexdigest()!=h]; print(len(m),'files',len(bad),'mismatches'); print(hashlib.sha256(json.dumps(m,sort_keys=True).encode()).hexdigest())"
```

At 13:59Z this printed `231 files 2 mismatches` and the recorded tree hash: `assistant/controller.py` and `assistant/eco.py` had been modified after the 13:28:16Z snapshot (by another owner; this agent did not touch `assistant/`). The check validates recorded files only; it does **not** enumerate added files. To compare a full current tree (excluding `.git`, `__pycache__`, `.pytest_cache`, `workspace`, `evidence`, as in the recording script):

```powershell
& D:\tools\venv\Scripts\python.exe -c "import hashlib,json,pathlib; r=pathlib.Path(r'D:\audit\release_candidate_20261125\WORKING_COPY'); ex={'.git','__pycache__','.pytest_cache','workspace','evidence'}; h=lambda p: hashlib.sha256(p.read_bytes()).hexdigest(); t={p.relative_to(r).as_posix():h(p) for p in sorted(r.rglob('*')) if p.is_file() and not any(x in p.parts for x in ex)}; print(len(t),'files',hashlib.sha256(json.dumps(t,sort_keys=True).encode()).hexdigest())"
```

A deviation from `67dff406…` is expected until the coordinator restamps the final identity: these three documents and the concurrent source edits post-date the starting snapshot. Treat `STARTING_IDENTITY.json` as the *starting* identity only.

Verify the A7-P0-01 local probe result (local artifact, not public evidence):

```powershell
Get-Content D:\audit\release_candidate_20261125\EVIDENCE\A7P0_BEFORE_AFTER.json -Raw | ConvertFrom-Json | Format-List
Get-Content D:\audit\release_candidate_20261125\EVIDENCE\CANCEL_STRESS_AFTER.txt
```

Expected: reviewed `fa2bc75` `qualified=true` / `CLEAN_PASS` / `downstream_ok=true`; RC `qualified=false` / `BLOCKED` / `downstream_ok=false` with three `check_index` integrity problems; cancel stress `total=30`, `fails=0`.

## 6. Credential-free test command

No foundry license, network service, model credits, or production ledger key is required. Tests inject throwaway keys (`monkeypatch.setenv("ASSISTANT_LEDGER_KEY", …)`) or use ephemeral in-memory keys.

```powershell
Set-Location D:\audit\release_candidate_20261125\WORKING_COPY
& D:\tools\venv\Scripts\python.exe -m pytest -q
# targeted A7-P0-01 / transaction regressions:
& D:\tools\venv\Scripts\python.exe -m pytest tests/r71_evidence_check_binding.py tests/r72_regressions.py tests/r72_transaction_integrity.py -q
# collection only (no pass/fail claim):
& D:\tools\venv\Scripts\python.exe -m pytest --collect-only -q
```

Collection was verified here: **562 tests collected in 2.37s** (2026-11-25). `EVIDENCE/FULL_SUITE.txt` is empty (0 bytes), so **no full-suite result is asserted for this RC**. The suite mixes simulated-tool detection contracts with real-runner-with-simulated-tools tests; a test count is not a real EDA run count.

## 7. Exact procedure for real EDA — **NOT_TESTED**

No real EDA run was performed against the RC identity by this pass. The following is the procedure a reviewer must execute and record; until then, every real-EDA row for this RC remains `NOT_TESTED`. The same boundary applies to the **fixed candidate** (`e591c30a…`): its full suite, containment, cancellation and targeted security regressions ran on **one Windows host** (Python 3.10.11, pytest 9.2.0), while the WSL EDA evidence belongs to the frozen/earlier tree only. Real-tool EDA on the fixed candidate is **NOT_TESTED**. See `docs/VALIDATION_SCOPE_AND_PORTABILITY.md`.

1. **Verify tools.** From PowerShell: `cmd /c "call D:\tools\oss-cad-suite\environment.bat && yosys -V"`, then `iverilog -V`, `sby --version`, `nextpnr-generic --version`, `vvp -V`. From WSL: `wsl -d Ubuntu-24.04 -- bash -lc 'command -v yosys; command -v openroad; command -v sta; command -v klayout; command -v netgen'`. On this host `openroad`, `sta` and `OpenSTA` are **not on PATH**; record that explicitly rather than assuming the WSL tools exist.
2. **Verify collateral.** `python -m assistant provenance` and `python -m assistant preflight`; compare every hash and path with `assistant/pins.json`; re-probe ORFS git HEAD (`25b6429c…`) and the Sky130 PDK commit (`ac7fb61f…`). Reproduce the LF-normalized Liberty file per `docs/SETUP.md` §5 if needed; a mismatch is a reported drift, not a portable signoff claim.
3. **Run one real baseline:** §4 command with a fresh `--run-id` for `--design test_counter` (and separately `--design soc_ctrl` if evaluating that design). Capture the returned manifest, logs, and 16 stage verdicts.
4. **Run it a second time** in a fresh isolated directory with a different `--run-id` and `--clean`.
5. **Compare artifacts:** `& D:\tools\venv\Scripts\python.exe regression\checks\compare_runs.py <run-dir-A> <run-dir-B> [output.txt]` (script usage: two run directories plus optional output). Compare deterministic netlists, DEF/SPEF/GDS, DRC/LVS databases and STA summary; timestamp/path-only differences are excluded and reported by the script.
6. **Classify honestly:** record real tool versions and full hashes; keep any FAIL (e.g. historical soc_ctrl routed SS timing) visible; label academic KLayout/LVS results as academic; do not upgrade them to foundry signoff.

## 8. Second-host reproduction — **NOT_TESTED**

No second host was available in this audit, and none was used for the fixed
candidate (`e591c30a…`) either. WSL served only as an EDA tool host for the
frozen/earlier tree; a native POSIX pytest suite, a second Windows host, and any
real-tool EDA run on the fixed candidate are **NOT_TESTED**. Exact procedure:

1. Provision a new host per §1–§3 (real Python 3.10.11 venv, pytest 9.2.0, WSL2 Ubuntu-24.04, OSS-CAD suite with `environment.bat`, ORFS at the pinned commit, Sky130 PDK, KLayout/Netgen WSL paths).
2. Set only the §3 environment overrides needed for that host; do not copy `D:\audit` paths as if they were portable defaults.
3. Run `python -m assistant provenance`, then §5 hash checks against a source bundle supplied by the coordinator, then §4 preflight and baseline.
4. Run §6 test command and §7 real-EDA steps, then §7.5 comparison against the first host.
5. Publish the new logs/manifests as that host's evidence and enumerate every drift (tool version, ORFS/PDK hash, host path, collateral hash, stage verdict). A drifted result is a new finding, not a portability PASS.

## 9. What is not evidence

- Local `D:\audit` paths and the empty `EVIDENCE/FULL_SUITE.txt`; they are working artifacts of one audit session.
- Historical counts (353/324 pytest passes, 47/0/0 framework, 16/16 baselines) and historical published identities; they belong to earlier identities.
- Simulated providers, test-only ledger keys, and CLI-supplied PASS records (the supervised A-1 qualification).
- Tool *presence* checks in §7.1; presence is not execution.
- Any claim of production/tapeout authorization, numerical rating, or complete defect closure.
