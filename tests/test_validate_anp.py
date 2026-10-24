"""Pruebas de la validación con datos sintéticos pequeños."""

from __future__ import annotations

import geopandas as gpd
import pytest
from shapely.geometry import MultiPolygon, Polygon, box

from src.config import get_settings
from src.process.validate_anp import geodesic_area_ha, parse_estados, to_multipolygon, validate

# "Moño" (bowtie): polígono que se autointersecta -> inválido.
BOWTIE = Polygon([(-89.0, 20.0), (-88.9, 20.1), (-88.9, 20.0), (-89.0, 20.1), (-89.0, 20.0)])


def _layer(rows: list[dict], crs="EPSG:4326") -> gpd.GeoDataFrame:
    base = {"FID": 0, "NUM_ANP": 0, "ID_DOF": "x", "CAT_MANEJO": "PN", "REGION": "r", "S_TERRES": 0.0,
            "S_MARINA": 0.0, "PRIM_DEC": 0, "ULT_DOF": 0, "PM": None, "CERT_SINAP": None,
            "Shape__Area": 0.0, "Shape__Length": 0.0}  # fmt: skip
    return gpd.GeoDataFrame([base | r for r in rows], geometry="geometry", crs=crs)


@pytest.fixture
def settings():
    return get_settings()


@pytest.mark.parametrize(
    ("text", "states", "unknown"),
    [
        ("Campeche y Yucatán", ["Campeche", "Yucatán"], []),
        ("Yucatán y Campeche", ["Campeche", "Yucatán"], []),
        ("B.C., B.C.S., Sonora y Sinaloa", ["Baja California", "Baja California Sur", "Sinaloa", "Sonora"], []),
        ("Queretaro", ["Querétaro"], []),
        ("Estado de México", ["México"], []),
        ("Se ubica en el Golfo de California", [], ["Se ubica en el Golfo de California"]),
        (None, [], []),
    ],
)
def test_parse_estados(text, states, unknown):
    assert parse_estados(text) == (states, unknown)


def test_to_multipolygon_drops_non_polygonal_parts():
    from shapely.geometry import GeometryCollection, LineString

    mixed = GeometryCollection([box(0, 0, 1, 1), LineString([(0, 0), (2, 2)])])
    result = to_multipolygon(mixed)
    assert isinstance(result, MultiPolygon) and len(result.geoms) == 1


def test_geodesic_area_of_one_degree_cell_at_equator():
    # Una celda de 1°×1° en el ecuador mide ~12,308 km² = ~1,230,800 ha.
    assert geodesic_area_ha(box(0, 0, 1, 1)) == pytest.approx(1_230_800, rel=0.01)


def test_invalid_geometry_is_repaired_and_flagged(settings):
    gdf = _layer([
        {"ID_ANP": "A", "NOMBRE": "Moño", "ESTADOS": "Yucatán", "SUPERFICIE": 100.0, "geometry": BOWTIE},
        {"ID_ANP": "B", "NOMBRE": "Caja", "ESTADOS": "Campeche", "SUPERFICIE": 100.0, "geometry": box(-90, 19, -89.9, 19.1)},
    ])  # fmt: skip
    clean, report = validate(gdf, settings)
    assert clean.is_valid.all()
    assert (clean.geom_type == "MultiPolygon").all()
    assert clean.set_index("id_anp").loc["A", "geom_reparada"]
    assert len(report.invalid_geometries) == 1
    assert "Self-intersection" in report.invalid_geometries[0]["motivo_invalidez"]


def test_duplicates_nulls_and_peninsula(settings):
    square = box(-87.5, 20.0, -87.4, 20.1)
    gdf = _layer([
        {"ID_ANP": "A", "NOMBRE": "Uno", "ESTADOS": "Quintana Roo", "SUPERFICIE": 1.0, "geometry": square},
        {"ID_ANP": "A", "NOMBRE": "Uno bis", "ESTADOS": "Quintana Roo", "SUPERFICIE": 1.0, "geometry": square},
        {"ID_ANP": "C", "NOMBRE": "Sin geometría", "ESTADOS": "Jalisco", "SUPERFICIE": 1.0, "geometry": None},
        {"ID_ANP": "D", "NOMBRE": "Norte", "ESTADOS": "  ", "SUPERFICIE": 1.0, "geometry": box(-100, 25, -99.9, 25.1)},
    ])  # fmt: skip
    clean, report = validate(gdf, settings)
    assert len(report.null_geometries) == 1
    assert len(report.duplicate_ids) == 2 and len(report.duplicate_geometries) == 2
    assert clean["id_anp"].is_unique
    assert report.null_counts["estados"] == 1  # cadena en blanco cuenta como vacía
    assert report.peninsula_count == 1


def test_projected_input_is_reprojected_to_wgs84(settings):
    projected = _layer(
        [{"ID_ANP": "A", "NOMBRE": "X", "ESTADOS": "Yucatán", "SUPERFICIE": 1.0, "geometry": box(-89, 20, -88.9, 20.1)}]
    ).to_crs("EPSG:6372")
    clean, report = validate(projected, settings)
    assert report.crs_input == "EPSG:6372"
    assert clean.crs.to_epsg() == 4326
    assert clean.total_bounds[0] == pytest.approx(-89, abs=1e-6)


def test_area_discrepancy_is_reported(settings):
    cell = box(-89.0, 20.0, -88.99, 20.01)  # ~116 ha
    gdf = _layer([{"ID_ANP": "A", "NOMBRE": "X", "ESTADOS": "Yucatán", "SUPERFICIE": 1000.0, "geometry": cell}])
    _, report = validate(gdf, settings)
    assert len(report.area_discrepancies) == 1
    assert report.area_discrepancies[0]["dif_superficie_pct"] < -80
