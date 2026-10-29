"""JSON canónico y hashes de contenido (escrito desde cero, sin `json.dumps`).

Reglas:
- Objetos: claves `str`, normalizadas a NFC y ordenadas por punto de código Unicode.
  Dos claves que coinciden tras NFC son un error (serían ambiguas).
- Strings: NFC. Se escapan solo `"`, `\\` y los caracteres de control U+0000–U+001F
  (con `\\b \\f \\n \\r \\t` cuando existen, si no `\\u00xx` en minúscula). El resto va literal.
  Los surrogates sueltos son un error (no son UTF-8 válido).
- Números: solo enteros en el rango seguro de JSON (±(2^53 − 1)). Los floats se rechazan:
  su representación textual no es única entre lenguajes.
- `true`, `false`, `null`; listas y tuplas como arrays.
- Sin espacios. Salida en bytes UTF-8.

Nota: RFC 8785 (JCS) ordena por unidades UTF-16; aquí se ordena por punto de código. Solo
difiere con claves que mezclan caracteres fuera del BMP con U+E000–U+FFFF.
"""

from __future__ import annotations

import hashlib
import unicodedata
from typing import Any

MAX_SAFE_INT = 2**53 - 1

_SHORT_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}


class CanonicalError(ValueError):
    """El valor no se puede representar en JSON canónico."""


def _string(value: str) -> str:
    value = unicodedata.normalize("NFC", value)
    out = ['"']
    for ch in value:
        code = ord(ch)
        if ch in _SHORT_ESCAPES:
            out.append(_SHORT_ESCAPES[ch])
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        elif 0xD800 <= code <= 0xDFFF:
            raise CanonicalError(f"surrogate suelto U+{code:04X} no representable en UTF-8")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


def _encode(value: Any, path: str) -> str:
    # bool antes que int: en Python `True` es un int.
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        if not -MAX_SAFE_INT <= value <= MAX_SAFE_INT:
            raise CanonicalError(f"{path}: entero fuera del rango seguro de JSON")
        return str(int(value))
    if isinstance(value, float):
        raise CanonicalError(f"{path}: los floats no están permitidos en JSON canónico")
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, (list, tuple)):
        return "[" + ",".join(_encode(v, f"{path}[{i}]") for i, v in enumerate(value)) + "]"
    if isinstance(value, dict):
        items: dict[str, Any] = {}
        for key, v in value.items():
            if not isinstance(key, str):
                raise CanonicalError(f"{path}: clave no string {key!r}")
            nkey = unicodedata.normalize("NFC", key)
            if nkey in items:
                raise CanonicalError(f"{path}: claves duplicadas tras NFC: {key!r}")
            items[nkey] = v
        parts = [f"{_string(k)}:{_encode(items[k], f'{path}.{k}')}" for k in sorted(items)]
        return "{" + ",".join(parts) + "}"
    raise CanonicalError(f"{path}: tipo no soportado {type(value).__name__}")


def canonical_json(value: Any) -> bytes:
    """Serializa `value` a JSON canónico (bytes UTF-8)."""
    return _encode(value, "$").encode("utf-8")


def canonical_hash(value: Any) -> str:
    """`sha256:<hex>` del JSON canónico de `value`."""
    return "sha256:" + hashlib.sha256(canonical_json(value)).hexdigest()
