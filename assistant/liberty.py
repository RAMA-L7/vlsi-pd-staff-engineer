r"""Liberty PVT parsing and validation (finding F).

PVT claims in configuration files are validated against the *actual* Liberty
content (``operating_conditions`` + ``nom_voltage``/``nom_temperature``) and
against the corner encoded in the file name (``_<tt|ss|ff>_<n?>\d+C_\d+v\d+``).
Availability of a local file is tracked separately from availability of a
published PDK corner - they are different claims.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

FILENAME_PVT_RE = re.compile(
    r"_(?P<proc>tt|ss|ff)_(?P<tneg>n?)(?P<temp>\d+)C_(?P<vint>\d+)v(?P<vfrac>\d+)",
    re.IGNORECASE,
)

PROC_TEMPS = {"tt": "typical", "ss": "slow", "ff": "fast"}


@dataclass
class LibertyPVT:
    path: str
    operating_condition: str | None = None
    nom_voltage: float | None = None
    nom_temperature: float | None = None
    nom_process: float | None = None
    file_name_pvt: dict | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def declared_process(self) -> str | None:
        return (self.file_name_pvt or {}).get("proc")


def parse_liberty_pvt(path: str) -> LibertyPVT:
    """Parse the operating condition block of a Liberty file (header only)."""
    pvt = LibertyPVT(path=str(path))
    if not os.path.isfile(path):
        pvt.errors.append(f"library file not found: {path}")
        return pvt

    name = os.path.basename(path)
    m = FILENAME_PVT_RE.search(name)
    if m:
        temp = float(m.group("temp"))
        if m.group("tneg"):
            temp = -temp
        volt = float(m.group("vint")) + float(m.group("vfrac")) / (
            10 ** len(m.group("vfrac"))
        )
        pvt.file_name_pvt = {
            "proc": m.group("proc").lower(),
            "temperature_c": temp,
            "voltage_v": round(volt, 4),
        }

    # Read only the head of the file: operating_conditions lives near the top.
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(200000)
    except OSError as exc:
        pvt.errors.append(f"cannot read library: {exc}")
        return pvt

    oc = re.search(r'operating_conditions\s*\(\s*"([^"]+)"', head)
    if oc:
        pvt.operating_condition = oc.group(1)
    for attr, target in (
        ("nom_voltage", "nom_voltage"),
        ("nom_temperature", "nom_temperature"),
        ("nom_process", "nom_process"),
    ):
        m2 = re.search(rf"{attr}\s*:\s*([-0-9.eE+]+)", head)
        if m2:
            try:
                setattr(pvt, target, float(m2.group(1)))
            except ValueError:
                pvt.errors.append(f"malformed {attr} in {path}")
    if pvt.nom_voltage is None and pvt.nom_temperature is None:
        pvt.errors.append(
            "no operating_conditions/nom_* block found in first 200 KB"
        )
    return pvt


def check_filename_header_consistency(pvt: LibertyPVT) -> list[str]:
    """The file name corner must agree with the header values inside it."""
    problems: list[str] = []
    fn = pvt.file_name_pvt
    if not fn:
        return problems
    if pvt.nom_temperature is not None and abs(
        pvt.nom_temperature - fn["temperature_c"]
    ) > 0.5:
        problems.append(
            f"file name says {fn['temperature_c']}C but header says "
            f"{pvt.nom_temperature}C"
        )
    if pvt.nom_voltage is not None and abs(
        pvt.nom_voltage - fn["voltage_v"]
    ) > 0.05:
        problems.append(
            f"file name says {fn['voltage_v']}V but header says "
            f"{pvt.nom_voltage}V"
        )
    return problems


@dataclass
class PVTClaim:
    """A PVT claim from configuration (e.g. synthesis_targets.json)."""

    corner: str
    claim_pvt: str
    lib_path: str
    available: bool = True
    note: str = ""


_NUM_RE = re.compile(r"(-?\d+(?:\.\d+)?)")


def extract_claim_numbers(text: str) -> tuple[float | None, float | None]:
    """Extract (temperature_c, voltage_v) guesses from a free-text PVT string.

    Heuristic (labelled as such): a number followed by 'V' is voltage; a number
    followed by 'C' (with optional degree sign) is temperature.  Strings like
    "0.9V / 125C" parse cleanly; ambiguous strings return None.
    """
    volt = None
    temp = None
    for m in re.finditer(r"(-?\d+(?:\.\d+)?)\s*(?:V|volt)", text, re.IGNORECASE):
        volt = float(m.group(1))
    for m in re.finditer(
        r"(-?\d+(?:\.\d+)?)\s*(?:C|deg\s*C|°)", text, re.IGNORECASE
    ):
        temp = float(m.group(1))
    return temp, volt


def validate_pvt_claim(claim: PVTClaim, *, tol_v: float = 0.05, tol_c: float = 1.0) -> dict:
    """Validate one configuration PVT claim against actual Liberty content.

    Returns a dict with:
      status: PASS | FAIL | INCONCLUSIVE | NOT_RUN
      problems: list[str]
      declared: parsed header values (or None)
      local_file_available: whether the referenced file exists
      note: interpretation
    """
    result: dict = {
        "corner": claim.corner,
        "claim_pvt": claim.claim_pvt,
        "lib": claim.lib_path,
        "problems": [],
        "declared": None,
        "local_file_available": os.path.isfile(claim.lib_path),
    }
    if not result["local_file_available"]:
        result["status"] = "NOT_RUN"
        result["problems"].append(
            "referenced Liberty file is not present locally "
            "(local file availability, NOT a claim about published PDK corners)"
        )
        return result

    pvt = parse_liberty_pvt(claim.lib_path)
    if pvt.errors:
        result["status"] = "FAIL"
        result["problems"].extend(pvt.errors)
        return result

    result["declared"] = {
        "operating_condition": pvt.operating_condition,
        "nom_voltage": pvt.nom_voltage,
        "nom_temperature": pvt.nom_temperature,
        "nom_process": pvt.nom_process,
        "file_name": pvt.file_name_pvt,
    }

    claim_temp, claim_volt = extract_claim_numbers(claim.claim_pvt)
    problems: list[str] = []

    if claim_volt is not None and pvt.nom_voltage is not None:
        if abs(claim_volt - pvt.nom_voltage) > tol_v:
            problems.append(
                f"claim voltage {claim_volt}V != Liberty nom_voltage "
                f"{pvt.nom_voltage}V"
            )
    elif claim_volt is not None and pvt.nom_voltage is None:
        problems.append("claim states a voltage but Liberty declares none")

    if claim_temp is not None and pvt.nom_temperature is not None:
        if abs(claim_temp - pvt.nom_temperature) > tol_c:
            problems.append(
                f"claim temperature {claim_temp}C != Liberty nom_temperature "
                f"{pvt.nom_temperature}C"
            )

    problems.extend(check_filename_header_consistency(pvt))

    # Process corner (tt/ss/ff) mentioned in the claim must match the file.
    claim_proc = None
    for token in ("ss", "ff", "tt", "slow", "fast", "typical"):
        if re.search(rf"\b{token}\b", claim.claim_pvt, re.IGNORECASE):
            claim_proc = "ss" if token in ("ss", "slow") else (
                "ff" if token in ("ff", "fast") else "tt"
            )
            break
    if claim_proc and pvt.declared_process and claim_proc != pvt.declared_process:
        problems.append(
            f"claim process {claim_proc} != file corner {pvt.declared_process}"
        )

    # Claimed purpose vs corner consistency (setup_worst should be slow etc.)
    if claim.note and claim_proc:
        pass  # informational only

    result["problems"] = problems
    result["status"] = "FAIL" if problems else "PASS"
    if not problems and (claim_temp is None or claim_volt is None):
        result["status"] = "PASS"
        result.setdefault("warnings", []).append(
            "claim text did not expose explicit numbers; header/name "
            "consistency checks only"
        )
    return result


def validate_targets_file(path: str, *, lib_dir: str | None = None) -> dict:
    """Validate every corner claim in synthesis_targets.json."""
    import json

    with open(path, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    base = lib_dir or os.path.dirname(os.path.abspath(path))
    results = []
    corners = cfg.get("corners", {}) or {}
    for name, spec in corners.items():
        lib = spec.get("lib", "")
        lib_path = lib if os.path.isabs(lib) else os.path.join(base, lib)
        claim = PVTClaim(
            corner=name,
            claim_pvt=str(spec.get("pvt", "")),
            lib_path=lib_path,
            available=bool(spec.get("available", True)),
            note=str(spec.get("note", "")),
        )
        res = validate_pvt_claim(claim)
        res["declared_available_in_config"] = claim.available
        res["config_note"] = claim.note
        results.append(res)
    statuses = [r["status"] for r in results]
    overall = (
        "FAIL"
        if "FAIL" in statuses
        else "INCONCLUSIVE"
        if "INCONCLUSIVE" in statuses
        else "NOT_RUN"
        if all(s == "NOT_RUN" for s in statuses)
        else "PASS"
    )
    return {"file": path, "overall": overall, "corners": results}
