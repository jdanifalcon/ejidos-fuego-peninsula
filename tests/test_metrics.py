"""Pruebas de los indicadores a partir de métricas anuales sintéticas."""

from __future__ import annotations

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

from src.gee.run import wide_to_long
from src.process.metrics import headline, summarize_ejidos, summarize_states


@pytest.fixture
def ejidos():
    return gpd.GeoDataFrame(
        {"clave": ["A", "B"], "estado": ["camp", "camp"], "nombre": ["UNO", "DOS"], "municipio_norm": ["M", "M"],
         "tipo": ["Ejido", "Ejido"], "area_ha": [1000.0, 500.0]},  # fmt: skip
        geometry=[box(0, 0, 1, 1), box(1, 0, 2, 1)], crs="EPSG:4326",
    ).rename_geometry("geom")


@pytest.fixture
def wide():
    return pd.DataFrame({
        "clave": ["A", "B"], "bosque2000_ha": [800.0, 0.0],
        "perdida_ha_2020": [40.0, 0.0], "quemada_ha_2020": [100.0, 5.0], "quemada_bosque_ha_2020": [60.0, 0.0],
        "perdida_fuego_ha_2020": [10.0, 0.0], "perdida_fuego_buffer_ha_2020": [20.0, 0.0], "focos_firms_2020": [3.0, 0.0],
        "perdida_ha_2021": [60.0, 0.0], "quemada_ha_2021": [0.0, 0.0], "quemada_bosque_ha_2021": [0.0, 0.0],
        "perdida_fuego_ha_2021": [30.0, 0.0], "perdida_fuego_buffer_ha_2021": [30.0, 0.0], "focos_firms_2021": [0.0, 0.0],
    })  # fmt: skip


def test_wide_to_long(wide):
    long = wide_to_long(wide, (2020, 2021))
    assert len(long) == 4
    assert set(long.columns) >= {"clave", "anio", "perdida_ha", "perdida_fuego_ha", "focos_firms", "bosque2000_ha"}
    assert long.query("clave == 'A' and anio == 2021")["perdida_fuego_ha"].iat[0] == 30


def test_summary_percentages_and_rank(wide, ejidos):
    long = wide_to_long(wide, (2020, 2021))
    summary = summarize_ejidos(long, ejidos).set_index("clave")
    assert summary.loc["A", "perdida_ha"] == 100
    assert summary.loc["A", "pct_perdida_fuego"] == 40.0  # 40 / 100
    assert summary.loc["A", "pct_fuego_area"] == 4.0  # 40 / 1000
    assert summary.loc["A", "anio_max_fuego"] == 2021
    assert pd.isna(summary.loc["B", "pct_perdida_fuego"])  # sin pérdida: 0/0 no es 0 %
    assert summary.loc["A", "rank_fuego_ha"] == 1


def test_headline(wide, ejidos):
    long = wide_to_long(wide, (2020, 2021))
    summary = summarize_ejidos(long, ejidos)
    series = summarize_states(long, ejidos)
    h = headline(long, summary, series)["camp"]
    assert h["pct_perdida_fuego"] == 40.0
    assert h["pct_perdida_fuego_buffer"] == 50.0
    assert h["r_firms_anual"] is None  # con solo 2 años no se calcula la correlación
    assert h["anio_max_fuego"] == 2021 and h["top_ejido"]["clave"] == "A"
