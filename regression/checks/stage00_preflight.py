#!/usr/bin/env python3
"""
Stage-00 pre-flight checks (executable framework component).

Checks: RTL syntax/elaboration (via yosys if available), basic undriven/constant
output scan, SDC syntax basics, environment/tool detection (including the
Microsoft Store ``python.exe`` alias trap), path validation and workspace
isolation (output directory must not overlap an RTL/SDC source directory).

Outputs JSON result + human-readable report. Exit 0 = PASS, 1 = FAIL, 2 = usage.

Every dependency is reported with the exact executable/path searched and the
override that can supply it. ``--require-tool NAME`` promotes a named optional
tool to mandatory so a CI preflight can demand an exact tool set.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

# Portable config source of truth (WORKSTREAM 2).  The checker is also run as
# a standalone script, so locate the repository explicitly before importing.
try:
    _CHECKER_DIR = os.path.dirname(os.path.abspath(__file__))
    _REPO_ROOT = os.path.dirname(os.path.dirname(_CHECKER_DIR))
    if _REPO_ROOT not in sys.path:
        sys.path.insert(0, _REPO_ROOT)
    from assistant.tools import (  # noqa: E402
        load_pins,
        resolve_platform_config,
        validate_platform_collateral,
        validate_portable_config,
    )
    _PLATFORM_IMPORT_ERROR = ""
except Exception as _exc:  # pragma: no cover - defensive import guard
    _PLATFORM_IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"

# Mandatory tools are the ones the preflight itself needs to be meaningful.
MANDATORY_TOOLS = ("git", "python")
# Optional tools may legitimately live in WSL / OSS-CAD rather than on PATH.
OPTIONAL_TOOLS = ("yosys", "openroad", "eqy", "klayout")

# Where to look for optional tools when they are not on PATH.
TOOL_SEARCH = {
    "tools_root_env": ("ASSISTANT_TOOLS_ROOT", "REG_TOOLS"),
    "oss_cad_env": ("REG_OSS_CAD",),
    "wsl_distro_env": ("ASSISTANT_WSL_DISTRO",),
}
TOOL_OVERRIDE_ENV = {
    "yosys": "ASSISTANT_YOSYS",
    "openroad": "ASSISTANT_OPENROAD_WSL",
    "eqy": "ASSISTANT_EQY",
    "klayout": "ASSISTANT_KLAYOUT_WSL",
}


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(65536), b""):
            h.update(c)
    return h.hexdigest()


def check(name, ok, detail="", mandatory=True):
    return {"name": name, "status": "PASS" if ok else "FAIL",
            "mandatory": mandatory, "detail": detail}


def which(cmd):
    return shutil.which(cmd)


def read_text(p):
    try:
        with open(p, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read()
    except Exception:
        return None


def _env_first(names, environ=None):
    environ = os.environ if environ is None else environ
    for n in names:
        v = environ.get(n)
        if v and str(v).strip():
            return str(v).strip()
    return ""


def _same_or_within(path, directory):
    """True if *path* is *directory* or lies beneath it (case-normalised)."""
    try:
        p = os.path.normcase(os.path.abspath(path))
        d = os.path.normcase(os.path.abspath(directory))
    except (OSError, ValueError):
        return False
    try:
        return os.path.commonpath([p, d]) == d
    except ValueError:  # different drives on Windows
        return False


def _probe_python_candidate(exe):
    """Run a candidate interpreter; reject the Microsoft Store alias stub.

    Returns ``(ok, version_or_error)``. A real interpreter prints ``PYOK``
    followed by its version; the Store alias prints an advisory and returns a
    non-zero code (or is not runnable at all).
    """
    if not exe:
        return False, "no candidate"
    try:
        proc = subprocess.run(
            [exe, "-c", "import sys; print('PYOK', sys.version.split()[0])"],
            capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"not runnable: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0 and "PYOK" in out:
        version = ""
        m = re.search(r"PYOK\s+(\S+)", out)
        if m:
            version = m.group(1)
        return True, version or "unknown (PYOK marker present)"
    if "Microsoft Store" in out or "WindowsApps" in exe:
        return False, "Microsoft Store alias stub - not a real interpreter; set REG_PYTHON"
    return False, (out.strip()[:200] or f"exit={proc.returncode}")


def resolve_python(environ=None):
    """Return ``(path, version, detail)`` for the first working interpreter.

    Order: REG_PYTHON -> current interpreter (sys.executable) -> python ->
    python3 on PATH. Every rejected candidate is named in ``detail`` so the
    report never hides a Store-stub false PASS.
    """
    environ = os.environ if environ is None else environ
    candidates = []
    for value in (_env_first(("REG_PYTHON",), environ),
                  sys.executable,
                  which("python"),
                  which("python3")):
        if value and value not in candidates:
            candidates.append(value)
    rejected = []
    for exe in candidates:
        ok, info = _probe_python_candidate(exe)
        if ok:
            detail = f"{exe} (version {info})"
            if rejected:
                detail += "; rejected: " + "; ".join(rejected)
            return exe, info, detail
        rejected.append(f"{exe}: {info}")
    return "", "", ("NO WORKING PYTHON - " + "; ".join(rejected)) if rejected else "no python candidate found"


def _wsl_probe(distro, command, timeout=8):
    if not which("wsl") or not distro:
        return False, ""
    try:
        proc = subprocess.run(
            ["wsl", "-d", distro, "--", "bash", "-lc", command],
            capture_output=True, text=True, timeout=timeout,
        )
        return proc.returncode == 0, (proc.stdout or "").strip().splitlines()[0] if (proc.stdout or "").strip() else ""
    except (OSError, subprocess.SubprocessError):
        return False, ""


def resolve_tool(name, environ=None):
    """Resolve one optional tool; return ``(found, detail)``.

    ``detail`` names every location searched so a missing dependency is
    reported exactly rather than as a generic "may be in WSL/OSS-CAD".
    """
    environ = os.environ if environ is None else environ
    searched = []
    override = _env_first((TOOL_OVERRIDE_ENV.get(name, ""),), environ) if TOOL_OVERRIDE_ENV.get(name) else ""
    candidates = []
    if override:
        if override.startswith("/"):  # WSL path: validate inside the distro
            distro0 = _env_first(TOOL_SEARCH["wsl_distro_env"], environ) or "Ubuntu-24.04"
            ok_ov, _ = _wsl_probe(distro0, f"test -x {override} && echo present")
            if ok_ov:
                candidates.append((override, "override"))
            else:
                searched.append(f"override {override} (not executable in WSL)")
        elif os.path.isfile(override):
            candidates.append((override, "override"))
        else:
            searched.append(f"override {override} (not a file)")
    p = which(name)
    if p:
        candidates.append((p, "PATH"))
    tools_root = _env_first(TOOL_SEARCH["tools_root_env"], environ) or r"D:\tools"
    oss_root = _env_first(TOOL_SEARCH["oss_cad_env"], environ) or os.path.join(tools_root, "oss-cad-suite")
    exe_suffix = ".exe" if os.name == "nt" else ""
    for candidate in (
        os.path.join(oss_root, "bin", name + exe_suffix),
        os.path.join(tools_root, "bin", name + exe_suffix),
    ):
        if os.path.isfile(candidate):
            candidates.append((candidate, "tool tree"))
    distro = _env_first(TOOL_SEARCH["wsl_distro_env"], environ) or "Ubuntu-24.04"
    if name in ("yosys", "openroad", "eqy", "klayout"):
        ok, loc = _wsl_probe(distro, f"command -v {name}")
        searched.append(f"WSL[{distro}]:{name}")
        if ok and loc:
            candidates.append((loc, f"WSL[{distro}]"))
        # Known WSL install locations (overridable) that are not on WSL PATH.
        known_wsl = {
            "klayout": _env_first(("ASSISTANT_KLAYOUT_WSL",), environ)
                       or "/mnt/d/tools/KLayout/bin/klayout.sh",
            "openroad": _env_first(("ASSISTANT_OPENROAD_WSL",), environ)
                        or "/usr/local/bin/openroad",
        }
        if name in known_wsl:
            wsl_path = known_wsl[name]
            searched.append(f"WSL[{distro}]:{wsl_path}")
            ok2, _ = _wsl_probe(distro, f"test -x {wsl_path} && echo present")
            if ok2:
                candidates.append((wsl_path, f"WSL[{distro}] known path"))
    if candidates:
        loc, source = candidates[0]
        return True, f"{loc} (source: {source})"
    searched.append("PATH")
    searched.append(os.path.join(oss_root, "bin", name + exe_suffix))
    env_hint = TOOL_OVERRIDE_ENV.get(name, "REG_TOOLS/REG_OSS_CAD")
    return False, ("NOT FOUND - searched " + ", ".join(searched)
                   + f"; install it or set {env_hint}")


def main():
    if len(sys.argv) < 2:
        print("Usage: stage00_preflight.py <config.json|project_dir> [--out-dir DIR] "
              "[--require-tool NAME]")
        sys.exit(2)
    arg = sys.argv[1]
    out_dir = None
    required_tools = set()
    i = 2
    while i < len(sys.argv):
        if sys.argv[i] == "--out-dir" and i + 1 < len(sys.argv):
            out_dir = sys.argv[i + 1]
            i += 2
        elif sys.argv[i] == "--require-tool" and i + 1 < len(sys.argv):
            required_tools.add(sys.argv[i + 1])
            i += 2
        else:
            i += 1
    unknown = sorted(required_tools - set(OPTIONAL_TOOLS))
    if unknown:
        print(f"FAIL: --require-tool accepts only {', '.join(OPTIONAL_TOOLS)}; got {unknown}")
        sys.exit(2)

    # Load config
    cfg = {}
    if os.path.isfile(arg) and arg.endswith(".json"):
        with open(arg, "r", encoding="utf-8-sig") as f:
            cfg = json.load(f)
        base = os.path.dirname(os.path.abspath(arg))
    elif os.path.isdir(arg):
        base = os.path.abspath(arg)
        for cand in ("project.json", "project_config.json", "config.json"):
            cp = os.path.join(base, cand)
            if os.path.isfile(cp):
                with open(cp, "r", encoding="utf-8-sig") as f:
                    cfg = json.load(f)
                break
    else:
        print(f"FAIL: not found: {arg}")
        sys.exit(1)
    if out_dir is None:
        out_dir = os.path.join(base, "stage00_out")
    os.makedirs(out_dir, exist_ok=True)

    results = []
    dependency_report = {"mandatory": [], "optional": [], "missing_mandatory": [],
                         "missing_optional": [], "raised_to_mandatory": sorted(required_tools)}
    start = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # ---- Environment / exact dependency reporting -------------------------
    git_path = which("git")
    git_ok = git_path is not None
    results.append(check("tool_git_available", git_ok,
                         git_path if git_ok else "NOT FOUND - install Git or add it to PATH",
                         mandatory=True))
    dependency_report["mandatory"].append({"name": "git", "path": git_path or "", "ok": git_ok})
    if not git_ok:
        dependency_report["missing_mandatory"].append("git")

    py_path, py_version, py_detail = resolve_python()
    py_ok = bool(py_path)
    results.append(check("tool_python_available", py_ok, py_detail, mandatory=True))
    dependency_report["mandatory"].append(
        {"name": "python", "path": py_path or "", "version": py_version, "ok": py_ok}
    )
    if not py_ok:
        dependency_report["missing_mandatory"].append("python")

    for tool in OPTIONAL_TOOLS:
        found, detail = resolve_tool(tool)
        mandatory = tool in required_tools
        results.append(check(f"tool_{tool}_available", found, detail, mandatory=mandatory))
        dependency_report["optional"].append(
            {"name": tool, "path": detail, "ok": found, "mandatory": mandatory}
        )
        if not found:
            (dependency_report["missing_mandatory"] if mandatory
             else dependency_report["missing_optional"]).append(tool)

    # ---- Path validation & workspace isolation ---------------------------
    results.append(check("workspace_out_dir_writable", os.access(out_dir, os.W_OK),
                         out_dir, mandatory=True))

    # Source directories that the output tree must not overlap.  A config file
    # living inside its own output directory is fine; writing into the RTL/SDC
    # source tree is not.
    source_dirs = []
    rtl_paths = cfg.get("rtl_sources", [])
    if isinstance(rtl_paths, str):
        rtl_paths = [rtl_paths]
    for rp in rtl_paths:
        ap = rp if os.path.isabs(rp) else os.path.join(base, rp)
        source_dirs.append(os.path.dirname(os.path.abspath(ap)))
    sdc_cfg = cfg.get("sdc", cfg.get("sdc_path"))
    if sdc_cfg:
        sdc_abs = sdc_cfg if os.path.isabs(sdc_cfg) else os.path.join(base, sdc_cfg)
        source_dirs.append(os.path.dirname(os.path.abspath(sdc_abs)))
    overlaps = [d for d in source_dirs if _same_or_within(out_dir, d) or _same_or_within(d, out_dir)]
    isolated = not overlaps
    results.append(check(
        "workspace_isolation", isolated,
        (f"out={out_dir}; source dirs={sorted(set(source_dirs))}; overlaps={sorted(set(overlaps))}"
         if overlaps else f"out={out_dir}; no overlap with source dirs={sorted(set(source_dirs))}"),
        mandatory=True,
    ))

    # ---- RTL checks -------------------------------------------------------
    rtl_abs = []
    for rp in rtl_paths:
        ap = rp if os.path.isabs(rp) else os.path.join(base, rp)
        ex = os.path.isfile(ap)
        results.append(check(f"rtl_exists:{os.path.basename(rp)}", ex, ap, mandatory=True))
        if ex:
            rtl_abs.append(ap)

    # Basic syntax scan: undriven outputs / obvious issues (heuristic only).
    for ap in rtl_abs:
        txt = read_text(ap) or ""
        outs = re.findall(r"\boutput\s+(?:reg|wire|logic)?\s*(?:\[[^\]]+\])?\s*(\w+)", txt)
        outs += re.findall(r"\boutput\s+(?:reg|wire|logic)?\s*(?:\[[^\]]+\])?\s*\\?(\w+)", txt)
        issues = []
        for o in set(outs):
            if not re.search(r"\b" + re.escape(o) + r"\b\s*(?:\[|<=|=)", txt) and not re.search(
                r"assign\s+.*\b" + re.escape(o) + r"\b", txt
            ):
                issues.append(o)
        results.append(check(
            f"rtl_output_drive_scan:{os.path.basename(ap)}", len(issues) == 0,
            (f"heuristic undriven-suspect outputs: {sorted(issues)}" if issues
             else "no heuristic issues (heuristic only; yosys elaboration is authoritative)"),
            mandatory=False,
        ))
        const_outs = []
        for o in set(outs):
            assigns = re.findall(r"assign\s+" + re.escape(o) + r"\s*=\s*([^;]+);", txt)
            if assigns and all(re.match(r"^\s*(?:1'b0|1'b1|0|1)\s*$", a.strip()) for a in assigns):
                const_outs.append(o)
        results.append(check(
            f"rtl_constant_output_scan:{os.path.basename(ap)}", len(const_outs) == 0,
            (f"constant-collapsed outputs: {const_outs}" if const_outs else "none"),
            mandatory=False,
        ))

    # Try yosys elaboration if available and RTL provided.
    yosys_ok, yosys_detail = resolve_tool("yosys")
    if yosys_ok and rtl_abs:
        yosys_exe = yosys_detail.split(" (source:")[0]
        from_tool_tree = "source: tool tree" in yosys_detail or "source: override" in yosys_detail
        env = os.environ.copy()
        if from_tool_tree and os.name == "nt":
            # OSS-CAD yosys needs YOSYSHQ_ROOT plus bin+lib on PATH (what
            # environment.bat sets); a bare exe start fails with missing DLLs.
            oss_root = os.path.dirname(os.path.dirname(yosys_exe))
            env["YOSYSHQ_ROOT"] = oss_root
            env["PATH"] = os.pathsep.join(
                [os.path.join(oss_root, "bin"), os.path.join(oss_root, "lib"), env.get("PATH", "")]
            )
        top = cfg.get("top_module", cfg.get("top", ""))
        read_cmds = " ".join([f'"{p}"' for p in rtl_abs])
        script = f"read_verilog {read_cmds}; hierarchy -check" + (f" -top {top}" if top else "") + "; check; stat"
        try:
            proc = subprocess.run([yosys_exe, "-p", script], capture_output=True,
                                  text=True, timeout=60, env=env)
            ok = proc.returncode == 0
            detail = (proc.stdout + proc.stderr)[-2000:]
            results.append(check("rtl_yosys_elaboration", ok, detail, mandatory=True))
        except Exception as e:
            results.append(check("rtl_yosys_elaboration", False, str(e), mandatory=False))
    else:
        results.append(check(
            "rtl_yosys_elaboration", False,
            "NOT_TESTED - yosys unavailable or no RTL provided", mandatory=False,
        ))

    # SDC checks
    sdc = cfg.get("sdc", cfg.get("sdc_path"))
    if sdc:
        ap = sdc if os.path.isabs(sdc) else os.path.join(base, sdc)
        ex = os.path.isfile(ap)
        results.append(check("sdc_exists", ex, ap, mandatory=True))
        if ex:
            stxt = read_text(ap) or ""
            has_clk = bool(re.search(r"create_clock", stxt))
            results.append(check("sdc_has_create_clock", has_clk,
                                 "create_clock found" if has_clk else "no create_clock",
                                 mandatory=True))
            bal = stxt.count("{") == stxt.count("}")
            results.append(check("sdc_balanced_braces", bal,
                                 f"{{={stxt.count('{')} }}={stxt.count('}')}", mandatory=True))
    else:
        results.append(check("sdc_exists", False, "no SDC specified", mandatory=False))

    # Technology/PDK checks
    pdk_val = cfg.get("pdk")
    pdk_root = cfg.get("pdk_root")
    liberty_items = []
    lef_items = []
    if isinstance(pdk_val, dict):
        pdk_root = pdk_root or pdk_val.get("root")
        liberty_items = pdk_val.get("liberty") or pdk_val.get("liberty_files") or []
        lef_items = pdk_val.get("lef") or pdk_val.get("lef_files") or []
    elif isinstance(pdk_val, str):
        pdk_root = pdk_root or pdk_val
    if not liberty_items:
        liberty_items = cfg.get("liberty") or cfg.get("liberty_files") or []
    if not lef_items:
        lef_items = cfg.get("lef") or cfg.get("lef_files") or []
    if pdk_root:
        ap = pdk_root if os.path.isabs(pdk_root) else os.path.join(base, pdk_root)
        results.append(check("pdk_root_exists", os.path.isdir(ap), ap, mandatory=True))
    else:
        results.append(check("pdk_root_exists", False, "no PDK specified", mandatory=False))

    def _norm_items(v):
        if not v:
            return []
        return v if isinstance(v, list) else [v]

    def _resolve_item(it):
        if os.path.isabs(it):
            return it
        if pdk_root:
            pr = pdk_root if os.path.isabs(pdk_root) else os.path.join(base, pdk_root)
            if os.path.isdir(pr):
                return os.path.join(pr, it)
        return os.path.join(base, it)

    for it in _norm_items(liberty_items):
        ap = _resolve_item(it)
        results.append(check(f"liberty_exists:{os.path.basename(it)}",
                             os.path.isfile(ap), ap, mandatory=True))
    for it in _norm_items(lef_items):
        ap = _resolve_item(it)
        results.append(check(f"lef_exists:{os.path.basename(it)}",
                             os.path.isfile(ap), ap, mandatory=True))

    # ---- Platform collateral (portable config source of truth) -----------
    platform_block: dict = {}
    if _PLATFORM_IMPORT_ERROR:
        results.append(check(
            "platform_config_available", False,
            f"cannot load portable config helpers: {_PLATFORM_IMPORT_ERROR}",
            mandatory=True,
        ))
        dependency_report["missing_mandatory"].append("platform_config_available")
    else:
        pins_path = os.path.join(_REPO_ROOT, "assistant", "pins.json")
        pins = load_pins(pins_path)
        errs = validate_portable_config(pins)
        results.append(check(
            "platform_config_valid", not errs,
            ("portable_config structurally valid" if not errs
             else "invalid portable_config: " + "; ".join(errs)),
            mandatory=True,
        ))
        if errs:
            dependency_report["missing_mandatory"].append("platform_config_valid")
        pcfg = resolve_platform_config()
        plat_hd = pcfg["values"]["platform_hd"]
        plat_hs = pcfg["values"]["platform_hs"]
        validation = validate_platform_collateral(plat_hd, plat_hs, pins)
        platform_block = {
            "source_of_truth": "assistant/pins.json -> portable_config",
            "pins_path": pins_path,
            "config_errors": errs,
            "values": {
                "platform_hd": plat_hd,
                "platform_hs": plat_hs,
                "orfs_dir": pcfg["values"].get("orfs_dir", ""),
            },
            "sources": {
                k: pcfg["sources"].get(k, "")
                for k in ("platform_hd", "platform_hs", "orfs_dir")
            },
            "portable_fallback_applied": pcfg.get("portable_fallback_applied", []),
            "overall": validation["overall"],
            "required_count": validation["required_count"],
            "optional_count": validation["optional_count"],
            "missing_required": validation["missing_required"],
            "hash_mismatch": validation["hash_mismatch"],
            "optional_missing": validation["optional_missing"],
            "items": validation["items"],
        }
        for item in validation["items"]:
            ok = item["status"] == "MATCH"
            detail = (
                f"{item['key']}: status={item['status']} path={item['path']} "
                f"expected={item['expected']} actual={item['actual']} "
                f"used_by={item['used_by']}"
            )
            results.append(check(
                f"platform_collateral:{item['key']}", ok, detail,
                mandatory=bool(item["required"]),
            ))
        dependency_report["platform"] = platform_block
        for key in validation["missing_required"] + validation["hash_mismatch"]:
            dependency_report["missing_mandatory"].append(f"platform_collateral:{key}")
        for key in validation["optional_missing"]:
            dependency_report["missing_optional"].append(f"platform_collateral:{key}")

    # ---- Summary ----------------------------------------------------------
    mandatory_fails = [r for r in results if r["mandatory"] and r["status"] == "FAIL"]
    overall = "FAIL" if mandatory_fails else "PASS"
    report = {
        "schema_version": "30.stage00.3",
        "platform_config": platform_block,
        "started_utc": start,
        "finished_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "base_dir": base,
        "out_dir": out_dir,
        "overall": overall,
        "mandatory_failures": len(mandatory_fails),
        "dependencies": dependency_report,
        "checks": results,
    }
    jout = os.path.join(out_dir, "stage00_result.json")
    with open(jout, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    lines = [f"Stage-00 Pre-flight: {overall}", f"Base: {base}", ""]
    for r in results:
        lines.append(f"[{r['status']}] {'(M)' if r['mandatory'] else '(opt)'} "
                     f"{r['name']}: {r['detail'][:200]}")
    lines.append("")
    lines.append("Missing mandatory dependencies: "
                 + (", ".join(dependency_report["missing_mandatory"]) or "none"))
    lines.append("Missing optional dependencies: "
                 + (", ".join(dependency_report["missing_optional"]) or "none"))
    with open(os.path.join(out_dir, "stage00_report.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("\n".join(lines[:80]))
    print(f"\nOVERALL={overall} mandatory_failures={len(mandatory_fails)} "
          f"missing_mandatory={dependency_report['missing_mandatory']} "
          f"missing_optional={dependency_report['missing_optional']} json={jout}")
    sys.exit(0 if overall == "PASS" else 1)


if __name__ == "__main__":
    main()
