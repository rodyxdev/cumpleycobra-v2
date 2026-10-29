import hashlib
import json
import unicodedata

import pytest

from backend.canonical import MAX_SAFE_INT, CanonicalError, canonical_hash, canonical_json


def reference(value) -> bytes:
    """Para entradas ya en NFC y sin floats, debe coincidir con json.dumps ordenado y compacto."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


@pytest.mark.parametrize(
    "value",
    [
        None, True, False, 0, -1, 42, MAX_SAFE_INT, -MAX_SAFE_INT,
        "", "hola", "comillas \" y barra \\", "línea\nnueva\ttab\r\b\f", "\x00\x01\x1f\x7f",
        "ñandú 日本 🚀", [], {}, [1, [2, [3]]],
        {"b": 1, "a": [True, None, "x"], "c": {"z": 0, "y": ""}},
        {"Z": 1, "a": 2, "_": 3, "0": 4, "é": 5},
    ],
)
def test_matches_reference_serialization(value):
    assert canonical_json(value) == reference(value)


def test_no_whitespace_and_sorted_keys():
    assert canonical_json({"b": [1, 2], "a": {"d": None, "c": True}}) == b'{"a":{"c":true,"d":null},"b":[1,2]}'


def test_control_chars_use_lowercase_unicode_escapes():
    assert canonical_json("\x00\x1f\x0b") == b'"\\u0000\\u001f\\u000b"'
    assert canonical_json("\n") == b'"\\n"'


def test_non_ascii_is_literal_utf8():
    assert canonical_json("ñ") == '"ñ"'.encode("utf-8")
    assert canonical_json("🚀") == b'"\xf0\x9f\x9a\x80"'


def test_nfc_normalization_of_values_and_keys():
    nfd = unicodedata.normalize("NFD", "canción")
    nfc = unicodedata.normalize("NFC", "canción")
    assert nfd != nfc
    assert canonical_json(nfd) == canonical_json(nfc)
    assert canonical_json({nfd: nfd}) == canonical_json({nfc: nfc})
    assert canonical_hash({"t": nfd}) == canonical_hash({"t": nfc})


def test_keys_colliding_after_nfc_are_rejected():
    with pytest.raises(CanonicalError, match="duplicadas"):
        canonical_json({unicodedata.normalize("NFD", "é"): 1, "é": 2})


def test_key_order_is_by_code_point():
    # 'Z' (U+005A) < 'a' (U+0061) < 'é' (U+00E9) < '日' (U+65E5)
    assert canonical_json({"日": 1, "é": 1, "a": 1, "Z": 1}) == '{"Z":1,"a":1,"é":1,"日":1}'.encode()


@pytest.mark.parametrize("bad", [1.0, 0.5, float("nan"), float("inf"), {"x": [1, 2.5]}])
def test_floats_rejected(bad):
    with pytest.raises(CanonicalError, match="float"):
        canonical_json(bad)


@pytest.mark.parametrize("bad", [MAX_SAFE_INT + 1, -MAX_SAFE_INT - 1, 10**30])
def test_unsafe_integers_rejected(bad):
    with pytest.raises(CanonicalError, match="rango"):
        canonical_json(bad)


@pytest.mark.parametrize("bad", [{1: "a"}, {("a",): 1}, {"s": {1, 2}}, b"bytes", object()])
def test_unsupported_types_rejected(bad):
    with pytest.raises(CanonicalError):
        canonical_json(bad)


def test_lone_surrogate_rejected():
    with pytest.raises(CanonicalError, match="surrogate"):
        canonical_json("\ud800")


def test_bool_is_not_confused_with_int():
    assert canonical_json([True, 1, False, 0]) == b"[true,1,false,0]"


def test_tuple_equals_list():
    assert canonical_json((1, "a")) == canonical_json([1, "a"])


def test_insertion_order_does_not_matter():
    a = {"x": 1, "y": {"b": 2, "a": 1}}
    b = {"y": {"a": 1, "b": 2}, "x": 1}
    assert canonical_hash(a) == canonical_hash(b)


def test_known_vector():
    value = {"criterios": [{"id": "C1", "texto": "Suma líneas"}], "versión": 1}
    expected = b'{"criterios":[{"id":"C1","texto":"Suma l\xc3\xadneas"}],"versi\xc3\xb3n":1}'
    assert canonical_json(value) == expected
    assert canonical_hash(value) == "sha256:" + hashlib.sha256(expected).hexdigest()


def test_hash_changes_with_content():
    assert canonical_hash({"a": 1}) != canonical_hash({"a": 2})
    assert canonical_hash({"a": "1"}) != canonical_hash({"a": 1})
