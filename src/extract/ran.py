"""Lectura de los polígonos de núcleos agrarios del RAN (Registro Agrario Nacional).

Los KML de datos abiertos del RAN guardan los atributos en
`<ExtendedData><SchemaData><SimpleData name="...">`. El driver KML de GDAL que trae
pyogrio no lee esa estructura (devuelve `Name` y `Description` vacíos), así que se
leen con un analizador XML propio. Se usa `iterparse` y se libera cada `Placemark`
después de leerlo, para no cargar el archivo completo en memoria (Chiapas pesa 15 MB).

Ruta esperada: data/ran/<estado>/ran_da_dgcat_poligonos_nucleos_agrarios_<estado>.kml
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import MultiPolygon, Polygon

from ..config import Settings, State

log = logging.getLogger(__name__)

KML_NS = "{http://www.opengis.net/kml/2.2}"

# Nombre en el KML -> nombre en el proyecto (minúsculas y en español claro).
FIELD_MAP = {
    "CLAVE": "clave",
    "NOM_NUC": "nombre",
    "MUNICIPIO": "municipio",
    "Clv_Unica": "clv_unica",
    "Fol_Matriz": "folio_matriz",
    "tipo": "tipo",
    "EDO_CLV": "edo_clv",
    "MUN_CLV": "mun_clv",
    "NUC_CLV": "nuc_clv",
    "PROGRAMA": "programa",
}
INT_FIELDS = ("edo_clv", "mun_clv", "nuc_clv")


def kml_path(settings: Settings, state: State) -> Path:
    return settings.ran_dir / state.code / f"ran_da_dgcat_poligonos_nucleos_agrarios_{state.code}.kml"


def _ring(element: ET.Element | None) -> list[tuple[float, float]]:
    """'lon,lat,z lon,lat,z ...' -> [(lon, lat), ...]. Se descarta la Z."""
    if element is None or not element.text:
        return []
    return [tuple(map(float, c.split(",")[:2])) for c in element.text.split()]


def _placemark_geometry(placemark: ET.Element) -> MultiPolygon | None:
    polygons = []
    for poly in placemark.iter(f"{KML_NS}Polygon"):
        outer = _ring(poly.find(f"{KML_NS}outerBoundaryIs/{KML_NS}LinearRing/{KML_NS}coordinates"))
        holes = [_ring(r) for r in poly.findall(f"{KML_NS}innerBoundaryIs/{KML_NS}LinearRing/{KML_NS}coordinates")]
        if len(outer) >= 4:
            polygons.append(Polygon(outer, [h for h in holes if len(h) >= 4]))
    return MultiPolygon(polygons) if polygons else None


def read_nucleos_kml(path: Path, state: State) -> gpd.GeoDataFrame:
    """Lee un KML de núcleos agrarios del RAN con todos sus atributos."""
    if not path.exists():
        raise FileNotFoundError(f"No existe {path}. Descarga el KML del RAN y colócalo ahí (ver README).")
    rows = []
    for _, element in ET.iterparse(path, events=("end",)):
        if element.tag != f"{KML_NS}Placemark":
            continue
        attrs = {d.get("name"): (d.text or "").strip() for d in element.iter(f"{KML_NS}SimpleData")}
        row = {new: attrs.get(old) or None for old, new in FIELD_MAP.items()}
        row["geometry"] = _placemark_geometry(element)
        rows.append(row)
        element.clear()

    gdf = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")  # KML siempre es WGS84
    for col in INT_FIELDS:
        gdf[col] = gdf[col].astype("Int64")
    gdf.insert(0, "estado", state.code)
    gdf["archivo_origen"] = path.name
    log.info("RAN %s: %d núcleos agrarios leídos de %s", state.name, len(gdf), path.name)
    return gdf


def read_states(settings: Settings) -> gpd.GeoDataFrame:
    """Concatena los núcleos agrarios de todos los estados configurados en STATES."""
    frames = [read_nucleos_kml(kml_path(settings, s), s) for s in settings.states]
    return gpd.GeoDataFrame(pd.concat(frames, ignore_index=True), crs="EPSG:4326")
