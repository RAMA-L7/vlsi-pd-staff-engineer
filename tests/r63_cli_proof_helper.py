"""R6.3: write a real TRUSTED proof (with its bound command) to a JSON file for CLI tests."""
import json, sys
from pathlib import Path

def write_proof_file(path):
    """Obtain a real TRUSTED proof and persist {**proof, 'command': argv}."""
    import r62_proof_helper as ph
    d, cmd = ph.proof_and_command()
    if d is None:
        return False
    d["command"] = list(cmd)
    Path(path).write_text(json.dumps(d), encoding="utf-8")
    return True
