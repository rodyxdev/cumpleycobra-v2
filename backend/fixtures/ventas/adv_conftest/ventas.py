"""Resumen de ventas por producto (entrega incompleta: no maneja comentarios ni valida)."""


def resumir_ventas(texto: str) -> dict[str, int]:
    """Devuelve el total vendido por producto, en centavos."""
    totales: dict[str, int] = {}
    for linea in texto.splitlines():
        if not linea.strip():
            continue
        producto, cantidad, precio = (campo.strip() for campo in linea.split(","))
        clave = producto.lower()
        totales[clave] = totales.get(clave, 0) + int(cantidad) * int(precio)
    return totales
