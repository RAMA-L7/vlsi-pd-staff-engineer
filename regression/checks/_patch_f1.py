import re

path = r"D:\audit\wt_phase22_B_rtl\regression\checks\acceptance.py"
src = open(path, encoding="utf-8").read()

old_executed = '''def log(stage):
    return os.path.join(LOGDIR, '%s.log' % stage)


def executed(stage):
    """A stage counts as executed when its log file exists."""
    return exists(log(stage), 0)'''

new_executed = '''# Stage-specific log locations (Windows stages write to different dirs)
STAGE_LOG_PATHS = {
    "01_synthesis": ["synth/generic_synth.log", "synth/synth_mapped.log",
                     "logs/01_synthesis.log"],
    "02_lec_generic": ["lec_generic/lec_generic.log",
                       "logs/02_lec_generic.log"],
    "03_lec_mapped": ["eqy/eqy_positive.log", "eqy/eqy_negative.log",
                      "logs/03_lec_mapped.log"],
}


def log(stage):
    return os.path.join(LOGDIR, '%s.log' % stage)


def _stage_log_candidates(stage):
    """Return all possible log file paths for a stage."""
    cands = [os.path.join(RUN, "logs", stage + ".log")]
    for rel in STAGE_LOG_PATHS.get(stage, []):
        cands.append(os.path.join(RUN, *rel.split("/")))
    return cands


def executed(stage):
    """A stage counts as executed when ANY of its known log files exist."""
    for p in _stage_log_candidates(stage):
        if exists(p, 0):
            return True
    return False


def stage_log_path(stage):
    """Return the first existing log path for a stage (for reading)."""
    for p in _stage_log_candidates(stage):
        if exists(p, 0):
            return p
    return os.path.join(LOGDIR, stage + ".log")'''

assert old_executed in src, "old block not found"
src = src.replace(old_executed, new_executed)

# Also fix log() calls in executed() checks - replace log(stage) with stage_log_path(stage)
# in the stage sections. The check functions use log(stage) for reading; update to use
# stage_log_path when available.

open(path, "w", encoding="utf-8", newline="\n").write(src)
print("F-1 patch applied")
