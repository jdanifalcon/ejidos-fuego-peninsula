"""Pruebas del lector de KML del RAN y de la validación de ejidos (datos sintéticos)."""

from __future__ import annotations

from dataclasses import replace

import geopandas as gpd
import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from src.config import STATES, get_settings
from src.extract.ran import read_nucleos_kml
from src.process.validate_ran import validate_ejidos

KML = """<?xml version="1.0" encoding="utf-8" ?>
<kml xmlns="http://www.opengis.net/kml/2.2"><Document><Folder>
{placemarks}
</Folder></Document></kml>"""

PLACEMARK = """<Placemark><ExtendedData><SchemaData schemaUrl="#X">
  <SimpleData name="NOM_NUC">{nombre}</SimpleData><SimpleData name="MUNICIPIO">{mun}</SimpleData>
  <SimpleData name="CLAVE">{clave}</SimpleData><SimpleData name="tipo">Ejido</SimpleData>
  <SimpleData name="EDO_CLV">4</SimpleData><SimpleData name="PROGRAMA">{prog}</SimpleData>
</SchemaData></ExtendedData>
<MultiGeometry><Polygon><outerBoundaryIs><LinearRing><coordinates>{coords}</coordinates></LinearRing></outerBoundaryIs></Polygon></MultiGeometry>
</Placemark>"""


def _coords(x0, y0, x1, y1) -> str:
    return " ".join(f"{x},{y},0" for x, y in [(x0, y0), (x1, y0), (x1, y1), (x0, y1), (x0, y0)])


def test_kml_reader_extracts_schema_data(tmp_path):
    path = tmp_path / "demo.kml"
    path.write_text(KML.format(placemarks=PLACEMARK.format(
        nombre="LAS GRANJAS", mun="CARMEN", clave="04003001", prog="FANAR", coords=_coords(-91.0, 18.0, -90.9, 18.1)
    )), encoding="utf-8")  # fmt: skip
    gdf = read_nucleos_kml(path, STATES["camp"])
    row = gdf.iloc[0]
    assert (row["nombre"], row["municipio"], row["clave"], row["programa"]) == ("LAS GRANJAS", "CARMEN", "04003001", "FANAR")
    assert row["edo_clv"] == 4 and row["estado"] == "camp"
    assert gdf.crs.to_epsg() == 4326
    assert not gdf.geometry.iat[0].has_z


def _ejidos(rows: list[dict]) -> gpd.GeoDataFrame:
    base = {"estado": "camp", "tipo": "Ejido", "clv_unica": "x", "folio_matriz": "x", "edo_clv": 4, "mun_clv": 1,
            "nuc_clv": 1, "archivo_origen": "demo.kml"}  # fmt: skip
    return gpd.GeoDataFrame([base | r for r in rows], geometry="geometry", crs="EPSG:4326")


@pytest.fixture
def settings():
    return replace(get_settings(), states=(STATES["camp"],), overlap_min_ha=1.0)


def test_duplicate_key_parts_are_merged(settings):
    gdf = _ejidos([
        {"clave": "A", "nombre": "HOLBOX", "municipio": "X", "programa": "PROCEDE", "geometry": MultiPolygon([box(-90, 19, -89.99, 19.01)])},
        {"clave": "A", "nombre": "HOLBOX", "municipio": "X", "programa": "PROCEDE", "geometry": MultiPolygon([box(-89.9, 19, -89.89, 19.01)])},
    ])  # fmt: skip
    clean, _, report = validate_ejidos(gdf, settings)
    assert len(clean) == 1 and len(clean.geometry.iat[0].geoms) == 2
    assert report.duplicate_keys[0]["accion"].startswith("Partes")


def test_duplicate_key_reregistration_keeps_newest_program(settings):
    square = box(-90, 19, -89.98, 19.02)
    gdf = _ejidos([
        {"clave": "B", "nombre": "CHICOACAN", "municipio": "X", "programa": "Procede", "geometry": MultiPolygon([square])},
        {"clave": "B", "nombre": "CHICOACAN", "municipio": "X", "programa": "FANAR", "geometry": MultiPolygon([square.buffer(0.0005)])},
    ])  # fmt: skip
    clean, _, report = validate_ejidos(gdf, settings)
    assert len(clean) == 1 and clean["programa_norm"].iat[0] == "FANAR"
    assert "FANAR" in report.duplicate_keys[0]["accion"]


def test_invalid_overlap_and_text_normalization(settings):
    bowtie = Polygon([(-90, 19), (-89.9, 19.1), (-89.9, 19), (-90, 19.1), (-90, 19)])
    gdf = _ejidos([
        {"clave": "C", "nombre": "UNO", "municipio": "HECELCHAKAN", "programa": "Procede", "geometry": MultiPolygon([bowtie])},
        {"clave": "D", "nombre": "DOS", "municipio": "HECELCHAKÁN", "programa": "PROCEDE", "geometry": MultiPolygon([box(-89.5, 19, -89.4, 19.1)])},
        {"clave": "E", "nombre": "TRES", "municipio": "HECELCHAKAN", "programa": "Procede", "geometry": MultiPolygon([box(-89.45, 19, -89.35, 19.1)])},
    ])  # fmt: skip
    clean, overlaps, report = validate_ejidos(gdf, settings)
    assert clean.is_valid.all() and len(report.invalid_geometries) == 1
    assert set(clean["municipio_norm"]) == {"HECELCHAKÁN"}
    assert set(clean["programa_norm"]) == {"PROCEDE"}
    assert len(overlaps) == 1 and {overlaps.iloc[0]["clave_a"], overlaps.iloc[0]["clave_b"]} == {"D", "E"}
