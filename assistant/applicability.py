"""Physical-applicability classification for PD inputs (ADR Round 6, section 9).

The decision is derived ONLY from a content signature (magic bytes, file
structure, table structure, or clear textual markers).  Filenames and
extensions are *never* consulted, and the absence of a recognized name can
never by itself yield ``SUPPORTED_NOT_APPLICABLE``.

Fail closed: anything that cannot be positively identified as a known physical
format (``SUPPORTED_APPLICABLE``) or positively identified as a known
non-physical document (``SUPPORTED_NOT_APPLICABLE``) is ``UNCERTAIN``.
Encoded/compressed blobs are only ever upgraded to ``SUPPORTED_APPLICABLE``
when the decoded payload carries a physical signature; otherwise they remain
``UNCERTAIN`` (an encoded payload never grants DRC N/A).

Only ``SUPPORTED_NOT_APPLICABLE`` authorizes an N/A verdict (see
:func:`authorize_na`).  All scanning covers the entire content, not a leading
window of lines.

No third-party dependencies; Python 3.10, stdlib only.
"""
from __future__ import annotations

import base64
import binascii
import gzip
import json
import re
import zlib
from enum import Enum
from pathlib import Path


class Applicability(Enum):
    SUPPORTED_APPLICABLE = "SUPPORTED_APPLICABLE"
    SUPPORTED_NOT_APPLICABLE = "SUPPORTED_NOT_APPLICABLE"
    UNSUPPORTED = "UNSUPPORTED"
    UNCERTAIN = "UNCERTAIN"

    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify(path: str | Path, data: bytes | None = None) -> Applicability:
    """Return the applicability class of the content at *path* / in *data*.

    *data*, when supplied, is authoritative and *path* is used for evidence
    only.  Any error while obtaining/decoding content resolves to
    ``UNCERTAIN`` (fail closed).
    """
    try:
        return _analyze(path, data, depth=0)[0]
    except Exception:  # noqa: BLE001 - deliberate fail-closed boundary
        return Applicability.UNCERTAIN


def authorize_na(a: Applicability) -> bool:
    """True only for a positively-identified, supported non-physical input."""
    return a is Applicability.SUPPORTED_NOT_APPLICABLE


def describe(path: str | Path, data: bytes | None = None) -> dict:
    """Audit-friendly view: classification plus the evidence used to decide."""
    try:
        return _analyze(path, data, depth=0)[1]
    except Exception as exc:  # noqa: BLE001 - deliberate fail-closed boundary
        return {
            "classification": Applicability.UNCERTAIN.name,
            "magic": None,
            "decoder": None,
            "decoded_magic": None,
            "reason": f"analysis error: {type(exc).__name__}: {exc}",
            "path": None if path is None else str(path),
            "source": "argument" if data is not None else "file",
            "size": None,
            "decoded_size": None,
        }


# ---------------------------------------------------------------------------
# Core analysis
# ---------------------------------------------------------------------------

_MAX_DECODE_DEPTH = 3


def _analyze(path, data, *, depth: int):
    """Return ``(Applicability, evidence_dict)`` for the given blob."""
    ev = {
        "classification": None,
        "magic": None,
        "decoder": None,
        "decoded_magic": None,
        "reason": None,
        "path": None if path is None else str(path),
        "source": "argument" if data is not None else "file",
        "size": None,
        "decoded_size": None,
    }

    blob = _coerce_bytes(data)
    if blob is None:
        if data is not None:
            blob = _coerce_bytes(str(data).encode("utf-8", "surrogatepass"))
    if blob is None:
        if path is None:
            ev.update(
                classification=Applicability.UNCERTAIN.name,
                reason="no data and no path provided",
            )
            return Applicability.UNCERTAIN, ev
        try:
            blob = Path(path).read_bytes()
        except Exception:
            ev.update(
                classification=Applicability.UNCERTAIN.name,
                reason="path could not be read",
            )
            return Applicability.UNCERTAIN, ev

    ev["size"] = len(blob)
    if not blob:
        ev.update(classification=Applicability.UNCERTAIN.name, reason="empty content")
        return Applicability.UNCERTAIN, ev

    # 1) Direct physical signature (highest-confidence evidence).
    magic = _physical_magic(blob)
    if magic is not None:
        ev.update(
            classification=Applicability.SUPPORTED_APPLICABLE.name,
            magic=magic,
            reason=f"direct physical signature: {magic}",
        )
        return Applicability.SUPPORTED_APPLICABLE, ev

    # 2) Encoded / compressed payload: decode and look ONLY for physical magic.
    decoder, decoded = _decode_or_none(blob)
    if decoder is not None:
        ev["decoder"] = decoder
        if decoded is None:
            ev.update(
                classification=Applicability.UNCERTAIN.name,
                reason=f"{decoder} decoding failed",
            )
            return Applicability.UNCERTAIN, ev
        ev["decoded_size"] = len(decoded)
        decoded_magic = _physical_magic(decoded)
        if decoded_magic is not None:
            ev.update(
                classification=Applicability.SUPPORTED_APPLICABLE.name,
                decoded_magic=decoded_magic,
                reason=f"{decoder}-decoded payload has physical signature: "
                       f"{decoded_magic}",
            )
            return Applicability.SUPPORTED_APPLICABLE, ev
        # Decode succeeded but yielded no physical signature.  Follow one more
        # layer of encoding (bounded), still only for physical magic.
        if depth < _MAX_DECODE_DEPTH:
            nested_decoder, nested = _decode_or_none(decoded)
            if nested_decoder is not None and nested is not None:
                nested_magic = _physical_magic(nested)
                if nested_magic is not None:
                    ev.update(
                        classification=Applicability.SUPPORTED_APPLICABLE.name,
                        decoder=f"{decoder}->{nested_decoder}",
                        decoded_magic=nested_magic,
                        reason=f"nested decoded payload has physical signature: "
                               f"{nested_magic}",
                    )
                    return Applicability.SUPPORTED_APPLICABLE, ev
        ev.update(
            classification=Applicability.UNCERTAIN.name,
            reason=f"{decoder}-decoded payload has no recognized physical signature",
        )
        return Applicability.UNCERTAIN, ev

    # 3) Plain text: recognize non-physical documents, else fail closed.
    text = _decode_text(blob)
    if text is None:
        ev.update(
            classification=Applicability.UNCERTAIN.name,
            reason="unknown binary content (no physical signature)",
        )
        return Applicability.UNCERTAIN, ev

    na_magic = _nonphysical_magic(text)
    if na_magic is not None:
        ev.update(
            classification=Applicability.SUPPORTED_NOT_APPLICABLE.name,
            magic=na_magic,
            reason=f"recognized non-physical text: {na_magic}",
        )
        return Applicability.SUPPORTED_NOT_APPLICABLE, ev

    ev.update(
        classification=Applicability.UNCERTAIN.name,
        reason="text content has no clear physical or non-physical marker",
    )
    return Applicability.UNCERTAIN, ev


# ---------------------------------------------------------------------------
# Byte/text helpers
# ---------------------------------------------------------------------------

def _coerce_bytes(value) -> bytes | None:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    return None


def _decode_text(blob: bytes) -> str | None:
    try:
        return blob.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _u16be(blob: bytes, off: int) -> int:
    return (blob[off] << 8) | blob[off + 1]


# ---------------------------------------------------------------------------
# Physical signatures
# ---------------------------------------------------------------------------

_GDS_DTYPES = frozenset({0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06})


def _looks_gdsii(blob: bytes) -> bool:
    """Recognize the canonical GDSII ``HEADER`` record at offset 0.

    A GDSII stream begins with a HEADER record: 2-byte big-endian length (>=
    4, even), record type 0x00, data type 0x02 (two-byte signed integer), then
    the stream version.  At least one further well-formed record must follow.
    """
    n = len(blob)
    if n < 6:
        return False
    rec_len = _u16be(blob, 0)
    if rec_len < 4 or rec_len > n or rec_len % 2:
        return False
    if blob[2] != 0x00 or blob[3] != 0x02:  # HEADER, INT2
        return False
    # Optional confirmation: if more data follows, the next record must also
    # be structurally plausible (length in range, known data type).
    if rec_len < n:
        off = rec_len
        if off + 4 > n:
            return False
        next_len = _u16be(blob, off)
        if next_len < 4 or next_len > n - off:
            return False
        if blob[off + 3] not in _GDS_DTYPES:
            return False
    return True


def _physical_magic(blob: bytes) -> str | None:
    """Return a physical-format name when *blob* positively matches one."""
    # GDSII is binary; check before any text decoding.
    if _looks_gdsii(blob):
        return "GDSII"

    # OASIS begins with an ASCII magic string.
    if blob.startswith(b"%SEMI-OASIS"):
        return "OASIS"

    text = _decode_text(blob)
    if text is None:
        return None

    lines = _content_lines(text)
    if not lines:
        return None
    first = lines[0]

    # DEF (Design Exchange Format) begins with: DESIGN <name> <version> ;
    if re.match(r"^DESIGN\s+[A-Za-z0-9_.$]+", first):
        return "DEF"

    # LEF (Library Exchange Format): leading VERSION / LEF statement plus a
    # terminating END LIBRARY somewhere in the file.
    if re.match(r"^(?:VERSION|LEF)\b", first) and re.search(
        r"\bEND\s+LIBRARY\b", text, re.IGNORECASE
    ):
        return "LEF"

    # Numeric coordinate table: >= 5 rows with a consistent >= 2 numeric cols.
    if _coordinate_table(text):
        return "COORD_TABLE"

    return None


# ---------------------------------------------------------------------------
# Encoded / compressed payloads
# ---------------------------------------------------------------------------

_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_B64_RE = re.compile(r"^[A-Za-z0-9+/]*={0,2}$")


def _decode_or_none(blob: bytes):
    """Return ``(decoder_name, decoded_bytes_or_None)`` or ``(None, None)``."""
    # gzip magic.
    if blob[:2] == b"\x1f\x8b":
        try:
            return "gzip", gzip.decompress(blob)
        except Exception:
            return "gzip", None

    # zlib magic (CMF/FLG common combinations).
    if len(blob) >= 2 and blob[0] == 0x78 and blob[1] in (0x01, 0x5E, 0x9C, 0xDA):
        try:
            return "zlib", zlib.decompress(blob)
        except Exception:
            return "zlib", None

    # Text-based encodings.  Reject non-ASCII content outright.
    try:
        stripped = blob.decode("ascii")
    except UnicodeDecodeError:
        return None, None
    compact = "".join(stripped.split())
    if len(compact) < 8:
        return None, None

    # Hex-only.
    if len(compact) % 2 == 0 and _HEX_RE.match(compact):
        try:
            return "hex", binascii.unhexlify(compact)
        except Exception:
            return "hex", None

    # Base64-looking.  Require padding or a strong base64 alphabet signal so
    # ordinary prose is not mistaken for encoded data.
    if len(compact) % 4 == 0 and _B64_RE.match(compact) and (
        compact.endswith("=") or re.search(r"[+/0-9]", compact)
    ):
        try:
            return "base64", base64.b64decode(compact, validate=True)
        except Exception:
            return "base64", None

    return None, None


# ---------------------------------------------------------------------------
# Non-physical text signatures
# ---------------------------------------------------------------------------

_NUM = r"[+-]?(?:0[xX][0-9A-Fa-f]+|\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
_ROW_RE = re.compile(
    r"^\s*" + _NUM + r"(?:[\s,;]+" + _NUM + r")+\s*$"
)


def _coordinate_table(text: str) -> bool:
    """True when >= 5 lines are numeric rows with a consistent column count."""
    per_width: dict[int, int] = {}
    for line in text.splitlines():
        if not _ROW_RE.match(line):
            continue
        toks = re.findall(_NUM, line)
        if len(toks) >= 2:
            per_width[len(toks)] = per_width.get(len(toks), 0) + 1
    return any(count >= 5 for count in per_width.values())


def _content_lines(text: str) -> list[str]:
    """Non-empty content lines after skipping leading blanks/comments."""
    lines = [ln.strip() for ln in text.splitlines()]
    i = 0
    while i < len(lines) and (
        lines[i] == ""
        or lines[i].startswith("#")
        or lines[i].startswith("//")
    ):
        i += 1
    return lines[i:]


_SRC_PATTERNS = (
    r"^\s*#include\b",
    r"^\s*#define\b",
    r"^\s*#\s*!\s*/",                       # shebang
    r"^\s*(?:def|class|import|from|return|lambda)\s",       # Python
    r"^\s*(?:public|private|protected)\s+(?:class|interface|static|final|void|int|String)\b",
    r"^\s*(?:package|namespace|using)\s",
    r"^\s*(?:function|var|let|const)\s",
    r"^\s*(?:fn|pub|use|impl|struct|enum|mod)\s",           # Rust
    r"^\s*func\s",                                          # Go
    r"<\?php\b|<\?xml\b|<!DOCTYPE\s+html\b|<html\b",
    r"^\s*SELECT\s+.+\s+FROM\s+",                           # SQL
)
_SRC_RES = tuple(re.compile(p, re.IGNORECASE) for p in _SRC_PATTERNS)


def _nonphysical_magic(text: str) -> str | None:
    """Return a non-physical document class when markers are unambiguous."""
    # JSON (positive parse is a clear marker; a bare number is harmless here).
    try:
        json.loads(text)
        return "JSON"
    except Exception:
        pass

    lines = _content_lines(text)

    # Source code (clear language keywords / directives).
    for rx in _SRC_RES:
        if any(rx.search(ln) for ln in lines):
            return "SOURCE_CODE"

    # YAML document markers: an explicit '---' plus key: value pairs.
    if any(ln == "---" for ln in lines):
        kv = sum(1 for ln in lines if re.match(r"^[\w.\-]+:\s+\S", ln))
        if kv >= 2:
            return "YAML"

    # Markdown / report documents: heading(s) and prose, or explicit report
    # section titles.  Count headings over ALL lines: a leading "# Title" is a
    # markdown heading, not an EDA comment (which _content_lines strips above).
    all_lines = [ln.strip() for ln in text.splitlines()]
    headings = sum(1 for ln in all_lines if re.match(r"^#{1,6}\s+\S", ln))
    prose = sum(
        1 for ln in all_lines
        if len(ln) >= 20 and re.search(r"[A-Za-z]{3}", ln)
    )
    if headings >= 2 and prose >= 2:
        return "DOC"
    if lines and re.match(
        r"^(?:Report|Summary|Executive Summary|Table of Contents)\b",
        lines[0],
        re.IGNORECASE,
    ):
        return "REPORT"

    return None


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _self_test() -> None:  # pragma: no cover - exercised as a script
    import os

    # 1) Real GDSII-like magic -> APPLICABLE.
    gdsii = b"".join([
        b"\x00\x06\x00\x02\x02\x58",  # HEADER, version 600
        b"\x00\x1c\x01\x02",          # BGNLIB record header
        b"\x00" * 24,                 # BGNLIB payload
        b"\x00\x04\x11\x00",          # ENDLIB
    ])
    assert classify("<gds>", gdsii) is Applicability.SUPPORTED_APPLICABLE
    assert describe("<gds>", gdsii)["magic"] == "GDSII"

    # 2) Base64-wrapped GDSII -> APPLICABLE.
    wrapped = base64.b64encode(gdsii)
    assert classify("<b64>", wrapped) is Applicability.SUPPORTED_APPLICABLE
    d = describe("<b64>", wrapped)
    assert d["decoder"] == "base64" and d["decoded_magic"] == "GDSII"

    # 3) Coordinate table past line 200 -> APPLICABLE.
    lines = [f"// padding line {i}" for i in range(210)]
    lines += [f"{i}.0 {i + 1}.0 {i + 2}.0 {i + 3}.0" for i in range(6)]
    coord = "\n".join(lines).encode("ascii")
    assert classify("<coord>", coord) is Applicability.SUPPORTED_APPLICABLE
    assert describe("<coord>", coord)["magic"] == "COORD_TABLE"

    # 4) Plain source code -> NOT_APPLICABLE.
    src = (
        b"import os\n\n"
        b"def main():\n"
        b"    print('hello')\n\n"
        b"if __name__ == '__main__':\n"
        b"    main()\n"
    )
    assert classify("<src>", src) is Applicability.SUPPORTED_NOT_APPLICABLE
    assert describe("<src>", src)["magic"] == "SOURCE_CODE"

    # 5) Random bytes -> UNCERTAIN.
    rnd = os.urandom(64)
    assert classify("<rnd>", rnd) is Applicability.UNCERTAIN

    # 6) authorize_na only for NOT_APPLICABLE.
    assert authorize_na(Applicability.SUPPORTED_NOT_APPLICABLE) is True
    for a in (
        Applicability.SUPPORTED_APPLICABLE,
        Applicability.UNSUPPORTED,
        Applicability.UNCERTAIN,
    ):
        assert authorize_na(a) is False

    # Fail-closed guards: empty content and missing path/data.
    assert classify("<empty>", b"") is Applicability.UNCERTAIN
    assert classify("<none>", None) is Applicability.UNCERTAIN

    print("applicability self-test: OK")


if __name__ == "__main__":
    _self_test()
