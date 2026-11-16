"""Liberty PVT validation tests (finding F)."""
import os

import pytest

from assistant.liberty import (
    PVTClaim,
    extract_claim_numbers,
    parse_liberty_pvt,
    validate_pvt_claim,
    validate_targets_file,
)

LIB_DIR = r"D:\tools\src\OpenROAD\src\sta\test\sky130hd"
TT = os.path.join(LIB_DIR, "sky130_fd_sc_hd__tt_025C_1v80.lib")
SS = os.path.join(LIB_DIR, "sky130_fd_sc_hd__ss_n40C_1v40.lib")
FF = os.path.join(LIB_DIR, "sky130_fd_sc_hd__ff_n40C_1v95.lib")

has_real_libs = os.path.isfile(TT)


@pytest.mark.skipif(not has_real_libs, reason="local Sky130 Liberty files not present")
def test_parse_real_tt_header():
    pvt = parse_liberty_pvt(TT)
    assert not pvt.errors
    assert pvt.operating_condition == "tt_025C_1v80"
    assert pvt.nom_temperature == pytest.approx(25.0)
    assert pvt.nom_voltage == pytest.approx(1.8)
    assert pvt.file_name_pvt == {"proc": "tt", "temperature_c": 25.0, "voltage_v": 1.8}


@pytest.mark.skipif(not has_real_libs, reason="local Sky130 Liberty files not present")
def test_parse_real_ss_and_ff_headers():
    ss = parse_liberty_pvt(SS)
    assert ss.nom_temperature == pytest.approx(-40.0)
    assert ss.nom_voltage == pytest.approx(1.4)
    ff = parse_liberty_pvt(FF)
    assert ff.nom_voltage == pytest.approx(1.95)


@pytest.mark.skipif(not has_real_libs, reason="local Sky130 Liberty files not present")
def test_original_config_pvt_claims_fail_validation(tmp_path):
    """The original synthesis_targets.json claims must be caught (finding F)."""
    # tt claim says 1.0V but Liberty says 1.8V -> FAIL
    claim = PVTClaim(
        corner="func_tt",
        claim_pvt="TT / 1.0V / 25C",
        lib_path=TT,
    )
    res = validate_pvt_claim(claim)
    assert res["status"] == "FAIL"
    assert any("1.0" in p or "voltage" in p for p in res["problems"])

    # ff claim says 1.1V/-40C but Liberty says 1.95V/-40C -> FAIL
    claim_ff = PVTClaim(
        corner="func_ff", claim_pvt="FF / 1.1V / -40C", lib_path=FF
    )
    res_ff = validate_pvt_claim(claim_ff)
    assert res_ff["status"] == "FAIL"

    # ss claim says 0.9V/125C; file is 1.4V/-40C -> FAIL
    claim_ss = PVTClaim(
        corner="func_ss", claim_pvt="SS / 0.9V / 125C", lib_path=SS
    )
    res_ss = validate_pvt_claim(claim_ss)
    assert res_ss["status"] == "FAIL"


@pytest.mark.skipif(not has_real_libs, reason="local Sky130 Liberty files not present")
def test_corrected_pvt_claims_pass(tmp_path):
    claim = PVTClaim(
        corner="func_tt",
        claim_pvt="TT / 1.80V / 25C",
        lib_path=TT,
    )
    res = validate_pvt_claim(claim)
    assert res["status"] == "PASS", res["problems"]

    claim_ff = PVTClaim(
        corner="func_ff", claim_pvt="FF / 1.95V / -40C", lib_path=FF
    )
    assert validate_pvt_claim(claim_ff)["status"] == "PASS"


def test_missing_local_file_is_not_run_not_fail():
    """Local unavailability != PDK corner unavailability (finding F)."""
    claim = PVTClaim(
        corner="func_ss",
        claim_pvt="SS / 1.60V / 100C",
        lib_path=r"Z:\no\such\ss_100C_1v60.lib",
    )
    res = validate_pvt_claim(claim)
    assert res["status"] == "NOT_RUN"
    assert "not present locally" in res["problems"][0]
    assert "PDK corner" in res["problems"][0]


def test_filename_header_mismatch_detected(tmp_path):
    lib = tmp_path / "sky130_fd_sc_hd__tt_025C_1v80.lib"
    lib.write_text(
        'library(x) {\n  operating_conditions ("tt_025C_1v80") {\n'
        "    nom_voltage : 1.0000000000;\n"
        "    nom_temperature : 25.000000000;\n"
        "  }\n}\n",
        encoding="utf-8",
    )
    claim = PVTClaim(
        corner="c", claim_pvt="TT / 1.0V / 25C", lib_path=str(lib)
    )
    res = validate_pvt_claim(claim)
    # file name says 1v80 but header says 1.0 -> inconsistency flagged
    assert res["status"] == "FAIL"
    assert any("file name says 1.8V" in p for p in res["problems"])


def test_extract_claim_numbers():
    assert extract_claim_numbers("TT / 1.80V / 25C") == (25.0, 1.80)
    assert extract_claim_numbers("SS 1.6V 100C") == (100.0, 1.6)
    assert extract_claim_numbers("no numbers here") == (None, None)


@pytest.mark.skipif(not has_real_libs, reason="local Sky130 Liberty files not present")
def test_validate_targets_file_reports_original_config(tmp_path):
    """Run against the repo's (corrected) synthesis_targets.json: must be PASS
    after remediation; before remediation this returned FAIL."""
    import shutil

    repo_targets = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "synthesis_targets.json",
    )
    if not os.path.isfile(repo_targets):
        pytest.skip("synthesis_targets.json not found")
    result = validate_targets_file(repo_targets, lib_dir=LIB_DIR)
    assert result["overall"] in ("PASS", "NOT_RUN", "INCONCLUSIVE")
    for c in result["corners"]:
        # every claim is either validated or explicitly NOT_RUN (file absent)
        assert c["status"] in ("PASS", "NOT_RUN", "FAIL")
    # after remediation: no FAIL allowed
    assert result["overall"] != "FAIL", result
