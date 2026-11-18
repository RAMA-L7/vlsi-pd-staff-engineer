"""Focused regression tests for stage-03 EQY gold-source selection (Milestone 1G).

The supported ``-RtlOverride`` flow makes ``REG_RTL`` point at a candidate RTL.
EQY's automatic matching only pairs top-level ports, so a structurally dissimilar
(but functionally equivalent) candidate produces a deterministic FALSE NEGATIVE
when used as the EQY gold.  Stage 03 now selects the canonical promoted RTL as the
EQY gold for that path while keeping the mapped-netlist negative control intact.

These tests deliberately do NOT run the full regression or prove the whole design.
They exercise the real ``03_eqy_prep.py`` config-generation path on minimal
fabricated PDK inputs and execute the PowerShell selection block in isolation.
"""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
STAGES = REPO / "regression" / "stages"
PREP = STAGES / "03_eqy_prep.py"
PS1 = STAGES / "03_lec_mapped.ps1"
GOLDEN_RTL = REPO / "examples" / "soc_ctrl" / "rtl" / "soc_ctrl.v"

CANDIDATE_RTL = Path(
    r"D:\audit\m1f_evidence_20261117\rtl\candidates\kogge_stone\soc_ctrl.v"
)

# Minimal mapped netlist: one sequential cell (for the negative control) and one
# combinational Sky130 cell.  No escaped bus ports, so prep's bus normaliser is a
# no-op and the test stays focused on gold-source selection.
NETLIST = """\
module soc_ctrl(a, b, cin, sum);
input [3:0] a, b;
input cin;
output [4:0] sum;
wire [3:0] q;
sky130_fd_sc_hd__dfxtp_1 ff0 ( .D(a[0]), .Q(q[0]), .CLK(clk) );
sky130_fd_sc_hd__maj3_1 maj0 ( .A(q[0]), .B(b[0]), .C(cin), .X(sum[0]) );
endmodule
"""

GOLD_MARKER = "# --- EQY gold-source selection (Milestone 1G) ---"
GOLD_PRINT = 'Write-Host "  EQY gold source: $EqyGold"'


def _win_path(p: Path) -> str:
    """Return a Windows-resolvable path string for a Path on either host.

    The tests run under Windows PowerShell even when pytest itself is launched
    from WSL, so every path handed to PowerShell/03_eqy_prep.py must use the
    Windows drive-letter form (SR-07 U7).
    """
    s = str(p).replace("\\", "/")
    if os.name == "nt":
        return s.replace("/", "\\")
    m = re.match(r"^/mnt/([A-Za-z])/(.*)$", s)
    if m:
        return m.group(1).upper() + ":/" + m.group(2)
    return s  # caller passes only paths rooted on a Windows-visible drive


def _norm_path(value: object) -> str:
    """Normalise a path (or a line containing one) for comparison.

    Converts WSL drive mounts anywhere in the text (a report may contain
    several paths) so Windows and WSL spellings compare equal.
    """
    s = str(value).replace("\\", "/")
    s = re.sub(r"/mnt/([A-Za-z])/", lambda m: m.group(1).upper() + ":/", s)
    return s.lower()


def _make_prep_args(tmp_path: Path, *, gold: object = ...):
    """Build a minimal PDK/OSS/netlist tree and return (outdir, argv).

    gold is ... (ellipsis) -> no 6th argument; a string -> append it verbatim.
    """
    out = tmp_path / "out"
    oss = tmp_path / "oss"
    pdk = tmp_path / "pdk"
    spm = oss / "examples" / "eqy" / "spm"
    models = pdk / "models"
    cells = pdk / "cells"
    for d in (out, spm, models, cells):
        d.mkdir(parents=True, exist_ok=True)

    (models / "sky130_fd_sc_hd__udp_mux_2to1.v").write_text(
        "primitive sky130_fd_sc_hd__udp_mux_2to1 (Y, A, B, S);\n"
        "  output Y; input A, B, S;\n"
        "endprimitive\n",
        encoding="utf-8",
    )
    for base, cell in (
        ("dfxtp", "sky130_fd_sc_hd__dfxtp_1"),
        ("maj3", "sky130_fd_sc_hd__maj3_1"),
    ):
        (cells / base).mkdir(exist_ok=True)
        (cells / base / (cell + ".v")).write_text(
            "module %s (Q, X, A, B, C, D, CLK);\n"
            "  output Q, X; input A, B, C, D, CLK;\n"
            "endmodule\n" % cell,
            encoding="utf-8",
        )
    (spm / "primitives.v").write_text(
        "primitive p0 (Y, A); output Y; input A; endprimitive\n", encoding="utf-8"
    )
    (spm / "formal_pdk_proc.py").write_text(
        "import sys\n"
        "out = sys.argv[sys.argv.index('-o') + 1]\n"
        "open(out, 'w').write('// stub formal pdk\\n')\n",
        encoding="utf-8",
    )
    netlist = tmp_path / "netlist.v"
    netlist.write_text(NETLIST, encoding="utf-8")
    rtl = tmp_path / "candidate.v"
    rtl.write_text("module soc_ctrl(input clk); endmodule\n", encoding="utf-8")

    args = [
        sys.executable,
        str(PREP),
        str(out),
        str(pdk),
        str(rtl),
        str(netlist),
        str(oss),
    ]
    if gold is not ...:
        args.append(gold)
    return out, args


def _run(args):
    return subprocess.run(args, capture_output=True, text=True)


def _section_line(cfg: Path, section: str) -> str:
    text = cfg.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^\[%s\]\s*\n([^\n]*)$" % section, text, re.M)
    assert match, "no [%s] section in %s" % (section, cfg)
    return match.group(1).strip()


def _ps_selection(tmp_path: Path, repo: Path, rtl: Path, design: str = "soc_ctrl"):
    """Execute only the gold-source selection block from the stage-03 ps1."""
    src = PS1.read_text(encoding="utf-8", errors="replace")
    start = src.index(GOLD_MARKER)
    end = src.index(GOLD_PRINT, start) + len(GOLD_PRINT)
    block = src[start:end]
    # Use -Command instead of -File: WSL's /tmp/... script path cannot be
    # resolved by Windows PowerShell.  Windows PowerShell launched from WSL
    # does not reliably inherit subprocess `env` overrides, so the two paths
    # the selection block reads are injected as literal env assignments in the
    # script itself.  Both are Windows-resolvable (SR-07 U7).
    script = (
        "$env:REG_RTL = '%s'\n$env:REG_REPO = '%s'\n$Des = '%s'\n"
        % (_win_path(rtl), _win_path(repo), design)
    ) + block + (
        "\nWrite-Output ('EQYGOLD=' + $EqyGold)\n"
        "Write-Output ('EQYGOLDWSL=' + $EqyGoldWsl)\n"
    )
    return subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
    )


# --------------------------------------------------------------------------- #
# 03_eqy_prep.py : explicit gold source and backward compatibility
# --------------------------------------------------------------------------- #

def test_prep_honours_explicit_eqy_gold(tmp_path):
    out, args = _make_prep_args(tmp_path, gold=str(GOLDEN_RTL))
    result = _run(args)
    assert result.returncode == 0, result.stdout + result.stderr
    gold_line = _section_line(out / "soc_ctrl.eqy", "gold")
    assert _norm_path(GOLDEN_RTL) in _norm_path(gold_line)
    assert "candidate.v" not in gold_line
    assert "EQY gold source:" in result.stdout
    assert _norm_path(GOLDEN_RTL) in _norm_path(result.stdout)


def test_prep_defaults_gold_to_rtl_when_no_sixth_arg(tmp_path):
    out, args = _make_prep_args(tmp_path)
    result = _run(args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "candidate.v" in _section_line(out / "soc_ctrl.eqy", "gold")


def test_prep_blank_gold_arg_falls_back_to_rtl(tmp_path):
    out, args = _make_prep_args(tmp_path, gold="")
    result = _run(args)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "candidate.v" in _section_line(out / "soc_ctrl.eqy", "gold")


def test_prep_negative_control_still_corrupts_gate_not_gold(tmp_path):
    """Switching the gold source must not weaken the negative control."""
    out, args = _make_prep_args(tmp_path, gold=str(GOLDEN_RTL))
    result = _run(args)
    assert result.returncode == 0, result.stdout + result.stderr
    pos = out / "soc_ctrl.eqy"
    neg = out / "soc_ctrl_broken.eqy"
    assert _section_line(pos, "gold") == _section_line(neg, "gold")
    assert "_broken.v" in _section_line(neg, "gate")
    assert "_broken.v" not in _section_line(pos, "gate")


# --------------------------------------------------------------------------- #
# 03_lec_mapped.ps1 : canonical-gold selection under -RtlOverride
# --------------------------------------------------------------------------- #

def test_stage03_switches_to_canonical_gold_on_override(tmp_path):
    result = _ps_selection(tmp_path, REPO, CANDIDATE_RTL)
    assert result.returncode == 0, result.stderr
    assert "-RtlOverride detected" in result.stdout
    m = re.search(r"EQYGOLD=(.*)", result.stdout)
    assert m and _norm_path(m.group(1).strip()) == _norm_path(GOLDEN_RTL)
    # Convert-ToWslPath maps D:/... (or D:\...) -> /mnt/d/... on either host.
    # _win_path() returns backslashes under native Windows pytest, so normalise
    # separators before deriving the expected WSL path.
    win = _win_path(GOLDEN_RTL).replace("\\", "/")
    expected_wsl = "/mnt/" + win[0].lower() + win[2:]
    mw = re.search(r"EQYGOLDWSL=(.*)", result.stdout)
    assert mw and mw.group(1).strip().replace("\\", "/") == expected_wsl


def test_stage03_keeps_rtl_when_no_override(tmp_path):
    result = _ps_selection(tmp_path, REPO, GOLDEN_RTL)
    assert result.returncode == 0, result.stderr
    assert "-RtlOverride detected" not in result.stdout
    m = re.search(r"EQYGOLD=(.*)", result.stdout)
    assert m and _norm_path(m.group(1).strip()) == _norm_path(GOLDEN_RTL)


def test_stage03_equivalence_gates_are_not_weakened():
    src = PS1.read_text(encoding="utf-8", errors="replace")
    for needle in (
        "if ($rcPos -ne 0)",
        "if ($rcNeg -eq 0)",
        "Successfully proved designs equivalent",
        "exit 132",
        "exit 133",
    ):
        assert needle in src, "missing gate: %s" % needle
    prep = PREP.read_text(encoding="utf-8", errors="replace")
    assert "cannot create negative control" in prep
    # The fix selects a gold source; it must not add black-boxing or undef-to-anyseq.
    assert "setundef -anyseq" not in prep
    assert "(* blackbox *)" not in prep
    assert "(* noblackbox *)" in prep  # existing wrappers retained
    assert "EQY_GOLD = sys.argv[6]" in prep


def test_equivalence_precondition_artifacts_are_the_expected_ones():
    """Pin the candidate/golden identities the accommodation relies upon."""
    assert GOLDEN_RTL.is_file()
    assert hashlib.sha256(GOLDEN_RTL.read_bytes()).hexdigest().startswith(
        "2a7e028dacd97b4"
    )
    if not CANDIDATE_RTL.is_file():
        pytest.skip("1F candidate evidence not present on this host")
    assert hashlib.sha256(CANDIDATE_RTL.read_bytes()).hexdigest().startswith(
        "12e0425c5f6638e"
    )
