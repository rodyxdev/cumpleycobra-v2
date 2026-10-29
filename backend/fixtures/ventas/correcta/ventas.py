"""Resumen de ventas por producto."""

from __future__ import annotations

import re

_ENTERO = re.compile(r"[0-9]+")


def resumir_ventas(texto: str) -> dict[str, int]:
    """Devuelve el total vendido por producto, en centavos.

    Cada línea tiene el formato `producto,cantidad,precio_unitario_centavos`. Se ignoran las
    líneas vacías y los comentarios (`#`). Ante una línea inválida lanza ValueError indicando
    el número de línea (desde 1).
    """
    totales: dict[str, int] = {}
    for numero, linea in enumerate(texto.splitlines(), start=1):
        limpia = linea.strip()
        if not limpia or limpia.startswith("#"):
            continue
        campos = [campo.strip() for campo in linea.split(",")]
        if len(campos) != 3:
            raise ValueError(f"línea {numero}: se esperaban 3 campos y hay {len(campos)}")
        producto, cantidad, precio = campos
        if not producto:
            raise ValueError(f"línea {numero}: el producto está vacío")
        if not _ENTERO.fullmatch(cantidad) or not _ENTERO.fullmatch(precio):
            raise ValueError(f"línea {numero}: cantidad y precio deben ser enteros no negativos")
        clave = producto.lower()
        totales[clave] = totales.get(clave, 0) + int(cantidad) * int(precio)
    return totales
