"""Tool discovery, version pinning and provenance capture.

All probes are deterministic shell-free lookups where possible; versions are
recorded exactly as the tool reports them (plus a binary hash fallback for
tools whose CLI does not support a version flag in batch mode).
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


@dataclass
class EnvironmentProvenance:
    tools: list[ToolPin] = field(default_factory=list)
    flow: dict = field(default_factory=dict)
    pdk: dict = field(default_factory=dict)
    libraries: dict = field(default_factory=dict)
    platforms: dict = field(default_factory=dict)
    host: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "tools": [t.to_dict() for t in self.tools],
            "flow": self.flow,
            "pdk": self.pdk,
            "libraries": self.libraries,
            "platforms": self.platforms,
            "host": self.host,
        }

    def save(self, path: str) -> str:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, sort_keys=True)
        return path


def default_provenance(repo_root: str, *, tools_root: str = r"D:\tools") -> EnvironmentProvenance:
    """Capture the pinned environment used by this repository's regression."""
    prov = EnvironmentProvenance()
    import platform as _platform
    import sys

    prov.host = {
        "python": sys.version.split()[0],
        "platform": _platform.platform(),
        "machine": _platform.machine(),
    }

    # Windows-native oss-cad-suite yosys/eqy (needs environment.bat for DLLs).
    oss_env = os.path.join(tools_root, "oss-cad-suite", "environment.bat")
    if os.path.isfile(oss_env):
        # shell=True: nested quotes (call "env.bat" && yosys -V) break
        # list2cmdline quoting for cmd.exe (verified empirically).
        rc, out = _run(
            f'call "{oss_env}" >nul 2>&1 && yosys -V', shell=True
        )
        ver = out.strip().splitlines()[0].strip() if rc == 0 and out.strip() else "<unavailable>"
        yosys_path = os.path.join(tools_root, "oss-cad-suite", "bin", "yosys.exe")
        prov.tools.append(
            ToolPin(
                name="yosys",
                version=ver,
                path=yosys_path,
                sha256=sha256_file(yosys_path) if os.path.isfile(yosys_path) else "",
                source="cli" if rc == 0 else "missing",
                notes="oss-cad-suite (Windows native); invoked via environment.bat",
            )
        )
        eqy = os.path.join(tools_root, "oss-cad-suite", "bin", "eqy.exe")
        if os.path.isfile(eqy):
            rc2, out2 = _run([eqy, "--version"])
            prov.tools.append(
                ToolPin(
                    name="eqy",
                    version=out2.strip().splitlines()[0].strip() if rc2 == 0 else "<unavailable>",
                    path=eqy,
                    sha256=sha256_file(eqy),
                    source="cli",
                )
            )

    # WSL-side tools.
    distro = "Ubuntu-24.04"
    rc, out = probe_wsl(distro, "/usr/local/bin/openroad -version 2>&1 | head -1")
    if rc == 0 and out.strip():
        or_path = "/usr/local/bin/openroad"
        prov.tools.append(
            ToolPin(
                name="openroad",
                version=out.strip().splitlines()[0].strip(),
                path=or_path,
                source="cli (WSL)",
                notes="OpenROAD build reports bazel-nostamp; commit recorded in flow",
            )
        )
    rc, out = probe_wsl(distro, "/mnt/d/tools/KLayout/bin/klayout.sh -v 2>&1 | head -1")
    if rc == 0 and out.strip():
        prov.tools.append(
            ToolPin(name="klayout", version=out.strip(), path="/mnt/d/tools/KLayout", source="cli (WSL)")
        )
    netgen = os.path.join(tools_root, "netgen", "bin", "netgen")
    if os.path.isfile(netgen):
        prov.tools.append(
            ToolPin(
                name="netgen",
                version="<no batch version flag; pinned by binary sha256>",
                path=netgen,
                sha256=sha256_file(netgen),
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
    orfs = os.environ.get("ORFS_DIR") or os.path.join(
        r"D:\rta-real-world-benchmark\02_projects", "OpenROAD-flow-scripts"
    )
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
    pdk_repo = os.path.join(tools_root, "pdk", "skywater-pdk-complete",
                            "skywater-pdk-libs-sky130_fd_sc_hd")
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
    lib_dir = os.environ.get(
        "REG_LIB_DIR", os.path.join(tools_root, "src", "OpenROAD", "src", "sta", "test", "sky130hd")
    )
    for lib in (
        "sky130_fd_sc_hd__tt_025C_1v80.lib",
        "sky130_fd_sc_hd__ss_n40C_1v40.lib",
        "sky130_fd_sc_hd__ff_n40C_1v95.lib",
    ):
        p = os.path.join(lib_dir, lib)
        prov.libraries[lib] = (
            {"path": p, "sha256": sha256_file(p)} if os.path.isfile(p) else {"path": p, "sha256": "<missing>"}
        )

    # Platform LEF/tech pins.
    plat = prov.flow.get("path", "")
    for rel in (
        os.path.join("flow", "platforms", "sky130hd", "lef", "sky130_fd_sc_hd_merged.lef"),
    ):
        p = os.path.join(plat, rel)
        prov.platforms[rel.replace(os.sep, "/")] = (
            {"path": p, "sha256": sha256_file(p)} if os.path.isfile(p) else {"path": p, "sha256": "<missing>"}
        )

    return prov
