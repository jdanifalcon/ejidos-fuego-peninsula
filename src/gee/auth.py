"""Inicialización de Google Earth Engine.

Requisitos (una sola vez por equipo):
1. Un proyecto de Google Cloud registrado para Earth Engine (uso no comercial gratuito).
2. `earthengine authenticate` para guardar las credenciales del usuario.
3. `EE_PROJECT=<id-del-proyecto>` en `.env`.
"""

from __future__ import annotations

import logging
from types import ModuleType

log = logging.getLogger(__name__)

_initialized: set[str] = set()


def init_ee(project: str | None) -> ModuleType:
    """Inicializa Earth Engine una vez por proyecto y devuelve el módulo `ee`."""
    import ee  # import tardío: el resto del pipeline funciona sin earthengine-api configurado

    if not project:
        raise RuntimeError("Falta EE_PROJECT en .env (ID del proyecto de Google Cloud con Earth Engine).")
    if project not in _initialized:
        try:
            ee.Initialize(project=project)
        except Exception as exc:  # ee lanza varios tipos según falte credencial o permiso
            raise RuntimeError(
                f"No se pudo inicializar Earth Engine con el proyecto '{project}'. "
                "Ejecuta `earthengine authenticate` y verifica que el proyecto esté registrado para Earth Engine. "
                f"Detalle: {exc}"
            ) from exc
        _initialized.add(project)
        log.info("Earth Engine inicializado (proyecto %s)", project)
    return ee
