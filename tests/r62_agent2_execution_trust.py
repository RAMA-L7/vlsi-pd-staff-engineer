"""R6.2 Agent 2 regressions -- structural execution trust boundary.

ADR §6 + AMENDMENT 1 (A1 frozen image manifest, A2 tool-spec allowlist,
A3 nonce/digest replay binding).  Run:

    <PY> -m pytest tests/r62_agent2_execution_trust.py -q
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

import pytest

from assistant import execution_trust as et
from assistant import r61_identity as ident
from assistant import runner

PY = sys.executable
UTC = lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class _TestTrust:
    """Trust root with a fixed in-memory key (mirrors trust.py canonical MAC)."""

    def __init__(self):
        self._key = b"r62-agent2-regression-key"
        self.context = {
            "session_id": "r62-agent2", "repo_id": "r62-agent2",
            "revision": "r62-agent2", "created_utc": UTC(),
        }
        self.ephemeral = False

    def _blob(self, domain, payload):
        return json.dumps(
            {"domain": domain, "payload": dict(payload)},
            sort_keys=True, separators=(",", ":"), default=str,
        ).encode("utf-8")

    def sign(self, domain, payload):
        return hmac.new(
            self._key, self._blob(domain, payload), hashlib.sha256
        ).hexdigest()

    def verify(self, domain, payload, mac):
        try:
            return hmac.compare_digest(self.sign(domain, payload), str(mac))
        except Exception:
            return False


@pytest.fixture(scope="module")
def boundary():
    return et.ExecutionBoundary(_TestTrust())


def _log(name):
    return os.path.join(tempfile.mkdtemp(prefix="r62_a2_"), "logs")


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 1. benign command stays TRUSTED
# ---------------------------------------------------------------------------


def test_benign_command_stays_trusted(boundary):
    result, proof = boundary.run([PY, "-c", "pass"])
    assert proof.state is et.ExecutionState.TRUSTED, proof.reason
    assert proof.approved_images_match is True
    assert proof.root_job_member is True
    assert proof.job_member_count >= 1
    assert len(proof.approved_images) >= 1
    assert str(PY) in [p for p, _ in proof.approved_images]
    assert proof.verify_mac(boundary.trust)
    assert result.exit_code == 0
    assert result.authorization_safe is True


def test_benign_run_managed_attaches_trusted_proof():
    result = runner.run_managed(
        [PY, "-c", "pass"], log_dir=_log("benign"), name="r62-benign",
        deadline=time.monotonic() + 20.0,
    )
    assert result.proof is not None
    assert result.proof.state is et.ExecutionState.TRUSTED, result.proof.reason
    assert result.execution_proof["state"] == "TRUSTED"
    assert result.authorization_safe is True


# ---------------------------------------------------------------------------
# 2. renamed cmd.exe UNTRUSTED (A2 allowlist + A1 frozen manifest)
# ---------------------------------------------------------------------------


def test_renamed_cmd_via_arbitrary_argv_is_untrusted(tmp_path):
    """The P0-C shape: framework interpreter with a `-c` payload spawning a
    renamed cmd.exe.  The argv is not in the signed tool-spec allowlist, so the
    proof is UNTRUSTED and authorization is impossible."""
    shell = tmp_path / "tool.exe"
    if os.path.isfile(r"C:\Windows\System32\cmd.exe"):
        shutil.copy2(r"C:\Windows\System32\cmd.exe", shell)
    else:  # pragma: no cover - portable fallback
        shell.write_bytes(b"MZ")
    sentinel = tmp_path / "sentinel.txt"
    inner = (
        "import subprocess;subprocess.run([%r,'/c','echo P0C > %%s'])" % str(shell)
        % str(sentinel)
    )
    cmd = [PY, "-c", inner]
    result = runner.run_managed(
        cmd, log_dir=_log("renamed"), name="r62-renamed",
        deadline=time.monotonic() + 20.0,
    )
    assert result.proof is not None
    assert result.proof.state is et.ExecutionState.UNTRUSTED, result.proof.reason
    assert "allowlist" in result.proof.reason
    assert result.authorization_safe is False


def test_copied_shell_hash_not_in_frozen_manifest(boundary, tmp_path, monkeypatch):
    """A1: copying a shell inside/near the roots and recording its hash at run
    time must NOT make it approved.  The frozen manifest is never extended."""
    src = r"C:\Windows\System32\cmd.exe"
    if not os.path.isfile(src):  # pragma: no cover
        pytest.skip("cmd.exe unavailable")
    copy = tmp_path / "python_copy.exe"
    shutil.copy2(src, copy)
    digest = _sha(str(copy))
    assert boundary.image_in_manifest(str(copy), digest) is False
    # An allowlisted argv with an injected copied-shell descendant must fail
    # closed: the descendant is readable but not in the frozen manifest.
    monkeypatch.setattr(
        ident, "DESCENDANTS_FN",
        lambda _pid: [{"pid": 424242, "image": str(copy), "error": None}],
    )
    result = runner.run_managed(
        [PY, "-c", "pass"], log_dir=_log("copyshell"), name="r62-copyshell",
        deadline=time.monotonic() + 20.0,
    )
    assert result.proof is not None
    assert result.proof.state is et.ExecutionState.UNKNOWN, result.proof.reason
    assert result.authorization_safe is False


# ---------------------------------------------------------------------------
# 3. wrapped chr()-built WSL UNKNOWN
# ---------------------------------------------------------------------------


def test_chr_built_wsl_argv_is_not_allowlisted(boundary):
    """The dynamically constructed WSL command has no literal token; the
    allowlist (not string recognition) rejects it."""
    chr_wsl = "''.join(chr(x) for x in [119,115,108,46,101,120,101])"
    inner = "import subprocess;subprocess.run([%s,'-d','Ubuntu'])" % chr_wsl
    cmd = [PY, "-c", inner]
    assert "wsl.exe" not in " ".join(cmd)
    result, proof = boundary.run(cmd)
    assert proof.state is et.ExecutionState.UNTRUSTED, proof.reason
    assert result.authorization_safe is False


def test_wsl_descendant_from_allowlisted_command_is_unknown(boundary, monkeypatch):
    """A WSL descendant observed under an allowlisted command is UNKNOWN: the
    image is outside the roots and cannot be admitted to the frozen manifest."""
    wsl = r"C:\Windows\System32\wsl.exe"
    monkeypatch.setattr(
        ident, "DESCENDANTS_FN",
        lambda _pid: [{"pid": 777777, "image": wsl, "error": None}],
    )
    result, proof = boundary.run([PY, "-c", "pass"])
    assert proof.state is et.ExecutionState.UNKNOWN, proof.reason
    assert result.authorization_safe is False


# ---------------------------------------------------------------------------
# 4. PATH shadowing UNTRUSTED
# ---------------------------------------------------------------------------


def test_path_shadowing_resolves_outside_roots_and_is_untrusted(boundary,
                                                                tmp_path,
                                                                monkeypatch):
    shadow_dir = tmp_path / "shadow"
    shadow_dir.mkdir()
    shadow = shadow_dir / ("python.exe" if os.name == "nt" else "python")
    shutil.copy2(PY, shadow)
    monkeypatch.setenv("PATH", str(shadow_dir) + os.pathsep + os.environ.get("PATH", ""))
    resolved = boundary._resolve_image(["python.exe" if os.name == "nt" else "python"])
    assert os.path.normcase(resolved).startswith(os.path.normcase(str(shadow_dir)))
    assert boundary._inside_roots(resolved) is False
    assert boundary.image_in_manifest(resolved, _sha(resolved)) is False
    result, proof = boundary.run(["python.exe" if os.name == "nt" else "python"])
    assert proof.state is et.ExecutionState.UNTRUSTED, proof.reason
    assert result.authorization_safe is False


# ---------------------------------------------------------------------------
# 5. proof tamper rejected by from_dict (A3 MAC + nonce binding)
# ---------------------------------------------------------------------------


def test_proof_tamper_rejected_by_from_dict(boundary):
    _result, proof = boundary.run([PY, "-c", "pass"])
    good = proof.to_dict()
    assert et.ExecutionProof.from_dict(good, boundary.trust).state is (
        et.ExecutionState.TRUSTED
    )
    for field, value in (
        ("state", "UNTRUSTED"),
        ("approved_images_match", False),
        ("nonce", "0" * 32),
        ("execution_digest", "f" * 64),
        ("mac", "0" * 64),
    ):
        bad = dict(good)
        bad[field] = value
        with pytest.raises(et.ExecutionProofError):
            et.ExecutionProof.from_dict(bad, boundary.trust)
    # A caller-supplied TRUSTED state on an UNTRUSTED proof cannot survive.
    _, untrusted = boundary.run([PY, "-c", "import os; os.system('calc')"])
    forged = untrusted.to_dict()
    forged["state"] = "TRUSTED"
    forged["approved_images_match"] = True
    with pytest.raises(et.ExecutionProofError):
        et.ExecutionProof.from_dict(forged, boundary.trust)


def test_proof_carries_replay_binding_fields(boundary):
    _, proof = boundary.run([PY, "-c", "pass"])
    d = proof.to_dict()
    for key in (
        "nonce", "execution_digest", "candidate_digest", "approved_images_match",
        "job_member_count", "descendant_count", "consumed_digest",
        "dependency_digest", "containment_claim", "state", "reason", "mac",
    ):
        assert key in d, key
    assert len(proof.nonce) >= 16
    assert len(proof.execution_digest) == 64
    assert d["containment_claim"] == "none"


# ---------------------------------------------------------------------------
# 6. no setter can force authorization_safe True
# ---------------------------------------------------------------------------


def test_no_setter_can_force_authorization_safe_true():
    result = runner.run_managed(
        [PY, "-c", "import os; os.system('calc')"],
        log_dir=_log("nosetter"), name="r62-nosetter",
        deadline=time.monotonic() + 20.0,
    )
    assert result.proof.state is not et.ExecutionState.TRUSTED
    assert result.authorization_safe is False
    # The property setter deliberately ignores True; only False latches a veto.
    result.authorization_safe = True
    assert result.authorization_safe is False
    # A hand-built forged proof dict cannot be used to force authorization.
    result.proof = {
        "state": "TRUSTED", "mac": "0" * 64, "nonce": "0" * 32,
    }
    assert result.authorization_safe is False


def test_false_setter_still_latches_a_permanent_veto():
    result = runner.run_managed(
        [PY, "-c", "pass"], log_dir=_log("veto"), name="r62-veto",
        deadline=time.monotonic() + 20.0,
    )
    assert result.authorization_safe is True
    result.authorization_safe = False
    result.containment = runner.CONTAINMENT_CLEAN
    result.wsl_uncontained = False
    result.error = ""
    assert result.authorization_safe is False
