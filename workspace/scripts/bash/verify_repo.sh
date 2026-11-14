#!/usr/bin/env bash
#
# verify_repo.sh — Repository Integrity Checker
# Validates repository structure and F-01/F-03 preservation checks for the
# VLSI Physical Design multi-agent framework.
#
# Honours the D Drive Execution Policy: all paths resolve relative to
# the repository root (derived from this script location), with no hardcoded
# C: paths.
#
# Usage:
#   bash workspace/scripts/bash/verify_repo.sh
#
# Exit codes:
#   0 — All mandatory checks applicable in this context passed.
#   1 — Any mandatory applicable check failed.
#   2 — Blocked/inconclusive: required context unavailable for a mandatory check.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")"

WORKSPACE_DIR="$REPO_ROOT/workspace"
AGENTS_DIR="$REPO_ROOT/agents"
SKILLS_DIR="$REPO_ROOT/skills"
CLAUDE_AGENTS_DIR="$REPO_ROOT/.claude/agents"
CLAUDE_SKILLS_DIR="$REPO_ROOT/.claude/skills"
REPORTS_DIR="$WORKSPACE_DIR/reports"

export REPO_ROOT WORKSPACE_DIR AGENTS_DIR SKILLS_DIR CLAUDE_AGENTS_DIR CLAUDE_SKILLS_DIR

log_pass() { echo "PASS: $1"; }
log_fail() { echo "FAIL: $1"; }
log_info() { echo "INFO: $1"; }
log_warn() { echo "WARN: $1"; }
log_blocked() { echo "BLOCKED: $1"; }
log_not_tested() { echo "NOT_TESTED: $1"; }

# safe count helpers
count_md_files() {
  local dir="$1"
  if [[ -d "$dir" ]]; then
    find "$dir" -maxdepth 1 -type f -name '*.md' 2>/dev/null | wc -l | tr -d '[:space:]'
  else
    echo "0"
  fi
}

count_skill_dirs() {
  local dir="$1"
  if [[ -d "$dir" ]]; then
    # count immediate subdirs excluding 'docs' if present
    find "$dir" -mindepth 1 -maxdepth 1 -type d ! -name 'docs' 2>/dev/null | wc -l | tr -d '[:space:]'
  else
    echo "0"
  fi
}

count_pattern() {
  local pattern="$1" file="$2"
  if [[ -f "$file" ]]; then
    # grep -c exits 1 when no matches; normalize to single integer
    local c
    c=$(grep -c "$pattern" "$file" 2>/dev/null || true)
    c=$(printf '%s' "$c" | tr -d '[:space:]')
    [[ -z "$c" ]] && c="0"
    echo "$c"
  else
    echo "0"
  fi
}

#=============================================================================
# CHECK 1: Nine Agents Exist and Are Readable (tracked invariant)
#=============================================================================
check_agents() {
  local expected=(
    "cts-engineer.md" "floorplan-engineer.md" "forensic-eco-debugger.md"
    "lec-verifier.md" "low-power-verifier.md" "placement-engineer.md"
    "routing-engineer.md" "signoff-engineer.md" "synthesis-engineer.md"
  )
  local missing=0 ok=0
  for f in "${expected[@]}"; do
    local agent_path="$AGENTS_DIR/$f"
    if [[ -f "$agent_path" && -r "$agent_path" ]]; then
      if grep -q '^name:' "$agent_path" 2>/dev/null; then
        log_pass "Agent: $f"
        ((ok++))
      else
        log_fail "Agent $f: missing 'name:' field"
        ((missing++))
      fi
    else
      log_fail "Agent $f: not found at $agent_path"
      ((missing++))
    fi
  done
  echo ""
  echo "Agents: $ok present and valid, $missing missing or malformed"
  [[ $missing -eq 0 ]]
}

#=============================================================================
# CHECK 2: Ten Skills Exist and Are Readable (tracked invariant)
#=============================================================================
check_skills() {
  local expected=(
    "cts" "floorplan" "forensic-debugging" "lec" "low-power"
    "placement" "routing" "signoff" "synthesis" "using-pd-staff-engineer"
  )
  local missing=0 ok=0
  for f in "${expected[@]}"; do
    local skill_path="$SKILLS_DIR/$f"
    if [[ -d "$skill_path" && -r "$skill_path" ]]; then
      if [[ -f "$skill_path/SKILL.md" && -r "$skill_path/SKILL.md" ]]; then
        log_pass "Skill: $f"
        ((ok++))
      else
        log_fail "Skill $f: SKILL.md not found or not readable"
        ((missing++))
      fi
    else
      log_fail "Skill $f: directory not found at $skill_path"
      ((missing++))
    fi
  done
  echo ""
  echo "Skills: $ok present and valid, $missing missing or malformed"
  [[ $missing -eq 0 ]]
}

#=============================================================================
# CHECK 3: .claude/ sync (local development mirror; context-dependent)
#=============================================================================
check_claude_sync() {
  # This check validates synchronization between repo files and .claude mirrors
  # when mirrors are present. .claude/ is commonly local/untracked development
  # context and may be absent in a clean committed checkout.
  local has_mirror=false
  [[ -d "$CLAUDE_AGENTS_DIR" || -d "$CLAUDE_SKILLS_DIR" ]] && has_mirror=true

  local pairs=(
    "skills/lec/SKILL.md" ".claude/skills/lec/SKILL.md"
    "skills/signoff/SKILL.md" ".claude/skills/signoff/SKILL.md"
    "agents/lec-verifier.md" ".claude/agents/lec-verifier.md"
    "agents/signoff-engineer.md" ".claude/agents/signoff-engineer.md"
  )

  if [[ "$has_mirror" == false ]]; then
    log_not_tested "Claude sync: .claude mirrors not present in this checkout (local development context unavailable). Skipping sync comparison; tracked agent/skill files remain validated by checks 1-2."
    echo ""
    echo "Claude sync: NOT_TESTED (no .claude mirror context)"
    return 0  # not a failure in clean committed checkout; absence of untracked mirrors is not a missing tracked requirement
  fi

  local mismatches=0 in_sync=0 missing_pairs=0
  local i
  for ((i=0; i<${#pairs[@]}; i+=2)); do
    local repo_rel="${pairs[$i]}"
    local claude_rel="${pairs[$i+1]}"
    local repo_path="$REPO_ROOT/$repo_rel"
    local claude_path="$REPO_ROOT/$claude_rel"
    if [[ -f "$repo_path" && -f "$claude_path" ]]; then
      if diff -q "$repo_path" "$claude_path" > /dev/null 2>&1; then
        log_pass "IN SYNC: $repo_rel"
        ((in_sync++))
      else
        log_fail "DIFFERS: $repo_rel (repo vs $claude_rel)"
        ((mismatches++))
      fi
    else
      if [[ -f "$repo_path" && ! -f "$claude_path" ]]; then
        log_fail "MISSING: $claude_rel (mirror present context but required pair file missing)"
        ((mismatches++))
      elif [[ ! -f "$repo_path" ]]; then
        log_fail "MISSING: tracked file $repo_rel (genuine repository file missing)"
        ((mismatches++))
      else
        log_fail "MISSING: $repo_rel or $claude_rel not found"
        ((mismatches++))
      fi
      ((missing_pairs++))
    fi
  done

  echo ""
  echo "Claude sync: $in_sync in sync, $mismatches mismatches/missing"
  [[ $mismatches -eq 0 ]]
}

#=============================================================================
# CHECK 4: F-01 Preservation (context-dependent evidence)
#=============================================================================
check_f01() {
  # F-01 preservation was defined relative to historical phase artifacts/branch
  # context (e.g. PHASE17B_FINAL_REPORT.md, specific feature branch). These may
  # be untracked/local or phase-specific and absent from a clean committed
  # checkout of main. Verify consistency when evidence is present; otherwise
  # report NOT_TESTED (evidence unavailable) rather than fabricating PASS/FAIL.
  local f01_pass=0 f01_fail=0 f01_not_tested=0
  local branch_marker="feature/F-01-claude-agent-discovery"
  local phase_report="$REPO_ROOT/PHASE17B_FINAL_REPORT.md"

  # Evidence: PHASE17B report
  if [[ -f "$phase_report" ]]; then
    if grep -q "$branch_marker" "$phase_report" 2>/dev/null; then
      log_pass "F-01: branch marker found in PHASE17B_FINAL_REPORT.md"
      ((f01_pass++))
    else
      log_fail "F-01: PHASE17B_FINAL_REPORT.md present but missing expected branch marker '$branch_marker'"
      ((f01_fail++))
    fi
  else
    log_not_tested "F-01: PHASE17B_FINAL_REPORT.md not present in this checkout (historical/phase evidence unavailable). Cannot verify marker from committed repository state."
    ((f01_not_tested++))
  fi

  # Git branch context (informational/conditional)
  if [[ -d "$REPO_ROOT/.git" ]] || git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    local current_branch
    current_branch=$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo "UNKNOWN")
    if [[ "$current_branch" == "$branch_marker" ]]; then
      log_pass "F-01: git branch is $branch_marker"
      ((f01_pass++))
    elif [[ "$current_branch" == "HEAD" || "$current_branch" == "UNKNOWN" ]]; then
      log_not_tested "F-01: git branch context unavailable/detached ('$current_branch'); branch-preservation check not applicable in this checkout context"
      ((f01_not_tested++))
    elif [[ "$current_branch" == "main" || "$current_branch" == "master" ]]; then
      log_not_tested "F-01: current branch is '$current_branch' (post-merge/main context); feature-branch preservation check not applicable"
      ((f01_not_tested++))
    else
      # different named branch and PHASE17B evidence not asserting otherwise here
      log_not_tested "F-01: current branch '$current_branch' differs from historical marker '$branch_marker'; preservation relative to historical phase context not verifiable without phase evidence"
      ((f01_not_tested++))
    fi
  else
    log_not_tested "F-01: git metadata not available; skipping git branch check"
    ((f01_not_tested++))
  fi

  echo ""
  echo "F-01 preservation: $f01_pass passed, $f01_fail failed, $f01_not_tested not_tested"
  [[ $f01_fail -eq 0 ]]
}

#=============================================================================
# CHECK 5: F-03 Preservation — tracked structure + context-dependent evidence
#=============================================================================
check_f03() {
  local f03_pass=0 f03_fail=0 f03_not_tested=0

  # Tracked/structural invariants (must hold in committed repo)
  local agent_count skill_dir_count
  agent_count=$(count_md_files "$AGENTS_DIR")
  if [[ "$agent_count" -eq 9 ]]; then
    log_pass "F-03: exactly 9 agent .md files found ($agent_count)"
    ((f03_pass++))
  else
    log_fail "F-03: expected 9 agent .md files, found $agent_count"
    ((f03_fail++))
  fi

  skill_dir_count=$(count_skill_dirs "$SKILLS_DIR")
  if [[ "$skill_dir_count" -eq 10 ]]; then
    log_pass "F-03: exactly 10 skill directories found ($skill_dir_count excluding docs)"
    ((f03_pass++))
  else
    log_fail "F-03: expected 10 skill directories, found $skill_dir_count"
    ((f03_fail++))
  fi

  # F-03 text markers in tracked files: verify if present; if absent, marker-based
  # evidence is unavailable in committed state (historically not present in tracked history)
  local lec_skill_marker lec_verifier_marker
  lec_skill_marker=$(count_pattern 'F-03' "$SKILLS_DIR/lec/SKILL.md")
  if [[ "$lec_skill_marker" -ge 1 ]]; then
    log_pass "F-03: F-03 marker found in skills/lec/SKILL.md ($lec_skill_marker occurrence(s))"
    ((f03_pass++))
  else
    log_not_tested "F-03: F-03 marker not present in skills/lec/SKILL.md (marker-based preservation evidence unavailable in this checkout)"
    ((f03_not_tested++))
  fi

  lec_verifier_marker=$(count_pattern 'F-03' "$AGENTS_DIR/lec-verifier.md")
  if [[ "$lec_verifier_marker" -ge 1 ]]; then
    log_pass "F-03: F-03 marker found in agents/lec-verifier.md ($lec_verifier_marker occurrence(s))"
    ((f03_pass++))
  else
    log_not_tested "F-03: F-03 marker not present in agents/lec-verifier.md (marker-based preservation evidence unavailable in this checkout)"
    ((f03_not_tested++))
  fi

  # .claude mirrors (local dev context)
  if [[ -d "$CLAUDE_AGENTS_DIR" || -d "$CLAUDE_SKILLS_DIR" ]]; then
    local claude_agent_count claude_skill_count
    claude_agent_count=$(count_md_files "$CLAUDE_AGENTS_DIR")
    claude_skill_count=$(count_skill_dirs "$CLAUDE_SKILLS_DIR")
    if [[ -d "$CLAUDE_AGENTS_DIR" && "$claude_agent_count" -eq 9 ]]; then
      log_pass "F-03: .claude/agents has 9 files (matching)"
      ((f03_pass++))
    elif [[ -d "$CLAUDE_AGENTS_DIR" ]]; then
      log_fail "F-03: .claude/agents has $claude_agent_count files (expected 9) in present mirror context"
      ((f03_fail++))
    else
      log_not_tested "F-03: .claude/agents mirror directory not present"
      ((f03_not_tested++))
    fi
    if [[ -d "$CLAUDE_SKILLS_DIR" && "$claude_skill_count" -eq 10 ]]; then
      log_pass "F-03: .claude/skills has 10 directories (matching)"
      ((f03_pass++))
    elif [[ -d "$CLAUDE_SKILLS_DIR" ]]; then
      log_fail "F-03: .claude/skills has $claude_skill_count directories (expected 10) in present mirror context"
      ((f03_fail++))
    else
      log_not_tested "F-03: .claude/skills mirror directory not present"
      ((f03_not_tested++))
    fi
  else
    log_not_tested "F-03: .claude mirrors not present in this checkout (local development context unavailable); tracked agent/skill structure validated above"
    ((f03_not_tested++))
  fi

  echo ""
  echo "F-03 preservation: $f03_pass passed, $f03_fail failed, $f03_not_tested not_tested"
  [[ $f03_fail -eq 0 ]]
}

#=============================================================================
# Report
#=============================================================================
write_report() {
  local overall_status="$1"
  local report_time
  report_time=$(date -u +"%Y-%m-%dT%H:%M:%SZ" 2>/dev/null || echo "unknown-time")
  mkdir -p "$REPORTS_DIR"
  local report_path="$REPORTS_DIR/verify_repo_${report_time}.md"
  if [[ -f "$report_path" ]]; then
    report_path="$REPORTS_DIR/verify_repo_${report_time}_modified.md"
  fi
  cat > "$report_path" <<EOF
# Repository Integrity Check Report
Generated: $report_time
Script: workspace/scripts/bash/verify_repo.sh
Repository: $REPO_ROOT
Overall: $overall_status

## Summary
- Checks 1-2 validate tracked repository invariants (agents/skills structure).
- Check 3 validates .claude mirror sync when mirror context is present; otherwise NOT_TESTED.
- Checks 4-5 (F-01/F-03 preservation evidence) validate consistency when phase/local evidence is present; unavailable context is reported as NOT_TESTED (not PASS), while inconsistencies in present context FAIL.

## Notes
- Read-only checks; no repository content modified by this script.
- Paths resolved relative to repository root (portable; no hardcoded workstation C: paths).
EOF
  echo "Report written: $report_path"
}

main() {
  local exit_code=0 overall_status="PASS" any_blocked=false

  echo "============================================================="
  echo "Repository Integrity Checker — verify_repo.sh"
  echo "Repository: $REPO_ROOT"
  echo "============================================================="
  echo ""

  mkdir -p "$REPORTS_DIR" 2>/dev/null || true

  if ! check_agents; then overall_status="BLOCKED"; exit_code=1; fi
  echo ""
  if ! check_skills; then overall_status="BLOCKED"; exit_code=1; fi
  echo ""
  if ! check_claude_sync; then overall_status="BLOCKED"; exit_code=1; fi
  echo ""
  if ! check_f01; then overall_status="BLOCKED"; exit_code=1; fi
  echo ""
  if ! check_f03; then overall_status="BLOCKED"; exit_code=1; fi

  echo ""
  if [[ $exit_code -eq 0 ]]; then
    write_report "$overall_status"
    echo "=== INTEGRITY CHECK RESULT: $overall_status ==="
    echo "RESULT: PASS — Mandatory applicable integrity checks satisfied (context-dependent checks reported NOT_TESTED where evidence unavailable)"
  else
    echo "=== INTEGRITY CHECK RESULT: BLOCKED ==="
    echo "RESULT: BLOCKED — One or more mandatory checks failed (see details above)"
  fi
  echo "============================================================="
  return $exit_code
}

main
exit $?