"""Utilidades geométricas y de texto compartidas por las validaciones de ANP y de ejidos."""

from __future__ import annotations

import unicodedata
from typing import Any

import numpy as np
import pandas as pd
from pyproj import Geod
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon
from shapely.geometry.base import BaseGeometry
from shapely.geometry.polygon import orient

WGS84 = "EPSG:4326"
MEXICO_LCC = "EPSG:6372"  # México ITRF2008 / LCC, proyección de referencia de INEGI
GEOD = Geod(ellps="WGS84")


def fold(text: str) -> str:
    """Minúsculas y sin acentos, para comparar 'Queretaro' con 'Querétaro'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lower().strip()


def to_multipolygon(geom: BaseGeometry | None) -> MultiPolygon | None:
    """Uniforma a MultiPolygon. `make_valid` puede devolver GeometryCollection con
    líneas o puntos degenerados: se conservan solo las partes poligonales."""
    if geom is None or geom.is_empty:
        return None
    if isinstance(geom, MultiPolygon):
        return geom
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    if isinstance(geom, GeometryCollection):
        polygons = [p for part in geom.geoms for p in getattr(part, "geoms", [part]) if isinstance(p, Polygon)]
        return MultiPolygon(polygons) if polygons else None
    return None


def geodesic_area_ha(geom: BaseGeometry | None) -> float:
    """Área sobre el elipsoide WGS84 (independiente de cualquier proyección)."""
    if geom is None or geom.is_empty:
        return float("nan")
    # pyproj espera anillo exterior antihorario para dar área positiva.
    polygons = geom.geoms if isinstance(geom, MultiPolygon) else [geom]
    area_m2 = sum(GEOD.geometry_area_perimeter(orient(p, sign=1.0))[0] for p in polygons)
    return abs(area_m2) / 10_000


def records(df: pd.DataFrame, columns: list[str]) -> list[dict[str, Any]]:
    """Filas -> lista de dicts serializables (NaN -> None)."""
    return df[columns].astype(object).replace({np.nan: None, pd.NA: None}).to_dict(orient="records")
