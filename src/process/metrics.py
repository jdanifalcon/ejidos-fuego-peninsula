"""Indicadores a partir de las métricas anuales de Earth Engine.

- `summarize_ejidos`: totales del periodo por ejido, porcentajes y ranking.
- `summarize_states`: serie anual por estado.
- `headline`: las cifras que responden la pregunta del proyecto.

Notas de interpretación:
- `quemada_ha` sumada en varios años son *hectáreas-año*: una misma superficie que
  arde dos veces cuenta dos veces. Por eso no se expresa como % del ejido.
- Los totales estatales suman ejidos. Es válido porque la validación mostró que los
  traslapes entre ejidos son despreciables (ver reporte de calidad).
"""

from __future__ import annotations

from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd

SUM_COLUMNS = ["perdida_ha", "quemada_ha", "quemada_bosque_ha", "perdida_fuego_ha", "perdida_fuego_buffer_ha", "focos_firms"]


def _pct(num: pd.Series | float, den: pd.Series | float) -> pd.Series | float:
    """Porcentaje con 0/0 -> NaN (no hay pérdida que repartir)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        result = 100 * np.asarray(num, dtype=float) / np.asarray(den, dtype=float)
    result = np.where(np.isfinite(result), result, np.nan)
    return pd.Series(result, index=num.index) if isinstance(num, pd.Series) else float(result)


def summarize_ejidos(long: pd.DataFrame, ejidos: gpd.GeoDataFrame) -> pd.DataFrame:
    """Una fila por ejido con totales del periodo, porcentajes y ranking estatal."""
    cols = [c for c in SUM_COLUMNS if c in long]
    totals = long.groupby("clave")[cols].sum()
    totals["bosque2000_ha"] = long.groupby("clave")["bosque2000_ha"].first()
    # Año con más pérdida asociada a fuego (solo si hubo alguna).
    peak = long.loc[long["perdida_fuego_ha"] > 0].sort_values("perdida_fuego_ha").groupby("clave").tail(1)
    totals["anio_max_fuego"] = peak.set_index("clave")["anio"]

    attrs = pd.DataFrame(ejidos.drop(columns="geom"))[["clave", "estado", "nombre", "municipio_norm", "tipo", "area_ha"]]
    out = attrs.merge(totals.reset_index(), on="clave", how="left")
    out["pct_perdida_fuego"] = _pct(out["perdida_fuego_ha"], out["perdida_ha"]).round(1)
    out["pct_perdida_area"] = _pct(out["perdida_ha"], out["area_ha"]).round(2)
    out["pct_fuego_area"] = _pct(out["perdida_fuego_ha"], out["area_ha"]).round(2)
    out["pct_perdida_bosque"] = _pct(out["perdida_ha"], out["bosque2000_ha"]).round(2)
    out["rank_fuego_ha"] = out.groupby("estado")["perdida_fuego_ha"].rank(ascending=False, method="min").astype(int)
    out["rank_fuego_pct_area"] = out.groupby("estado")["pct_fuego_area"].rank(ascending=False, method="min").astype(int)
    out["anio_max_fuego"] = out["anio_max_fuego"].astype("Int64")
    float_cols = [c for c in cols if c != "focos_firms"] + ["bosque2000_ha"]
    out[float_cols] = out[float_cols].round(2)
    return out


def summarize_states(long: pd.DataFrame, ejidos: gpd.GeoDataFrame) -> pd.DataFrame:
    """Serie anual por estado (suma de ejidos)."""
    cols = [c for c in SUM_COLUMNS if c in long]
    merged = long.merge(pd.DataFrame(ejidos[["clave", "estado"]]), on="clave")
    series = merged.groupby(["estado", "anio"])[cols].sum().reset_index()
    series["pct_perdida_fuego"] = _pct(series["perdida_fuego_ha"], series["perdida_ha"]).round(1)
    return series.round(2)


def headline(long: pd.DataFrame, summary: pd.DataFrame, series: pd.DataFrame) -> dict[str, dict[str, Any]]:
    """Cifras clave por estado: la respuesta a la pregunta del proyecto."""
    result = {}
    for code, s in series.groupby("estado"):
        ej = summary[summary["estado"] == code]
        loss, fire = s["perdida_ha"].sum(), s["perdida_fuego_ha"].sum()
        # Verificación independiente: ¿los años con más focos FIRMS son los de más pérdida en área quemada?
        # (Por ejido-año no sirve: casi todo ejido grande tiene al menos un foco cada año.)
        firms_r = s["focos_firms"].corr(s["perdida_fuego_ha"]) if "focos_firms" in s and len(s) > 2 else np.nan
        top = ej.sort_values("perdida_fuego_ha", ascending=False).head(1).iloc[0]
        result[code] = {
            "ejidos": int(len(ej)),
            "area_ejidos_ha": round(float(ej["area_ha"].sum()), 0),
            "bosque2000_ha": round(float(ej["bosque2000_ha"].sum()), 0),
            "perdida_ha": round(float(loss), 0),
            "perdida_fuego_ha": round(float(fire), 0),
            "pct_perdida_fuego": round(_pct(fire, loss), 1),
            "pct_perdida_fuego_buffer": round(_pct(s["perdida_fuego_buffer_ha"].sum(), loss), 1)
            if "perdida_fuego_buffer_ha" in s else None,
            "r_firms_anual": None if pd.isna(firms_r) else round(float(firms_r), 2),
            "anio_max_perdida": int(s.loc[s["perdida_ha"].idxmax(), "anio"]),
            "anio_max_fuego": int(s.loc[s["perdida_fuego_ha"].idxmax(), "anio"]),
            "ejidos_con_fuego": int((ej["perdida_fuego_ha"] > 0).sum()),
            # Concentración: qué parte de la pérdida asociada a fuego ocurre en el 10 % de ejidos más afectados.
            "pct_fuego_top10pct": round(
                _pct(ej.nlargest(max(1, len(ej) // 10), "perdida_fuego_ha")["perdida_fuego_ha"].sum(), fire), 1
            ),
            "top_ejido": {"clave": top["clave"], "nombre": top["nombre"], "municipio": top["municipio_norm"],
                          "perdida_fuego_ha": round(float(top["perdida_fuego_ha"]), 0),
                          "pct_perdida_fuego": None if pd.isna(top["pct_perdida_fuego"]) else float(top["pct_perdida_fuego"])},  # fmt: skip
        }
    return result
