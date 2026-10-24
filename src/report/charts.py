"""Gráficas estáticas (PNG) para el README, el reporte y la publicación.

Paleta: slots 1-3 de la paleta categórica de referencia (validados para daltonismo):
azul = pérdida forestal total, naranja = pérdida asociada a fuego, aqua = área quemada.
Un solo eje por panel: pérdida y área quemada tienen escalas distintas, así que van en
paneles separados (nunca doble eje Y).
"""

from __future__ import annotations

import logging
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # sin ventana: corre igual en CI
import matplotlib.pyplot as plt
import pandas as pd
from matplotlib.ticker import FuncFormatter

log = logging.getLogger(__name__)

BLUE, ORANGE, AQUA = "#2a78d6", "#eb6834", "#1baf7a"
INK, INK_2, MUTED, GRID, BASE, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"

plt.rcParams.update({
    "font.family": ["Segoe UI", "DejaVu Sans", "sans-serif"],
    "font.size": 10,
    "axes.edgecolor": BASE, "axes.labelcolor": INK_2, "axes.titlecolor": INK,
    "axes.spines.top": False, "axes.spines.right": False, "axes.spines.left": False,
    "axes.grid": True, "axes.grid.axis": "y", "grid.color": GRID, "grid.linewidth": 0.6,
    "xtick.color": MUTED, "ytick.color": MUTED, "figure.facecolor": SURFACE, "axes.facecolor": SURFACE,
    "savefig.facecolor": SURFACE, "savefig.dpi": 160,
})  # fmt: skip

THOUSANDS = FuncFormatter(lambda v, _: f"{v:,.0f}")


def ranking_chart(summary: pd.DataFrame, state_names: dict[str, str], path: Path, top: int = 12) -> Path:
    """Barras horizontales apiladas: pérdida asociada a fuego + resto de la pérdida, por ejido."""
    states = [s for s in state_names if s in set(summary["estado"])]
    fig, axes = plt.subplots(1, len(states), figsize=(6.2 * len(states), 0.42 * top + 1.6), squeeze=False)
    for ax, code in zip(axes[0], states):
        data = summary[summary["estado"] == code].nlargest(top, "perdida_fuego_ha").iloc[::-1]
        labels = [_short(f"{n.title()} ({m.title()})") for n, m in zip(data["nombre"], data["municipio_norm"])]
        rest = (data["perdida_ha"] - data["perdida_fuego_ha"]).clip(lower=0)
        ax.barh(labels, data["perdida_fuego_ha"], color=ORANGE, height=0.62, label="Pérdida en área quemada")
        # Separación de 2 px entre segmentos apilados: borde del color de la superficie.
        ax.barh(labels, rest, left=data["perdida_fuego_ha"], color=BLUE, alpha=0.35, height=0.62,
                edgecolor=SURFACE, linewidth=1.5, label="Resto de la pérdida forestal")  # fmt: skip
        # Etiqueta al final de la barra completa, fuera de las marcas.
        for y, (total, fire, pct) in enumerate(zip(data["perdida_ha"], data["perdida_fuego_ha"], data["pct_perdida_fuego"])):
            ax.text(total, y, f"  {fire:,.0f} ha · {pct:.0f} %", va="center", ha="left", fontsize=8.5, color=INK_2)
        ax.set_xlim(0, data["perdida_ha"].max() * 1.32)
        ax.set_title(state_names[code], loc="left", fontsize=12, fontweight="bold")
        ax.xaxis.set_major_formatter(THOUSANDS)
        ax.set_xlabel("hectáreas, 2015–2024")
        ax.grid(axis="x", color=GRID, linewidth=0.6)
        ax.grid(axis="y", visible=False)
        ax.tick_params(axis="y", length=0, labelcolor=INK)
    handles, names = axes[0][0].get_legend_handles_labels()
    fig.legend(handles, names, loc="upper left", bbox_to_anchor=(0.01, 0.94), ncol=2, frameon=False, fontsize=9)
    fig.suptitle("Ejidos con más pérdida forestal en área quemada", x=0.01, ha="left", fontsize=14, fontweight="bold")
    fig.text(0.01, 0.005, "Fuente: Hansen GFC v1.13, MODIS MCD64A1, RAN. Etiqueta: ha en área quemada · % de la pérdida del ejido.",
             fontsize=8, color=MUTED)  # fmt: skip
    fig.tight_layout(rect=(0, 0.03, 1, 0.91))
    fig.savefig(path)
    plt.close(fig)
    return path


def _short(text: str, limit: int = 36) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def annual_chart(series: pd.DataFrame, state_names: dict[str, str], path: Path) -> Path:
    """Pequeños múltiplos: fila 1 pérdida total vs. asociada a fuego; fila 2 área quemada."""
    states = [s for s in state_names if s in set(series["estado"])]
    fig, axes = plt.subplots(2, len(states), figsize=(6.2 * len(states), 6.4), squeeze=False, sharex=True,
                             gridspec_kw={"height_ratios": [1.3, 1]})  # fmt: skip
    for col, code in enumerate(states):
        s = series[series["estado"] == code].sort_values("anio")
        top, bottom = axes[0][col], axes[1][col]
        top.plot(s["anio"], s["perdida_ha"], color=BLUE, lw=2, marker="o", ms=5, label="Pérdida forestal")
        top.plot(s["anio"], s["perdida_fuego_ha"], color=ORANGE, lw=2, marker="o", ms=5, label="…en área quemada")
        top.fill_between(s["anio"], 0, s["perdida_fuego_ha"], color=ORANGE, alpha=0.12, lw=0)
        peak = s.loc[s["perdida_fuego_ha"].idxmax()]
        top.annotate(f"{peak['perdida_fuego_ha']:,.0f} ha\n({peak['pct_perdida_fuego']:.0f} % de la pérdida)",
                     (peak["anio"], peak["perdida_fuego_ha"]), xytext=(8, 14), textcoords="offset points",
                     fontsize=8.5, color=INK_2)  # fmt: skip
        top.set_title(state_names[code], loc="left", fontsize=12, fontweight="bold")
        top.set_ylabel("ha / año")
        bottom.bar(s["anio"], s["quemada_ha"], color=AQUA, width=0.62)
        # Una sola serie: el subtítulo la nombra, sin caja de leyenda.
        bottom.set_title("Área quemada MCD64A1, todas las coberturas", loc="left", fontsize=10, color=INK_2)
        bottom.set_ylabel("ha / año")
        for ax in (top, bottom):
            ax.yaxis.set_major_formatter(THOUSANDS)
            ax.set_ylim(bottom=0)
        bottom.set_xticks(s["anio"])
        bottom.tick_params(axis="x", rotation=0)
    axes[0][0].legend(loc="upper left", frameon=False, fontsize=9)
    fig.suptitle("Pérdida forestal y área quemada en ejidos, por año", x=0.01, ha="left", fontsize=14, fontweight="bold")
    fig.text(0.01, 0.005, "Pérdida: Hansen GFC (dosel ≥ 30 % en 2000). Área quemada: MODIS MCD64A1 (500 m). "
             "«En área quemada»: pérdida dentro de área quemada del mismo año o del anterior.", fontsize=8, color=MUTED)  # fmt: skip
    fig.tight_layout(rect=(0, 0.03, 1, 0.95))
    fig.savefig(path)
    plt.close(fig)
    return path


def make_charts(summary: pd.DataFrame, series: pd.DataFrame, state_names: dict[str, str], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = [
        ranking_chart(summary, state_names, out_dir / "ranking_ejidos.png"),
        annual_chart(series, state_names, out_dir / "serie_anual.png"),
    ]
    log.info("Gráficas: %s", ", ".join(p.name for p in paths))
    return paths
