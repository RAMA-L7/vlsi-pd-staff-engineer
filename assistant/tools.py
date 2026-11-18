"""Tool discovery, version pinning and provenance capture.

All probes are deterministic shell-free lookups where possible; versions are
recorded exactly as the tool reports them (plus a binary hash fallback for
tools whose CLI does not support a version flag in batch mode).

Portability (SR-08): every workstation-specific root is an overridable,
validated setting.  The defaults are the recorded developer-host values, so
behaviour is unchanged when no environment override is supplied.  See
``docs/SETUP.md`` for the supported platform and minimum tool versions.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field

from .hashing import sha256_file


@dataclass
class ToolPin:
    name: str
    version: str
    path: str = ""
    sha256: str = ""
    source: str = "cli"
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "version": self.version,
            "path": self.path,
            "sha256": self.sha256,
            "source": self.source,
            "notes": self.notes,
        }


def _run(
    argv: list[str] | str, timeout: float = 30, *, shell: bool = False
) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            shell=shell,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, f"<probe failed: {exc}>"
    return proc.returncode, proc.stdout.decode("utf-8", "replace")


def probe_tool(
    name: str,
    argv: list[str],
    *,
    fallback_file: str | None = None,
    version_regex: str | None = None,
    notes: str = "",
) -> ToolPin:
    """Run *argv* and record the first line as the version string."""
    import re

    rc, out = _run(argv)
    version = "<unavailable>"
    source = "cli"
    if rc == 0 and out.strip():
        first = out.strip().splitlines()[0].strip()
        if version_regex:
            m = re.search(version_regex, out)
            if m:
                first = m.group(0)
        version = first
    elif fallback_file and os.path.isfile(fallback_file):
        version = f"<file-hash only: {os.path.basename(fallback_file)}>"
        source = "sha256"
    path = shutil.which(argv[0]) or argv[0]
    digest = ""
    if os.path.isfile(path):
        try:
            digest = sha256_file(path)
        except OSError:
            digest = ""
    if fallback_file and os.path.isfile(fallback_file) and not digest:
        try:
            digest = sha256_file(fallback_file)
        except OSError:
            digest = ""
    return ToolPin(
        name=name,
        version=version,
        path=path,
        sha256=digest,
        source=source,
        notes=notes,
    )


def probe_wsl(wsl_distro: str, command: str, *, timeout: float = 60) -> tuple[int, str]:
    """Run a command inside WSL (used for OpenROAD/netgen/KLayout probes)."""
    return _run(["wsl", "-d", wsl_distro, "--", "bash", "-lc", command], timeout)


# ---------------------------------------------------------------------------
# Validated, overridable environment configuration (SR-08).
#
# Every value below replaces a former hard-coded workstation path.  The
# defaults are exactly the previously hard-coded values, so an unconfigured
# host behaves identically.  Override precedence: first non-empty environment
# variable in the tuple, then ``tools_root_override`` for tools_root, then the
# recorded default.  An override is validated before use: it must be a
# non-empty string without NUL/control characters.  Path existence is
# *reported* (never silently assumed) in ``config.validation``.
# ---------------------------------------------------------------------------
ENV_KEYS: dict[str, tuple[str, ...]] = {
    "tools_root": ("ASSISTANT_TOOLS_ROOT", "REG_TOOLS"),
    "wsl_distro": ("ASSISTANT_WSL_DISTRO",),
    "orfs_dir": ("ASSISTANT_ORFS_DIR", "ORFS_DIR"),
    "lib_dir": ("REG_LIB_DIR", "ASSISTANT_LIB_DIR"),
    "pdk_root": ("ASSISTANT_PDK_ROOT",),
    "platform_hd": ("REG_PLATFORM_SKY130HD", "ASSISTANT_PLATFORM_HD"),
    "platform_hs": ("REG_PLATFORM_SKY130HS", "ASSISTANT_PLATFORM_HS"),
    "klayout_wsl": ("ASSISTANT_KLAYOUT_WSL",),
    "openroad_wsl": ("ASSISTANT_OPENROAD_WSL",),
    "netgen": ("ASSISTANT_NETGEN",),
}

#: kind -> how (or whether) existence is validated on this host
CONFIG_KINDS: dict[str, str] = {
    "tools_root": "dir",
    "wsl_distro": "identifier",
    "orfs_dir": "dir",
    "lib_dir": "dir",
    "pdk_root": "dir",
    "platform_hd": "dir",
    "platform_hs": "dir",
    "klayout_wsl": "wsl-path",
    "openroad_wsl": "wsl-path",
    "netgen": "file",
}


def _validated_value(name: str, value: object) -> str:
    """Validate one override/default; raise ValueError on a malformed value."""
    if not isinstance(value, str):
        raise ValueError(f"{name}: override must be a string, got {type(value).__name__}")
    v = value.strip()
    if not v:
        raise ValueError(f"{name}: override is empty")
    if "\x00" in v or any(ord(ch) < 32 for ch in v):
        raise ValueError(f"{name}: override contains a control character")
    return v


def resolve_environment_config(
    environ: dict[str, str] | None = None,
    *,
    tools_root_override: str | None = None,
) -> dict:
    """Resolve and validate all overridable roots.

    Returns ``{"values": {...}, "sources": {...}, "validation": {...}}``.
    Pure with respect to ``os.environ`` (a mapping may be injected in tests).
    """
    env = os.environ if environ is None else environ
    values: dict[str, str] = {}
    sources: dict[str, str] = {}

    def pick(key: str, default: str) -> str:
        for var in ENV_KEYS[key]:
            raw = env.get(var)
            if raw is not None and str(raw).strip():
                values[key] = _validated_value(var, raw)
                sources[key] = var
                return values[key]
        values[key] = _validated_value(key, default)
        sources[key] = "default"
        return values[key]

    tools_root = pick(
        "tools_root",
        _validated_value("tools_root_override", tools_root_override)
        if tools_root_override is not None
        else r"D:\tools",
    )
    if tools_root_override is not None:
        # An explicit API override outranks environment variables.
        values["tools_root"] = _validated_value("tools_root_override", tools_root_override)
        sources["tools_root"] = "tools_root_override"
        tools_root = values["tools_root"]

    orfs_dir = pick(
        "orfs_dir",
        os.path.join(r"D:\rta-real-world-benchmark\02_projects", "OpenROAD-flow-scripts"),
    )
    pick("wsl_distro", "Ubuntu-24.04")
    pick("lib_dir", os.path.join(tools_root, "src", "OpenROAD", "src", "sta", "test", "sky130hd"))
    pick(
        "pdk_root",
        os.path.join(
            tools_root, "pdk", "skywater-pdk-complete", "skywater-pdk-libs-sky130_fd_sc_hd"
        ),
    )
    pick("platform_hd", os.path.join(orfs_dir, "flow", "platforms", "sky130hd"))
    pick("platform_hs", os.path.join(orfs_dir, "flow", "platforms", "sky130hs"))
    pick("klayout_wsl", "/mnt/d/tools/KLayout/bin/klayout.sh")
    pick("openroad_wsl", "/usr/local/bin/openroad")
    pick("netgen", os.path.join(tools_root, "netgen", "bin", "netgen"))

    validation: dict[str, dict] = {}
    for key, value in values.items():
        kind = CONFIG_KINDS[key]
        exists: bool | None
        if kind == "dir":
            exists = os.path.isdir(value)
        elif kind == "file":
            exists = os.path.isfile(value)
        else:
            exists = None  # WSL paths / distro names cannot be checked from Windows
        validation[key] = {"kind": kind, "exists": exists, "source": sources[key]}
    return {"values": values, "sources": sources, "validation": validation}


# ---------------------------------------------------------------------------
# Portable config source of truth (WORKSTREAM 2).
#
# ``assistant/pins.json -> portable_config`` is the single source of truth for
# (a) host fallbacks used ONLY when a recorded default path is absent and no
# environment override was supplied, and (b) the exact SHA-256 identity of the
# platform collateral consumed by the wrapped regression.  Helpers are pure /
# injectable so tests never touch the real toolchain.  No independent
# portability beyond the validated host is claimed by these values.
# ---------------------------------------------------------------------------
PINS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pins.json")
_HASH_HEX = frozenset("0123456789abcdefABCDEF")


def load_pins(path: str | None = None) -> dict:
    """Load ``assistant/pins.json`` (the portable config source of truth)."""
    p = path or PINS_PATH
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def validate_portable_config(pins: dict) -> list[str]:
    """Return structural errors in ``portable_config``; empty means usable.

    This guards against a truncated/edited config silently disabling fallback
    or collateral validation.  It does not touch the filesystem.
    """
    errors: list[str] = []
    pc = pins.get("portable_config")
    if not isinstance(pc, dict):
        return ["portable_config: missing or not an object"]
    fallbacks = pc.get("fallbacks")
    if not isinstance(fallbacks, dict) or not fallbacks:
        errors.append("portable_config.fallbacks: missing or empty")
    else:
        for key, val in fallbacks.items():
            if not isinstance(val, str) or not val.strip():
                errors.append(f"portable_config.fallbacks.{key}: non-empty string required")
    collateral = pc.get("collateral")
    if not isinstance(collateral, list) or not collateral:
        errors.append("portable_config.collateral: missing or empty")
    else:
        seen: set[str] = set()
        for i, item in enumerate(collateral):
            if not isinstance(item, dict):
                errors.append(f"portable_config.collateral[{i}]: not an object")
                continue
            key = item.get("key")
            if not isinstance(key, str) or not key:
                errors.append(f"portable_config.collateral[{i}]: missing key")
                continue
            if key in seen:
                errors.append(f"portable_config.collateral[{i}]: duplicate key {key!r}")
            seen.add(key)
            rel = item.get("path")
            if not isinstance(rel, str) or not rel.strip() or os.path.isabs(rel):
                errors.append(f"{key}: path must be a non-empty relative path")
            if item.get("base", "platform_hd") not in ("platform_hd", "platform_hs"):
                errors.append(f"{key}: base must be platform_hd or platform_hs")
            digest = item.get("sha256")
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(c not in _HASH_HEX for c in digest)):
                errors.append(f"{key}: sha256 must be 64 hex characters")
            if not isinstance(item.get("required"), bool):
                errors.append(f"{key}: required must be a boolean")
    return errors


def apply_portable_fallbacks(cfg: dict, pins: dict, exists) -> dict:
    """Apply portable fallbacks in place for absent defaults.

    A fallback is used only when the key still has source ``default`` (i.e. no
    environment/API override) AND the recorded default path is absent AND the
    fallback itself exists.  The fallback never overwrites an override and is
    never used when it would point at a non-existent path.  ``exists`` is an
    injectable ``os.path.isdir``-compatible predicate.
    """
    pc = pins.get("portable_config") or {}
    fallbacks = pc.get("fallbacks") or {}
    applied: list[str] = []
    for key, fallback in fallbacks.items():
        if key not in cfg.get("values", {}) or cfg.get("sources", {}).get(key) != "default":
            continue
        current = cfg["values"][key]
        if exists(current) or not exists(fallback):
            continue
        cfg["values"][key] = fallback
        cfg["sources"][key] = "portable_fallback"
        applied.append(key)
    if applied:
        cfg["portable_fallback_applied"] = applied
        for key in applied:
            kind = CONFIG_KINDS.get(key)
            if kind in ("dir", "file"):
                cfg["validation"][key] = {
                    "kind": kind, "exists": exists(cfg["values"][key]),
                    "source": "portable_fallback",
                }
    cfg["portable_config_source"] = pc.get(
        "source_of_truth", "assistant/pins.json -> portable_config"
    )
    return cfg


def resolve_platform_config(
    environ: dict[str, str] | None = None,
    *,
    pins: dict | None = None,
    exists=None,
    tools_root_override: str | None = None,
) -> dict:
    """Resolve config with portable fallback for absent host defaults.

    Pure with respect to ``os.environ`` when *environ* is supplied.  If the
    config source of truth is structurally invalid, the error list is attached
    as ``portable_config_errors`` and no fallback is applied (fail-closed).
    """
    resolved_pins = pins if pins is not None else load_pins()
    cfg = resolve_environment_config(environ, tools_root_override=tools_root_override)
    errs = validate_portable_config(resolved_pins)
    if errs:
        cfg["portable_config_errors"] = errs
        return cfg
    return apply_portable_fallbacks(cfg, resolved_pins, exists or os.path.isdir)


def validate_platform_collateral(
    platform_hd: str,
    platform_hs: str,
    pins: dict | None = None,
    *,
    sha256=None,
    exists=None,
) -> dict:
    """Validate every pinned collateral file against its exact SHA-256.

    ``sha256`` / ``exists`` are injectable so tests are deterministic.  Status
    per item: MATCH / MISSING (required absent) / HASH_MISMATCH /
    OPTIONAL_MISSING.  Overall is FAIL only for missing-required or
    hash-mismatch; optional absences are reported, never fatal.
    """
    resolved_pins = pins if pins is not None else load_pins()
    pc = resolved_pins.get("portable_config") or {}
    items = pc.get("collateral") or []
    digest_fn = sha256 or sha256_file
    exists_fn = exists or os.path.isfile
    bases = {"platform_hd": platform_hd, "platform_hs": platform_hs}
    results: list[dict] = []
    missing_required: list[str] = []
    hash_mismatch: list[str] = []
    optional_missing: list[str] = []
    for item in items:
        key = item.get("key", "<unnamed>")
        rel = item.get("path", "")
        base = bases.get(item.get("base", "platform_hd"), platform_hd)
        path = rel if os.path.isabs(rel) else os.path.join(base, rel)
        expected = item.get("sha256", "")
        required = bool(item.get("required"))
        present = exists_fn(path)
        actual = "<missing>"
        if present:
            try:
                actual = digest_fn(path)
            except OSError:
                actual = "<unreadable>"
        if not present:
            status = "MISSING" if required else "OPTIONAL_MISSING"
        elif expected and actual != expected:
            status = "HASH_MISMATCH"
        elif not expected:
            status = "UNPINNED"
        else:
            status = "MATCH"
        results.append({
            "key": key, "path": path, "required": required,
            "expected": expected, "actual": actual, "status": status,
            "used_by": item.get("used_by", ""),
        })
        if status == "MISSING":
            missing_required.append(key)
        elif status == "HASH_MISMATCH":
            hash_mismatch.append(key)
        elif status == "OPTIONAL_MISSING":
            optional_missing.append(key)
    overall = "FAIL" if (missing_required or hash_mismatch) else "PASS"
    return {
        "overall": overall,
        "platform_hd": platform_hd,
        "platform_hs": platform_hs,
        "required_count": sum(1 for r in results if r["required"]),
        "optional_count": sum(1 for r in results if not r["required"]),
        "items": results,
        "missing_required": missing_required,
        "hash_mismatch": hash_mismatch,
        "optional_missing": optional_missing,
    }


def _sha256_or_missing(path: str) -> str:
    if not os.path.isfile(path):
        return "<missing>"
    try:
        return sha256_file(path)
    except OSError:
        return "<missing>"


@dataclass
class EnvironmentProvenance:
    tools: list[ToolPin] = field(default_factory=list)
    flow: dict = field(default_factory=dict)
    pdk: dict = field(default_factory=dict)
    libraries: dict = field(default_factory=dict)
    platforms: dict = field(default_factory=dict)
    #: extraction collateral (SPEF-generating view inputs + tool identity)
    extraction: dict = field(default_factory=dict)
    #: DRC/LVS decks and the reference-netlist generator
    decks: dict = field(default_factory=dict)
    #: resolved overridable settings, their sources and existence validation
    config: dict = field(default_factory=dict)
    host: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "tools": [t.to_dict() for t in self.tools],
            "flow": self.flow,
            "pdk": self.pdk,
            "libraries": self.libraries,
            "platforms": self.platforms,
            "extraction": self.extraction,
            "decks": self.decks,
            "config": self.config,
            "host": self.host,
        }

    def save(self, path: str) -> str:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, sort_keys=True)
        return path


def default_provenance(
    repo_root: str,
    *,
    tools_root: str | None = None,
    config: dict | None = None,
) -> EnvironmentProvenance:
    """Capture the pinned environment used by this repository's regression.

    ``tools_root`` is kept as a backward-compatible API override; prefer the
    documented environment variables (see :func:`resolve_environment_config`).
    """
    prov = EnvironmentProvenance()
    import platform as _platform
    import sys

    cfg = config or resolve_environment_config(tools_root_override=tools_root)
    values = cfg["values"]
    prov.config = cfg

    prov.host = {
        "python": sys.version.split()[0],
        "platform": _platform.platform(),
        "machine": _platform.machine(),
    }

    # Windows-native oss-cad-suite yosys/eqy (needs environment.bat for DLLs).
    oss_env = os.path.join(values["tools_root"], "oss-cad-suite", "environment.bat")
    if os.path.isfile(oss_env):
        # shell=True: nested quotes (call "env.bat" && yosys -V) break
        # list2cmdline quoting for cmd.exe (verified empirically).
        rc, out = _run(
            f'call "{oss_env}" >nul 2>&1 && yosys -V', shell=True
        )
        ver = out.strip().splitlines()[0].strip() if rc == 0 and out.strip() else "<unavailable>"
        yosys_path = os.path.join(values["tools_root"], "oss-cad-suite", "bin", "yosys.exe")
        prov.tools.append(
            ToolPin(
                name="yosys",
                version=ver,
                path=yosys_path,
                sha256=_sha256_or_missing(yosys_path),
                source="cli" if rc == 0 else "missing",
                notes="oss-cad-suite (Windows native); invoked via environment.bat",
            )
        )
        eqy = os.path.join(values["tools_root"], "oss-cad-suite", "bin", "eqy.exe")
        if os.path.isfile(eqy):
            rc2, out2 = _run([eqy, "--version"])
            prov.tools.append(
                ToolPin(
                    name="eqy",
                    version=out2.strip().splitlines()[0].strip() if rc2 == 0 else "<unavailable>",
                    path=eqy,
                    sha256=_sha256_or_missing(eqy),
                    source="cli",
                )
            )

    # WSL-side tools.
    distro = values["wsl_distro"]
    rc, out = probe_wsl(distro, f"{values['openroad_wsl']} -version 2>&1 | head -1")
    if rc == 0 and out.strip():
        prov.tools.append(
            ToolPin(
                name="openroad",
                version=out.strip().splitlines()[0].strip(),
                path=values["openroad_wsl"],
                source="cli (WSL)",
                notes="OpenROAD build reports bazel-nostamp; commit recorded in flow",
            )
        )
    rc, out = probe_wsl(distro, f"{values['klayout_wsl']} -v 2>&1 | head -1")
    if rc == 0 and out.strip():
        prov.tools.append(
            ToolPin(name="klayout", version=out.strip(), path=values["klayout_wsl"], source="cli (WSL)")
        )
    netgen = values["netgen"]
    if os.path.isfile(netgen):
        prov.tools.append(
            ToolPin(
                name="netgen",
                version="<no batch version flag; pinned by binary sha256>",
                path=netgen,
                sha256=_sha256_or_missing(netgen),
                source="sha256",
            )
        )
    # WSL yosys (used by the example test script)
    rc, out = probe_wsl(distro, "yosys -V 2>&1 | head -1")
    if rc == 0 and out.strip():
        prov.tools.append(
            ToolPin(name="yosys_wsl", version=out.strip(), path="/usr/bin/yosys", source="cli (WSL)")
        )

    # ORFS flow pin.
    orfs = values["orfs_dir"]
    if os.path.isdir(os.path.join(orfs, ".git")):
        rc, out = _run(["git", "-C", orfs, "rev-parse", "HEAD"])
        rc2, out2 = _run(["git", "-C", orfs, "status", "--porcelain"])
        prov.flow = {
            "name": "OpenROAD-flow-scripts",
            "path": orfs,
            "commit": out.strip() if rc == 0 else "<unknown>",
            "dirty": bool(out2.strip()) if rc2 == 0 else None,
            "dirty_files": out2.strip().splitlines() if rc2 == 0 else [],
        }
    else:
        prov.flow = {"name": "OpenROAD-flow-scripts", "path": orfs, "commit": "<missing>"}

    # PDK pin (sky130_fd_sc_hd library repo).
    pdk_repo = values["pdk_root"]
    if os.path.isdir(os.path.join(pdk_repo, ".git")):
        rc, out = _run(["git", "-C", pdk_repo, "rev-parse", "HEAD"])
        prov.pdk = {
            "name": "sky130_fd_sc_hd",
            "path": pdk_repo,
            "commit": out.strip() if rc == 0 else "<unknown>",
        }
    else:
        prov.pdk = {"name": "sky130_fd_sc_hd", "path": pdk_repo, "commit": "<missing>"}

    # Liberty libraries actually used by the regression.
    lib_dir = values["lib_dir"]
    for lib in (
        "sky130_fd_sc_hd__tt_025C_1v80.lib",
        "sky130_fd_sc_hd__ss_n40C_1v40.lib",
        "sky130_fd_sc_hd__ff_n40C_1v95.lib",
    ):
        p = os.path.join(lib_dir, lib)
        prov.libraries[lib] = {"path": p, "sha256": _sha256_or_missing(p)}

    # Platform LEF/tech pins.  Keys stay relative-to-ORFS for manifest
    # compatibility; the resolved path honours the platform override.
    plat = values["platform_hd"]
    orfs_root = values["orfs_dir"]
    for rel in (
        os.path.join("flow", "platforms", "sky130hd", "lef", "sky130_fd_sc_hd_merged.lef"),
    ):
        p = os.path.join(orfs_root, rel)
        if not os.path.isfile(p):
            p = os.path.join(plat, "lef", os.path.basename(rel))
        prov.platforms[rel.replace(os.sep, "/")] = {"path": p, "sha256": _sha256_or_missing(p)}

    # Extraction provenance (SR-08): the exact collateral that produces SPEF.
    # OpenROAD is the extraction engine; its identity is recorded in ``tools``
    # and its flow build in ``flow`` (no independent version string available).
    plat_hs = values["platform_hs"]
    prov.extraction = {
        "tool": {
            "name": "openroad (WSL, extract_parasitics + write_spef)",
            "pinned_via": "tools[openroad] + flow[commit]",
        },
        "setRC.tcl": {
            "path": os.path.join(plat, "setRC.tcl"),
            "sha256": _sha256_or_missing(os.path.join(plat, "setRC.tcl")),
        },
        "rcx_patterns.rules": {
            "path": os.path.join(plat_hs, "rcx_patterns.rules"),
            "sha256": _sha256_or_missing(os.path.join(plat_hs, "rcx_patterns.rules")),
            "notes": "sky130hs copy is used; the sky130hd copy is a 30-byte pointer that segfaults OpenROAD",
        },
        "sky130_fd_sc_hd.tlef": {
            "path": os.path.join(plat, "lef", "sky130_fd_sc_hd.tlef"),
            "sha256": _sha256_or_missing(os.path.join(plat, "lef", "sky130_fd_sc_hd.tlef")),
        },
    }

    # Physical-verification decks (SR-08).  Hashes are recorded from the exact
    # files used by stages 14/15, never inferred from a repository tag.
    prov.decks = {
        "cdl/sky130hd.cdl": {
            "path": os.path.join(plat, "cdl", "sky130hd.cdl"),
            "sha256": _sha256_or_missing(os.path.join(plat, "cdl", "sky130hd.cdl")),
        },
        "drc/sky130hd.lydrc": {
            "path": os.path.join(plat, "drc", "sky130hd.lydrc"),
            "sha256": _sha256_or_missing(os.path.join(plat, "drc", "sky130hd.lydrc")),
        },
        "lvs/sky130hd.lylvs": {
            "path": os.path.join(plat, "lvs", "sky130hd.lylvs"),
            "sha256": _sha256_or_missing(os.path.join(plat, "lvs", "sky130hd.lylvs")),
        },
        "sky130hd.lyt": {
            "path": os.path.join(plat, "sky130hd.lyt"),
            "sha256": _sha256_or_missing(os.path.join(plat, "sky130hd.lyt")),
        },
        "regression/stages/15a_lvs_refcdl.tcl": {
            "path": os.path.join(repo_root, "regression", "stages", "15a_lvs_refcdl.tcl"),
            "sha256": _sha256_or_missing(
                os.path.join(repo_root, "regression", "stages", "15a_lvs_refcdl.tcl")
            ),
        },
    }

    return prov
