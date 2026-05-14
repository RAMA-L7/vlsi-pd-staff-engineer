#!/bin/bash
# ============================================================
# test_framework.sh — Validate the PD agent framework
#
# Run this BEFORE using Claude Code to verify your tools work.
# Each test prints PASS or FAIL. Fix any FAILs before proceeding.
#
# Usage: chmod +x test_framework.sh && ./test_framework.sh
# ============================================================

set -uo pipefail

PASS=0
FAIL=0
SKIP=0
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"

pass() { echo "  ✓ PASS: $1"; PASS=$((PASS+1)); }
fail() { echo "  ✗ FAIL: $1"; FAIL=$((FAIL+1)); }
skip() { echo "  ○ SKIP: $1"; SKIP=$((SKIP+1)); }

echo ""
echo "========================================"
echo " VLSI PD Staff Engineer — Framework Test"
echo "========================================"
echo " Project: $PROJECT_DIR"
echo " Date:    $(date)"
echo "========================================"


# ============================================================
echo ""
echo "--- TEST 1: Project structure ---"
# ============================================================

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
         hooks/session-start \
         rtl/test_counter.v \
         rtl/test_counter.sdc; do
  if [ -f "$PROJECT_DIR/$f" ]; then
    pass "$f exists"
  else
    fail "$f MISSING"
  fi
done


# ============================================================
echo ""
echo "--- TEST 2: Hook executability ---"
# ============================================================

if [ -x "$PROJECT_DIR/hooks/session-start" ]; then
  pass "hooks/session-start is executable"
else
  fail "hooks/session-start is not executable — run: chmod +x hooks/session-start"
fi

if [ -x "$PROJECT_DIR/hooks/run-hook.cmd" ]; then
  pass "hooks/run-hook.cmd is executable"
else
  fail "hooks/run-hook.cmd is not executable — run: chmod +x hooks/run-hook.cmd"
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

# OpenSTA
if command -v sta &>/dev/null; then
  pass "OpenSTA found: $(which sta)"
elif command -v opensta &>/dev/null; then
  pass "OpenSTA found: $(which opensta)"
else
  skip "OpenSTA not in PATH (can use Yosys STA or Docker)"
fi

# OpenROAD
if command -v openroad &>/dev/null; then
  pass "OpenROAD found: $(which openroad)"
else
  # Check if Docker has it
  if command -v docker &>/dev/null; then
    if docker image inspect openroad/orfs:latest &>/dev/null 2>&1; then
      pass "OpenROAD available via Docker (openroad/orfs:latest)"
    else
      skip "OpenROAD not native, Docker image not pulled"
    fi
  else
    fail "OpenROAD not found (native or Docker)"
  fi
fi

# Node.js (needed for Claude Code)
if command -v node &>/dev/null; then
  NODE_VER=$(node -v)
  NODE_MAJOR=$(echo "$NODE_VER" | sed 's/v//' | cut -d. -f1)
  if [ "$NODE_MAJOR" -ge 18 ]; then
    pass "Node.js $NODE_VER (≥18, good for Claude Code)"
  else
    fail "Node.js $NODE_VER is too old — Claude Code needs ≥18"
  fi
else
  fail "Node.js not found — install: https://nodejs.org"
fi

# Claude Code
if command -v claude &>/dev/null; then
  pass "Claude Code found: $(which claude)"
else
  skip "Claude Code not installed — run: npm install -g @anthropic-ai/claude-code"
fi


# ============================================================
echo ""
echo "--- TEST 4: Sky130 library ---"
# ============================================================

# Search common locations
LIB_FILE=""
for p in \
  "$PROJECT_DIR/lib/sky130_fd_sc_hd__tt_025C_1v80.lib" \
  "$HOME/.volare/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib" \
  "$HOME/skywater-pdk/libraries/sky130_fd_sc_hd/latest/timing/sky130_fd_sc_hd__tt_025C_1v80.lib" \
  "/usr/share/pdk/sky130A/libs.ref/sky130_fd_sc_hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"; do
  if [ -f "$p" ]; then
    LIB_FILE="$p"
    break
  fi
done

if [ -n "$LIB_FILE" ]; then
  pass "Sky130 liberty found: $LIB_FILE"
  # Copy to project lib/ if not already there
  if [ "$LIB_FILE" != "$PROJECT_DIR/lib/sky130_fd_sc_hd__tt_025C_1v80.lib" ]; then
    mkdir -p "$PROJECT_DIR/lib"
    cp "$LIB_FILE" "$PROJECT_DIR/lib/"
    pass "Copied liberty to project lib/ directory"
  fi
else
  fail "Sky130 liberty file not found anywhere"
  echo "    You need sky130_fd_sc_hd__tt_025C_1v80.lib"
  echo "    Get it from: https://github.com/efabless/skywater-pdk-libs-sky130_fd_sc_hd"
  echo "    Or extract from Docker: docker cp \$(docker create openroad/orfs:latest):/OpenROAD-flow-scripts/flow/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib ./lib/"
fi


# ============================================================
echo ""
echo "--- TEST 5: Yosys synthesis (the real test) ---"
# ============================================================

if ! command -v yosys &>/dev/null; then
  skip "Yosys not available — cannot run synthesis test"
else
  mkdir -p "$PROJECT_DIR/results"
  
  SYNTH_SCRIPT="$PROJECT_DIR/results/synth_test.ys"
  cat > "$SYNTH_SCRIPT" << 'YOSYS_EOF'
read_verilog rtl/test_counter.v
synth -top test_counter
stat
write_verilog -noattr results/test_counter_synth.v
YOSYS_EOF

  cd "$PROJECT_DIR"
  SYNTH_OUT=$(yosys "$SYNTH_SCRIPT" 2>&1)
  SYNTH_RC=$?
  
  if [ $SYNTH_RC -eq 0 ]; then
    pass "Yosys synthesis completed (exit 0)"
    
    # Extract cell count
    CELLS=$(echo "$SYNTH_OUT" | grep "Number of cells:" | tail -1 | awk '{print $NF}')
    if [ -n "$CELLS" ]; then
      pass "Synthesized to $CELLS cells"
    fi
    
    # Check netlist was written
    if [ -f "$PROJECT_DIR/results/test_counter_synth.v" ]; then
      pass "Gate-level netlist written to results/test_counter_synth.v"
    else
      fail "Netlist file not created"
    fi
  else
    fail "Yosys synthesis failed (exit $SYNTH_RC)"
    echo "$SYNTH_OUT" | tail -10
  fi
fi


# ============================================================
echo ""
echo "--- TEST 6: Yosys LEC (equiv_check) ---"
# ============================================================

if ! command -v yosys &>/dev/null; then
  skip "Yosys not available — cannot run LEC test"
elif [ ! -f "$PROJECT_DIR/results/test_counter_synth.v" ]; then
  skip "No synthesis netlist — skipping LEC"
else
  LEC_SCRIPT="$PROJECT_DIR/results/lec_test.ys"

  # Find a cell model for LEC — check if synthesis used a tech lib
  # For generic synth (no liberty), equiv check works against RTL directly
  cat > "$LEC_SCRIPT" << 'LEC_EOF'
# Reference: RTL
read_verilog rtl/test_counter.v
prep -top test_counter
design -save reference

# Revised: synthesized netlist
design -reset
read_verilog results/test_counter_synth.v
prep -top test_counter
design -save revised

# Run equivalence check
equiv_make reference revised
equiv_simple
equiv_induct
equiv_status -assert
LEC_EOF

  cd "$PROJECT_DIR"
  LEC_OUT=$(yosys "$LEC_SCRIPT" 2>&1)
  LEC_RC=$?
  
  if [ $LEC_RC -eq 0 ]; then
    EQUIV=$(echo "$LEC_OUT" | grep "Equivalence successfully proven" || true)
    if [ -n "$EQUIV" ]; then
      pass "LEC passed — RTL ≡ synthesized netlist"
    else
      # Check for equiv_status results
      PROVEN=$(echo "$LEC_OUT" | grep "proven" | tail -1 || true)
      if [ -n "$PROVEN" ]; then
        pass "LEC completed: $PROVEN"
      else
        fail "LEC completed but equivalence not confirmed"
      fi
    fi
  else
    # equiv_status -assert exits non-zero if not all proven
    UNPROVEN=$(echo "$LEC_OUT" | grep "unproven" || true)
    if [ -n "$UNPROVEN" ]; then
      fail "LEC FAILED — non-equivalent points found: $UNPROVEN"
    else
      fail "LEC failed (exit $LEC_RC)"
      echo "$LEC_OUT" | tail -5
    fi
  fi
fi


# ============================================================
echo ""
echo "--- TEST 7: OpenSTA timing (if available) ---"
# ============================================================

STA_CMD=""
if command -v sta &>/dev/null; then
  STA_CMD="sta"
elif command -v opensta &>/dev/null; then
  STA_CMD="opensta"
fi

if [ -z "$STA_CMD" ]; then
  skip "OpenSTA not available — timing check skipped"
  echo "    (Claude Code can still use Yosys for basic timing)"
elif [ -z "$LIB_FILE" ]; then
  skip "No liberty file — cannot run STA"
else
  STA_SCRIPT="$PROJECT_DIR/results/sta_test.tcl"
  cat > "$STA_SCRIPT" << STA_EOF
read_liberty $PROJECT_DIR/lib/sky130_fd_sc_hd__tt_025C_1v80.lib
read_verilog $PROJECT_DIR/results/test_counter_synth.v
link_design test_counter
read_sdc $PROJECT_DIR/rtl/test_counter.sdc
report_checks -path_delay max -group_count 5
report_checks -path_delay min -group_count 5
exit
STA_EOF

  cd "$PROJECT_DIR"
  STA_OUT=$($STA_CMD "$STA_SCRIPT" 2>&1)
  STA_RC=$?
  
  if [ $STA_RC -eq 0 ]; then
    WNS=$(echo "$STA_OUT" | grep "slack" | head -1 || true)
    if [ -n "$WNS" ]; then
      pass "STA completed: $WNS"
    else
      pass "STA completed (check results/sta_test.tcl for details)"
    fi
  else
    fail "STA failed (exit $STA_RC)"
    echo "$STA_OUT" | tail -5
  fi
fi


# ============================================================
echo ""
echo "--- TEST 8: Session-start hook ---"
# ============================================================

cd "$PROJECT_DIR"
HOOK_OUT=$(bash hooks/session-start 2>&1)
HOOK_RC=$?

if [ $HOOK_RC -eq 0 ]; then
  pass "Session-start hook ran successfully"
  
  # Check if it detected tools
  if echo "$HOOK_OUT" | grep -q "yosys"; then
    pass "Hook detected Yosys"
  else
    fail "Hook did not detect Yosys"
  fi
  
  # Check if it found the handbook
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
  echo "  All tests passed! Framework is ready."
  echo ""
  echo "  Next steps:"
  echo "    1. cd $PROJECT_DIR"
  echo "    2. claude"
  echo "    3. Tell it: 'Run synthesis on the test counter design'"
  echo ""
else
  echo "  Some tests failed. Fix the issues above before proceeding."
  echo ""
  echo "  Common fixes:"
  echo "    - Missing Yosys:    sudo apt install yosys"
  echo "    - Missing Node.js:  curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash - && sudo apt install -y nodejs"
  echo "    - Missing Claude:   npm install -g @anthropic-ai/claude-code"
  echo "    - Missing Sky130:   See the docker cp command in TEST 4 above"
  echo "    - Hooks not exec:   chmod +x hooks/session-start hooks/run-hook.cmd"
  echo ""
fi

exit $FAIL
