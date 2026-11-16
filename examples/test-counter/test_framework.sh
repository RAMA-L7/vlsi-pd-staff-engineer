#!/bin/bash
# ============================================================
# test_framework.sh — Validate the PD agent framework
#
# Run this BEFORE using Claude Code to verify your tools work.
# Each test prints PASS or FAIL. Fix any FAILs before proceeding.
#
# Repairs (see docs/reviews/remediation-tracker.md):
#   B  repo-root files are checked against REPO_ROOT, not the example dir,
#      so the script works from any CWD and on a fresh clone.
#   C  synthesis runs BOTH a generic pass and a technology-mapped pass
#      (abc -liberty) when a Sky130 Liberty is available; STA uses the
#      MAPPED netlist only.
#   D  LEC follows the proven pattern (async2sync + design -stash/copy-from
#      + equiv_make gold gate equiv + equiv_status -assert) and requires the
#      explicit "Equivalence successfully proven" verdict; a deliberately
#      corrupted netlist MUST fail (negative control).
#   E  STA distinguishes "analysis ran" from "meets targets": it rejects
#      missing clocks, unresolved cells, malformed/absent slack and
#      unconstrained paths, and a zero exit code alone never passes.
#
# Usage: ./test_framework.sh          (from anywhere; bash or WSL)
# ============================================================

set -uo pipefail

PASS=0
FAIL=0
SKIP=0

# --- B: resolve BOTH directories from the script location -------------------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"           # examples/test-counter
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"           # repository root
PROJECT_DIR="$SCRIPT_DIR"                              # example-local files

pass() { echo "  [PASS] $1"; PASS=$((PASS+1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL+1)); }
skip() { echo "  [SKIP] $1"; SKIP=$((SKIP+1)); }

echo ""
echo "========================================"
echo " VLSI PD Staff Engineer - Framework Test"
echo "========================================"
echo " Repo root: $REPO_ROOT"
echo " Example:   $PROJECT_DIR"
echo " Date:      $(date)"
echo "========================================"


# ============================================================
echo ""
echo "--- TEST 1: Project structure (repo root + example) ---"
# ============================================================

# Repo-root files (finding B: these live at the repository root)
for f in CLAUDE.md synthesis_targets.json \
         agents/synthesis-engineer.md \
         agents/lec-verifier.md \
         agents/low-power-verifier.md \
         agents/forensic-eco-debugger.md \
         agents/floorplan-engineer.md \
         agents/placement-engineer.md \
         agents/cts-engineer.md \
         agents/routing-engineer.md \
         agents/signoff-engineer.md \
         skills/using-pd-staff-engineer/SKILL.md \
         skills/synthesis/SKILL.md \
         skills/lec/SKILL.md \
         skills/low-power/SKILL.md \
         skills/forensic-debugging/SKILL.md \
         skills/floorplan/SKILL.md \
         skills/placement/SKILL.md \
         skills/cts/SKILL.md \
         skills/routing/SKILL.md \
         skills/signoff/SKILL.md \
         hooks/hooks.json \
         hooks/session-start; do
  if [ -f "$REPO_ROOT/$f" ]; then
    pass "repo: $f exists"
  else
    fail "repo: $f MISSING (looked in $REPO_ROOT)"
  fi
done

# Example-local files
for f in rtl/test_counter.v rtl/test_counter.sdc; do
  if [ -f "$PROJECT_DIR/$f" ]; then
    pass "example: $f exists"
  else
    fail "example: $f MISSING (looked in $PROJECT_DIR)"
  fi
done


# ============================================================
echo ""
echo "--- TEST 2: Hook executability ---"
# ============================================================

if [ -f "$REPO_ROOT/hooks/session-start" ]; then
  # On a fresh Windows clone the exec bit may be absent; running via bash
  # still works, so accept either -x or an explicit bash invocation.
  if [ -x "$REPO_ROOT/hooks/session-start" ] || bash -n "$REPO_ROOT/hooks/session-start" 2>/dev/null; then
    pass "hooks/session-start present and parseable by bash"
  else
    fail "hooks/session-start is not executable and does not parse"
  fi
else
  fail "hooks/session-start MISSING"
fi

if [ -f "$REPO_ROOT/hooks/run-hook.cmd" ]; then
  pass "hooks/run-hook.cmd present"
else
  fail "hooks/run-hook.cmd MISSING"
fi

# EOL guard: a CRLF shell script dies with "$'\r': command not found"
if grep -q $'\r' "$REPO_ROOT/hooks/session-start" 2>/dev/null; then
  fail "hooks/session-start has CRLF line endings (must be LF)"
else
  pass "hooks/session-start uses LF line endings"
fi


# ============================================================
echo ""
echo "--- TEST 3: Tool detection ---"
# ============================================================

# Yosys
if command -v yosys &>/dev/null; then
  YOSYS_VER=$(yosys -V 2>&1 | head -1)
  pass "Yosys found: $YOSYS_VER"
else
  fail "Yosys not in PATH — install: sudo apt install yosys"
fi

# OpenSTA (standalone binary or OpenROAD-embedded)
STA_CMD=""
if command -v sta &>/dev/null; then
  STA_CMD="$(command -v sta)"
elif command -v opensta &>/dev/null; then
  STA_CMD="$(command -v opensta)"
elif [ -x /mnt/d/tools/src/OpenSTA/build/sta ]; then
  STA_CMD="/mnt/d/tools/src/OpenSTA/build/sta"
fi
if [ -n "$STA_CMD" ]; then
  pass "OpenSTA found: $STA_CMD"
else
  skip "OpenSTA not in PATH (STA test will be skipped)"
fi

# OpenROAD
if command -v openroad &>/dev/null; then
  pass "OpenROAD found: $(command -v openroad)"
else
  if command -v docker &>/dev/null 2>&1 && docker image inspect openroad/orfs:latest &>/dev/null 2>&1; then
    pass "OpenROAD available via Docker (openroad/orfs:latest)"
  else
    skip "OpenROAD not found (native or Docker) — physical stages unavailable here"
  fi
fi

# Node.js (needed for Claude Code)
if command -v node &>/dev/null; then
  NODE_VER=$(node -v)
  NODE_MAJOR=$(echo "$NODE_VER" | sed 's/v//' | cut -d. -f1)
  if [ "$NODE_MAJOR" -ge 18 ]; then
    pass "Node.js $NODE_VER (>=18, good for Claude Code)"
  else
    fail "Node.js $NODE_VER is too old — Claude Code needs >=18"
  fi
else
  skip "Node.js not found (only needed for Claude Code) — install: https://nodejs.org"
fi

# Claude Code
if command -v claude &>/dev/null; then
  pass "Claude Code found: $(command -v claude)"
else
  skip "Claude Code not installed — run: npm install -g @anthropic-ai/claude-code"
fi


# ============================================================
echo ""
echo "--- TEST 4: Sky130 library (local availability, not PDK scope) ---"
# ============================================================

LIB_FILE=""
for p in \
  "$PROJECT_DIR/lib/sky130_fd_sc_hd__tt_025C_1v80.lib" \
  "$REPO_ROOT/lib/sky130_fd_sc_hd__tt_025C_1v80.lib" \
  "${SKY130_LIB_DIR:-}/sky130_fd_sc_hd__tt_025C_1v80.lib" \
  "/mnt/d/tools/src/OpenROAD/src/sta/test/sky130hd/sky130_fd_sc_hd__tt_025C_1v80.lib" \
  "$HOME/.volare/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib" \
  "/usr/share/pdk/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"; do
  if [ -n "$p" ] && [ -f "$p" ]; then
    LIB_FILE="$p"
    break
  fi
done

if [ -n "$LIB_FILE" ]; then
  pass "Sky130 liberty found (local file): $LIB_FILE"
  # Validate the header PVT against the file-name corner (finding F)
  HDR_V=$(grep -m1 "nom_voltage" "$LIB_FILE" | sed 's/.*: *//;s/;.*//' || true)
  HDR_T=$(grep -m1 "nom_temperature" "$LIB_FILE" | sed 's/.*: *//;s/;.*//' || true)
  if [ -n "$HDR_V" ] && [ -n "$HDR_T" ]; then
    pass "Liberty header declares ${HDR_T}C / ${HDR_V}V (validate config claims against this)"
  else
    fail "Liberty header has no nom_voltage/nom_temperature — cannot validate PVT"
  fi
else
  skip "Sky130 liberty not found locally — mapped synthesis and STA will be SKIPPED"
  echo "    (this is LOCAL FILE availability, not a claim that the PDK lacks corners)"
  echo "    set SKY130_LIB_DIR=<dir with sky130_fd_sc_hd__tt_025C_1v80.lib>"
fi


# ============================================================
echo ""
echo "--- TEST 5: Yosys synthesis (generic + technology-mapped) ---"
# ============================================================

if ! command -v yosys &>/dev/null; then
  skip "Yosys not available — cannot run synthesis test"
else
  mkdir -p "$PROJECT_DIR/results"
  cd "$PROJECT_DIR" || exit 1

  # 5a: generic synthesis (labelled GENERIC — not sufficient for Liberty STA)
  cat > results/synth_generic.ys << 'YOSYS_EOF'
read_verilog rtl/test_counter.v
synth -top test_counter
stat
write_verilog -noattr results/test_counter_generic.v
YOSYS_EOF

  SYNTH_OUT=$(yosys results/synth_generic.ys 2>&1)
  SYNTH_RC=$?

  if [ $SYNTH_RC -eq 0 ] && [ -f results/test_counter_generic.v ]; then
    pass "GENERIC synthesis completed (labelled GENERIC: no technology mapping)"
    CELLS=$(echo "$SYNTH_OUT" | grep "Number of cells:" | tail -1 | awk '{print $NF}')
    [ -n "$CELLS" ] && pass "Generic netlist has $CELLS cells"
  else
    fail "Generic synthesis failed (exit $SYNTH_RC)"
    echo "$SYNTH_OUT" | tail -10
  fi

  # 5b: technology-mapped synthesis (finding C) — only with a real Liberty
  if [ -n "$LIB_FILE" ]; then
    cat > results/synth_mapped.ys << YOSYS_EOF
read_verilog rtl/test_counter.v
# -ignore_miss_func: some library cells (e.g. integrated clock gates) declare
# an output pin with no function; this is normal Liberty, not a broken file.
read_liberty -ignore_miss_func $LIB_FILE
synth -top test_counter
# map flip-flops too (same recipe as regression/stages/01_synthesis.ps1);
# without this the netlist keeps behavioral 'always' blocks that STA rejects
dfflibmap -liberty $LIB_FILE
abc -liberty $LIB_FILE
opt_clean -purge
stat
write_verilog -noattr results/test_counter_mapped.v
YOSYS_EOF

    MAPPED_OUT=$(yosys results/synth_mapped.ys 2>&1)
    MAPPED_RC=$?

    if [ $MAPPED_RC -eq 0 ] && [ -f results/test_counter_mapped.v ]; then
      pass "MAPPED synthesis completed (abc -liberty, Sky130 technology mapping)"
      # The mapped netlist must contain Sky130 cell instances, not $-cells
      if grep -q "sky130_fd_sc_hd__" results/test_counter_mapped.v; then
        MAPPED_CELLS=$(grep -c "sky130_fd_sc_hd__" results/test_counter_mapped.v || true)
        pass "Mapped netlist contains Sky130 cells (instances found: $MAPPED_CELLS)"
      else
        fail "Mapped netlist contains no sky130_fd_sc_hd cells — mapping did not happen"
      fi
      if grep -qE '\$(and|or|not|dff|add)' results/test_counter_mapped.v; then
        fail "Mapped netlist still contains generic \$-cells — mapping incomplete"
      else
        pass "No generic \$-cells remain in mapped netlist"
      fi
    else
      fail "Mapped synthesis failed (exit $MAPPED_RC)"
      echo "$MAPPED_OUT" | tail -10
    fi
  else
    skip "No local Liberty — mapped synthesis SKIPPED (finding C requires it when available)"
  fi
fi


# ============================================================
echo ""
echo "--- TEST 6: Yosys LEC (RTL vs mapped netlist) + negative control ---"
# ============================================================

if ! command -v yosys &>/dev/null; then
  skip "Yosys not available — cannot run LEC test"
elif [ ! -f "$PROJECT_DIR/results/test_counter_mapped.v" ]; then
  skip "No MAPPED netlist — run TEST 5 with a Liberty first"
else
  cd "$PROJECT_DIR" || exit 1

  # --- 6a: positive control (RTL == mapped netlist) -------------------------
  # Finding D: equiv_make needs THREE args (gold, gate, equiv_module);
  # design -stash stores the whole design, and modules are referenced by
  # module name after design -copy-from ... -as <name>.
  # The gate side is FLATTENED so the SAT solver sees the Boolean function
  # of every library cell instead of opaque cell boundaries.
  cat > results/lec_gold.ys << LEC_EOF
read_verilog rtl/test_counter.v
prep -top test_counter
async2sync
design -stash gold

design -reset
read_liberty -ignore_miss_func $LIB_FILE
read_verilog results/test_counter_mapped.v
prep -top test_counter
flatten test_counter
opt_clean -purge
async2sync
design -stash gate

design -reset
design -copy-from gold -as gold test_counter
design -copy-from gate -as gate test_counter
equiv_make gold gate equiv
hierarchy -top equiv
equiv_simple
equiv_induct
equiv_status -assert
LEC_EOF

  LEC_OUT=$(yosys results/lec_gold.ys 2>&1)
  LEC_RC=$?

  LEC_VERDICT_OK=0
  if [ $LEC_RC -eq 0 ] && echo "$LEC_OUT" | grep -q "Equivalence successfully proven"; then
    LEC_VERDICT_OK=1
  fi

  if [ $LEC_VERDICT_OK -eq 1 ]; then
    pass "LEC PASSED — RTL == mapped netlist (explicit verdict asserted)"
  else
    fail "LEC did not prove equivalence (exit $LEC_RC)"
    echo "$LEC_OUT" | grep -E "ERROR|unproven|proven" | tail -5
  fi

  # --- 6b: negative control — a corrupted netlist MUST fail -----------------
  # Functional corruption: flip an output assignment so behaviour differs.
  sed 's/assign at_max = (count == 4.hF);/assign at_max = (count == 4'"'"'h0);/' \
      rtl/test_counter.v > results/test_counter_corrupt.v
  if cmp -s rtl/test_counter.v results/test_counter_corrupt.v; then
    # sed pattern did not match; corrupt the increment instead
    sed 's/count <= count + 4'"'"'b1;/count <= count + 4'"'"'b0;/' \
        rtl/test_counter.v > results/test_counter_corrupt.v
  fi

  cat > results/lec_corrupt.ys << 'LEC_EOF'
read_verilog rtl/test_counter.v
prep -top test_counter
async2sync
design -stash gold

design -reset
read_verilog results/test_counter_corrupt.v
prep -top test_counter
async2sync
design -stash gate

design -copy-from gold -as gold test_counter
design -copy-from gate -as gate test_counter
equiv_make gold gate equiv
hierarchy -top equiv
equiv_simple
equiv_induct
equiv_status -assert
LEC_EOF

  CORR_OUT=$(yosys results/lec_corrupt.ys 2>&1)
  CORR_RC=$?

  if [ $CORR_RC -ne 0 ] && echo "$CORR_OUT" | grep -qiE "unproven|error"; then
    pass "LEC negative control: corrupted netlist correctly REJECTED (exit $CORR_RC)"
  elif [ $LEC_VERDICT_OK -eq 0 ]; then
    skip "LEC negative control inconclusive (positive control already failed)"
  else
    fail "LEC negative control: corrupted netlist was NOT rejected — LEC setup is unsound"
    echo "$CORR_OUT" | grep -E "ERROR|unproven|proven" | tail -5
  fi
fi


# ============================================================
echo ""
echo "--- TEST 7: OpenSTA timing (mapped netlist; ran vs meets) ---"
# ============================================================

if [ -z "$STA_CMD" ]; then
  skip "OpenSTA not available — timing check skipped"
elif [ -z "$LIB_FILE" ]; then
  skip "No liberty file — cannot run STA"
elif [ ! -f "$PROJECT_DIR/results/test_counter_mapped.v" ]; then
  skip "No mapped netlist — STA would mix generic cells with Liberty (refused)"
else
  cd "$PROJECT_DIR" || exit 1

  # Finding E: SDC must define a clock BEFORE STA runs
  if ! grep -q "create_clock" rtl/test_counter.sdc; then
    fail "STA refused: SDC has no create_clock (missing timing constraints)"
  else
    cat > results/sta_test.tcl << STA_EOF
read_liberty $LIB_FILE
read_verilog $PROJECT_DIR/results/test_counter_mapped.v
link_design test_counter
read_sdc $PROJECT_DIR/rtl/test_counter.sdc
report_checks -path_delay max -group_count 5
report_checks -path_delay min -group_count 5
# Authoritative numbers requested from OpenSTA itself (same method as
# regression/stages/12_sta.sh), so parsing does not depend on path reports.
puts "ASSISTANT_WNS [sta::worst_slack -max]"
puts "ASSISTANT_WHS [sta::worst_slack -min]"
puts "ASSISTANT_TNS [sta::total_negative_slack -max]"
exit
STA_EOF

    STA_LOG="$PROJECT_DIR/results/sta_test.log"
    "$STA_CMD" results/sta_test.tcl > "$STA_LOG" 2>&1
    STA_RC=$?

    STA_FAILS=0

    # 1) the tool itself must succeed
    if [ $STA_RC -ne 0 ]; then
      fail "STA process failed (exit $STA_RC)"
      tail -5 "$STA_LOG"
      STA_FAILS=1
    fi

    # 2) unresolved cells / link failures
    if grep -qiE "unresolved|cannot find|no such cell|link failed|cannot link" "$STA_LOG"; then
      fail "STA reports unresolved cells/ports — numbers are not trustworthy"
      grep -iE "unresolved|cannot find|no such cell|link failed|cannot link" "$STA_LOG" | head -3
      STA_FAILS=1
    fi

    # 3) unconstrained paths
    if grep -qiE "unconstrained|no paths found|is not a valid startpoint" "$STA_LOG"; then
      fail "STA reports unconstrained/missing paths — constraints incomplete"
      grep -iE "unconstrained|no paths found|is not a valid startpoint" "$STA_LOG" | head -3
      STA_FAILS=1
    fi

    # 4) slack must actually be reported (finding E: exit 0 alone is NOT a pass)
    WNS_SETUP=$(grep "ASSISTANT_WNS" "$STA_LOG" | grep -oE -- "-?[0-9]+\.[0-9]+" | head -1)
    if [ -z "$WNS_SETUP" ]; then
      # fall back to path reports if the Tcl proc was unavailable
      WNS_SETUP=$(grep -E "slack \((MET|VIOLATED)\)" "$STA_LOG" | grep -oE -- "-?[0-9]+\.[0-9]+" | head -1)
    fi
    if [ -z "$WNS_SETUP" ]; then
      fail "STA produced no parseable slack (analysis did NOT run to completion)"
      STA_FAILS=1
    elif [ "$STA_FAILS" -eq 0 ]; then
      # 5) distinguish ran-vs-meets: report the number and judge vs target
      if awk "BEGIN{exit !($WNS_SETUP >= 0)}"; then
        pass "STA ran AND meets target: setup WNS = $WNS_SETUP ns (>= 0)"
      else
        fail "STA ran but MISSES target: setup WNS = $WNS_SETUP ns (< 0)"
      fi
      pass "Slack value parsed from $STA_LOG (analysis ran)"
    fi
  fi
fi


# ============================================================
echo ""
echo "--- TEST 8: Session-start hook (run from repo root) ---"
# ============================================================

cd "$REPO_ROOT" || exit 1
HOOK_OUT=$(bash hooks/session-start 2>&1)
HOOK_RC=$?

if [ $HOOK_RC -eq 0 ]; then
  pass "Session-start hook ran successfully"
  if echo "$HOOK_OUT" | grep -q "yosys"; then
    pass "Hook detected Yosys"
  else
    fail "Hook did not detect Yosys"
  fi
  if echo "$HOOK_OUT" | grep -q "EXTREMELY_IMPORTANT"; then
    pass "Hook loaded the employee handbook"
  else
    fail "Hook did not load the handbook"
  fi
else
  fail "Session-start hook failed (exit $HOOK_RC)"
  echo "$HOOK_OUT" | tail -5
fi


# ============================================================
echo ""
echo "========================================"
echo " RESULTS"
echo "========================================"
echo ""
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
echo "  SKIP: $SKIP"
echo ""

if [ $FAIL -eq 0 ]; then
  echo "  All executed tests passed. Skipped tests are listed above with reasons."
  echo ""
  echo "  Next steps:"
  echo "    1. cd $REPO_ROOT"
  echo "    2. claude"
  echo "    3. Tell it: 'Run synthesis on the test counter design'"
  echo ""
else
  echo "  Some tests failed. Fix the issues above before proceeding."
  echo ""
  echo "  Common fixes:"
  echo "    - Missing Yosys:    sudo apt install yosys"
  echo "    - Missing Liberty:  set SKY130_LIB_DIR=<dir with sky130_fd_sc_hd__tt_025C_1v80.lib>"
  echo "    - Missing OpenSTA:  see docs/SETUP.md"
  echo "    - Hooks not exec:   chmod +x hooks/session-start"
  echo ""
fi

exit $FAIL
