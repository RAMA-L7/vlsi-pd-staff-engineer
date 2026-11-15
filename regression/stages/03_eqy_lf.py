"""
Phase 14 regression - run EQY with LF line endings on the generated files.

EQY's Python code writes its makefile and strategy scripts with the platform
default newline translation, which on Windows produces CRLF.  GNU make then
passes the trailing CR into the shell/yosys invocation and the run aborts with
    Can't guess frontend for input file `run.ys'

This wrapper forces LF for every file that EQY writes.  Nothing else is
changed.  Identical to the Phase 13 validated wrapper.
"""
import builtins
import io
import os
import runpy
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EQY_PY = os.path.join(HERE, 'eqy-script.py')

_orig_open = io.open


def _lf_open(file, mode='r', buffering=-1, encoding=None, errors=None,
             newline=None, closefd=True, opener=None):
    if newline is None and 'b' not in mode and any(c in mode for c in 'wax'):
        newline = '\n'
    return _orig_open(file, mode, buffering, encoding, errors, newline,
                      closefd, opener)


io.open = _lf_open
builtins.open = _lf_open

sys.argv = [EQY_PY] + sys.argv[1:]
runpy.run_path(EQY_PY, run_name='__main__')
