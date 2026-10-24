"""Reporte de calidad en Markdown (para el repo) y HTML (para GitHub Pages).

El contenido se arma una sola vez como una lista de bloques (título, párrafo,
tabla) y se renderiza a los dos formatos, así ambos dicen exactamente lo mismo.
"""

from __future__ import annotations

import html
import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..config import Settings
from ..extract.arcgis import ExtractResult
from ..process.validate_anp import CATEGORIES, ValidationReport

log = logging.getLogger(__name__)

NULL_NOTES = {
    "pm": "Fecha de publicación del Programa de Manejo. Vacío = la ANP aún no tiene programa publicado.",
    "cert_sinap": "Certificado de incorporación al SINAP. Vacío = no incorporada.",
}


@dataclass
class Doc:
    """Documento mínimo independiente del formato de salida."""

    title: str
    blocks: list[tuple] = field(default_factory=list)

    def h2(self, text: str) -> None:
        self.blocks.append(("h2", text))

    def h3(self, text: str) -> None:
        self.blocks.append(("h3", text))

    def p(self, text: str) -> None:
        self.blocks.append(("p", text))

    def table(self, headers: list[str], rows: list[list[Any]]) -> None:
        self.blocks.append(("table", headers, rows))


def _fmt(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "Sí" if value else "No"
    if isinstance(value, float):
        return f"{value:,.2f}"
    if isinstance(value, int):
        return f"{value:,}"
    return str(value)


def _ok(condition: bool) -> str:
    return "✅" if condition else "⚠️"


def build_document(
    settings: Settings,
    extract: ExtractResult,
    report: ValidationReport,
    load: dict[str, Any] | None,
    web: dict[str, Any],
) -> Doc:
    doc = Doc("Reporte de calidad · Ejidos, fuego y pérdida forestal")
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    doc.p(f"Generado automáticamente por el pipeline el {generated}.")
    doc.h2("1. Capa de contexto: ANP federales (ArcGIS FeatureServer)")
    doc.p(f"Fuente: [{extract.layer_name}]({extract.source_url}).")

    # --- Resumen ---
    doc.h3("Resumen")
    n_invalid = len(report.invalid_geometries)
    n_fixed = sum(1 for g in report.invalid_geometries if g["valida_despues"])
    doc.table(
        ["Verificación", "Resultado", ""],
        [
            ["Entidades esperadas / recibidas", f"{extract.expected_count} / {extract.received_count}",
             _ok(extract.expected_count == extract.received_count)],
            ["Páginas descargadas", f"{extract.pages} × {extract.page_size} ({extract.method})", "✅"],
            ["CRS de entrada → salida", f"{report.crs_input} → {report.crs_output}", "✅"],
            ["Geometrías nulas o vacías", len(report.null_geometries), _ok(not report.null_geometries)],
            ["Geometrías inválidas → reparadas", f"{n_invalid} → {n_fixed}", _ok(n_invalid == n_fixed)],
            ["Claves duplicadas (id_anp)", len(report.duplicate_ids), _ok(not report.duplicate_ids)],
            ["Geometrías duplicadas", len(report.duplicate_geometries), _ok(not report.duplicate_geometries)],
            ["Registros con observaciones en ESTADOS", len(report.estados_issues), _ok(not report.estados_issues)],
            [f"Superficie con diferencia > {report.area_tolerance_pct:g} %", len(report.area_discrepancies),
             _ok(not report.area_discrepancies)],
            ["ANP en la Península de Yucatán", report.peninsula_count, "ℹ️"],
            ["Cargado en PostGIS", f"{load['rows']} filas en `{load['table']}`" if load else "Omitido (--skip-db)",
             _ok(bool(load) and load["rows"] == report.total_output)],
        ],
    )  # fmt: skip

    # --- Geometrías ---
    doc.h3("Geometrías inválidas")
    if report.invalid_geometries:
        doc.p(
            "Se repararon con `shapely.make_valid` (equivalente a `ST_MakeValid`). Se conservan solo las partes "
            "poligonales y todo se uniforma a MultiPolygon. Cada registro reparado queda marcado en las columnas "
            "`geom_reparada` y `motivo_invalidez`."
        )
        doc.table(
            ["id_anp", "Nombre", "Motivo (GEOS)", "¿Válida después?"],
            [[g["id_anp"], g["nombre"], g["motivo_invalidez"], g["valida_despues"]] for g in report.invalid_geometries],
        )
    else:
        doc.p("No se encontraron geometrías inválidas.")

    # --- Duplicados ---
    doc.h3("Duplicados")
    doc.p(
        f"Por clave `id_anp`: **{len(report.duplicate_ids)}**. Por geometría idéntica (WKB normalizado): "
        f"**{len(report.duplicate_geometries)}**."
    )

    # --- Campos vacíos ---
    doc.h3("Campos vacíos")
    empties = {k: v for k, v in report.null_counts.items() if v}
    if empties:
        doc.table(
            ["Campo", "Vacíos", "%", "Interpretación"],
            [[k, v, f"{v / report.total_output:.0%}", NULL_NOTES.get(k, "")] for k, v in empties.items()],
        )
    else:
        doc.p("Ningún campo tiene valores nulos o en blanco.")

    # --- ESTADOS ---
    doc.h3("Consistencia del campo ESTADOS")
    doc.p(
        "El campo original se conserva. Se agrega `estados_norm` con nombres oficiales en orden alfabético "
        "(p. ej. «Yucatán y Campeche» y «Campeche y Yucatán» → `Campeche; Yucatán`). Los textos libres "
        "(zonas marinas) quedan con `estados_norm` vacío y `estados_texto_libre = true` para revisión manual."
    )
    if report.estados_issues:
        doc.table(
            ["id_anp", "Nombre", "ESTADOS original", "Normalizado", "Observación"],
            [[i["id_anp"], i["nombre"], i["estados"], i["estados_norm"], i["observacion"]] for i in report.estados_issues],
        )

    # --- Superficie ---
    doc.h3("Superficie decretada vs. calculada")
    bias = report.area_6372_vs_geodesic_pct
    doc.p(
        "La referencia es el área **geodésica** (sobre el elipsoide WGS84). También se calcula en EPSG:6372 "
        f"(México LCC): por ser una proyección conforme, su área difiere de la geodésica entre {bias.get('min')} % "
        f"y {bias.get('max')} % (mediana {bias.get('mediana')} %). Para áreas oficiales conviene la geodésica "
        "o una proyección de áreas iguales."
    )
    if report.area_discrepancies:
        doc.p(
            f"ANP cuya geometría difiere más de {report.area_tolerance_pct:g} % de la superficie decretada "
            "(posibles cambios de poligonal, decretos con superficie redondeada o geometrías incompletas):"
        )
        doc.table(
            ["id_anp", "Nombre", "Decretada (ha)", "Calculada (ha)", "Diferencia %"],
            [[a["id_anp"], a["nombre"], a["superficie"], a["area_ha_geodesica"], a["dif_superficie_pct"]]
             for a in report.area_discrepancies],  # fmt: skip
        )

    # --- Categorías ---
    doc.h3("ANP por categoría de manejo")
    doc.table(
        ["Clave", "Categoría", "ANP"],
        [[k, CATEGORIES.get(k, k), v] for k, v in report.category_counts.items()],
    )

    # --- PostGIS ---
    doc.h3("Carga en PostGIS")
    if load:
        doc.table(
            ["Comprobación", "Resultado"],
            [
                ["Tabla", f"`{load['table']}`"],
                ["Filas", load["rows"]],
                ["Geometrías inválidas en la base (ST_IsValid)", load["invalid"]],
                ["Tipos de geometría distintos", load["geometry_types"]],
                ["SRID", load["srid"]],
                ["Superficie total (ha, geography)", load["total_ha"]],
                ["Versión de PostGIS", load["postgis_version"]],
            ],
        )
        doc.p("Índices: " + ", ".join(f"`{i['indexname']}`" for i in load["indexes"]))
        sample = load["sample_query"]
        doc.p(f"Consulta de ejemplo: ANP a menos de {sample['radius_km']} km de {sample['name']} (`ST_DWithin` sobre `geography`).")
        doc.table(["Nombre", "Categoría", "Distancia (km)"], [[r["nombre"], r["cat_manejo"], r["km"]] for r in sample["results"]])
    else:
        doc.p("La carga se omitió en esta ejecución (`--skip-db`).")

    # --- Web ---
    doc.h3("Exportación para el mapa web")
    doc.p(
        f"`web/data/anp.geojson`: {web['total']} ANP, simplificadas con tolerancia de "
        f"{web['tolerancia_simplificacion_m']:g} m en EPSG:6372 y coordenadas a 5 decimales → {web['tamano_mb']} MB."
    )
    return doc


def add_ran_sections(doc: Doc, ran: Any, ran_load: dict[str, Any] | None, settings: Settings) -> None:
    """Sección 2: validación de los núcleos agrarios del RAN."""
    doc.h2("2. Ejidos: núcleos agrarios del RAN")
    doc.p(
        "Fuente: KML de datos abiertos del Registro Agrario Nacional. Los atributos vienen en "
        "`ExtendedData/SchemaData`, que el driver KML de GDAL no lee; se usa un lector XML propio."
    )
    doc.h3("Resumen por estado")
    doc.table(
        ["Estado", "Registros leídos", "Núcleos tras validar", "Ejidos", "Comunidades", "Superficie (ha)"],
        [[v["nombre"], v["leidos"], v["salida"], v["ejidos"], v["comunidades"], v["area_ha"]] for v in ran.per_state.values()],
    )
    doc.h3("Geometrías")
    doc.p(
        f"CRS de entrada: `{ran.crs_input}` (KML es siempre WGS84). Coordenadas con Z (siempre 0) pasadas a 2D. "
        f"Geometrías nulas: **{len(ran.null_geometries)}**. Inválidas reparadas con `make_valid`: "
        f"**{len(ran.invalid_geometries)}**."
    )
    if ran.invalid_geometries:
        doc.table(
            ["Estado", "Clave", "Núcleo", "Motivo (GEOS)", "¿Válida después?"],
            [[g["estado"], g["clave"], g["nombre"], g["motivo_invalidez"], g["valida_despues"]] for g in ran.invalid_geometries],
        )
    doc.h3("Claves duplicadas")
    if ran.duplicate_keys:
        doc.table(
            ["Estado", "Clave", "Núcleo", "Registros", "Programas", "Traslape %", "Acción"],
            [[d["estado"], d["clave"], d["nombre"], d["registros"], d["programas"], d["traslape_pct"], d["accion"]]
             for d in ran.duplicate_keys],  # fmt: skip
        )
    else:
        doc.p("Ninguna `CLAVE` repetida.")
    doc.h3("Traslapes entre ejidos")
    slivers = ran.overlaps_summary.get("_astillas", {})
    real = {k: v for k, v in ran.overlaps_summary.items() if not k.startswith("_")}
    total_pairs = sum(v["pares"] for v in real.values())
    doc.p(
        f"Pares de ejidos distintos que se traslapan más de {slivers.get('umbral_ha', settings.overlap_min_ha):g} ha: "
        f"**{total_pairs}** ({sum(v['area_ha'] for v in real.values()):,.1f} ha). Contactos menores (astillas por "
        f"límites compartidos digitalizados distinto): {slivers.get('pares', 0)}. Como los traslapes reales son "
        "despreciables, los totales estatales se obtienen sumando ejidos sin doble conteo apreciable. "
        "Todos los pares quedan en `ran.traslapes`."
    )
    if ran.top_overlaps:
        doc.table(
            ["Clave A", "Núcleo A", "Clave B", "Núcleo B", "ha", "% de A", "% de B"],
            [[o["clave_a"], o["nombre_a"], o["clave_b"], o["nombre_b"], o["area_ha"], o["pct_a"], o["pct_b"]] for o in ran.top_overlaps],
        )
    doc.h3("Normalización de texto")
    doc.p("Se agregan `municipio_norm` y `programa_norm`; los valores originales se conservan.")
    if ran.text_normalizations:
        doc.table(
            ["Campo", "Original", "Normalizado", "Registros"],
            [[t["campo"], t["original"], t["normalizado"], t["registros"]] for t in ran.text_normalizations],
        )
    if ran_load:
        doc.h3("Carga en PostGIS")
        doc.p(
            f"`ran.nucleos_agrarios`: {ran_load['ejidos']} filas, {ran_load['invalidas']} inválidas según `ST_IsValid`. "
            f"`ran.traslapes`: {ran_load['traslapes']} filas. Ejidos que intersectan alguna ANP: {ran_load['ejidos_con_anp']}."
        )
        names = {s.code: s.name for s in settings.states}
        doc.table(
            ["Estado", "Relación con ANP", "Ejidos"],
            [[names.get(r["estado"], r["estado"]), r["relacion"], r["ejidos"]] for r in ran_load["relacion_anp"]],
        )


def add_results_sections(doc: Doc, headline: dict[str, Any], comparison: list[dict[str, Any]], settings: Settings) -> None:
    """Sección 3: respuesta a la pregunta del proyecto."""
    first, last = settings.years
    names = {s.code: s.name for s in settings.states}
    doc.h2(f"3. Resultados {first}–{last}")
    doc.p(
        f"Pérdida forestal: Hansen GFC `{settings.hansen_version}` con dosel ≥ {settings.treecover_threshold} % en 2000. "
        "Área quemada: MODIS MCD64A1. **Pérdida asociada a fuego**: pérdida dentro de área quemada del mismo año o "
        f"del anterior. Sensibilidad: área quemada ampliada {settings.fire_buffer_sens_m} m. Coincidencia espacial, no causalidad."
    )
    rows = []
    for code, h in headline.items():
        rows.append([
            names.get(code, code), h["ejidos"], h["bosque2000_ha"], h["perdida_ha"], h["perdida_fuego_ha"],
            f"{h['pct_perdida_fuego']} %", f"{h['pct_perdida_fuego_buffer']} %" if h.get("pct_perdida_fuego_buffer") is not None else "—",
            h["r_firms_anual"], h["anio_max_fuego"],
        ])  # fmt: skip
    doc.table(
        ["Estado", "Ejidos", "Bosque 2000 (ha)", "Pérdida (ha)", "Pérdida en área quemada (ha)", "% de la pérdida",
         f"% con buffer {settings.fire_buffer_sens_m} m", "r anual con focos FIRMS", "Año pico"],
        rows,
    )  # fmt: skip
    doc.h3("Ejido más afectado por estado")
    doc.table(
        ["Estado", "Ejido", "Municipio", "Pérdida en área quemada (ha)", "% de su pérdida",
         "Concentración: % en el 10 % de ejidos más afectados"],
        [[names.get(c, c), h["top_ejido"]["nombre"], h["top_ejido"]["municipio"], h["top_ejido"]["perdida_fuego_ha"],
          h["top_ejido"]["pct_perdida_fuego"], f"{h['pct_fuego_top10pct']} %"] for c, h in headline.items()],
    )  # fmt: skip
    if comparison:
        doc.h3("Dentro y fuera de ANP")
        doc.table(
            ["Estado", "Relación con ANP", "Ejidos", "Pérdida (ha)", "En área quemada (ha)", "% asociada a fuego",
             "% del bosque 2000 perdido"],
            [[names.get(r["estado"], r["estado"]), r["relacion_anp"], r["ejidos"], r["perdida_ha"], r["perdida_fuego_ha"],
              r["pct_perdida_fuego"], r["pct_perdida_bosque"]] for r in comparison],
        )  # fmt: skip
    doc.p("Gráficas: `docs/img/ranking_ejidos.png` y `docs/img/serie_anual.png`.")


# ---------------------------------------------------------------------------
# Renderizadores
# ---------------------------------------------------------------------------


def render_markdown(doc: Doc) -> str:
    def cell(v: Any) -> str:
        return _fmt(v).replace("|", "\\|").replace("\n", " ")

    lines = [f"# {doc.title}", ""]
    for block in doc.blocks:
        if block[0] == "h2":
            lines += [f"## {block[1]}", ""]
        elif block[0] == "h3":
            lines += [f"### {block[1]}", ""]
        elif block[0] == "p":
            lines += [block[1], ""]
        else:
            _, headers, rows = block
            lines.append("| " + " | ".join(headers) + " |")
            lines.append("|" + "|".join("---" for _ in headers) + "|")
            lines += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
            lines.append("")
    return "\n".join(lines)


def _inline_html(text: str) -> str:
    """Convierte el poco Markdown en línea que usamos: `código`, **negritas** y [enlaces](url)."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', out)


HTML_TEMPLATE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Reporte de calidad · ANP CONANP</title>
<style>
  :root {{ --bg:#f7f7f4; --fg:#1d2a24; --muted:#5b6b63; --line:#d9ddd6; --accent:#1f6f50; --card:#fff; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#141a17; --fg:#e4ebe6; --muted:#9db0a6; --line:#2c3832; --accent:#5cc497; --card:#1b231f; }}
  }}
  body {{ margin:0; background:var(--bg); color:var(--fg); font:15px/1.55 system-ui, -apple-system, "Segoe UI", sans-serif; }}
  main {{ max-width:1000px; margin:0 auto; padding:24px 16px 64px; }}
  h1 {{ font-size:1.6rem; margin:.2em 0 .4em; }}
  h3 {{ font-size:1rem; margin-top:1.6em; }}
  h2 {{ font-size:1.15rem; margin-top:2em; padding-bottom:.3em; border-bottom:1px solid var(--line); }}
  a {{ color:var(--accent); }}
  code {{ background:var(--card); border:1px solid var(--line); border-radius:4px; padding:0 .3em; font-size:.9em; }}
  .table {{ overflow-x:auto; }}
  table {{ border-collapse:collapse; width:100%; background:var(--card); font-size:.92rem; }}
  th, td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  th {{ color:var(--muted); font-weight:600; }}
  td.num {{ text-align:right; font-variant-numeric:tabular-nums; }}
  nav {{ font-size:.9rem; }}
</style>
</head>
<body><main>
<nav><a href="index.html">← Volver al mapa</a></nav>
{body}
</main></body>
</html>
"""


def render_html(doc: Doc) -> str:
    parts = [f"<h1>{html.escape(doc.title)}</h1>"]
    for block in doc.blocks:
        if block[0] in ("h2", "h3"):
            parts.append(f"<{block[0]}>{html.escape(block[1])}</{block[0]}>")
        elif block[0] == "p":
            parts.append(f"<p>{_inline_html(block[1])}</p>")
        else:
            _, headers, rows = block
            head = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
            body = "".join(
                "<tr>"
                + "".join(
                    f'<td class="num">{_fmt(v)}</td>' if isinstance(v, (int, float)) and not isinstance(v, bool)
                    else f"<td>{_inline_html(_fmt(v))}</td>"
                    for v in row
                )  # fmt: skip
                + "</tr>"
                for row in rows
            )
            parts.append(f'<div class="table"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>')
    return HTML_TEMPLATE.format(body="\n".join(parts))


def write_reports(
    settings: Settings,
    extract: ExtractResult,
    report: ValidationReport,
    load: dict[str, Any] | None,
    web: dict[str, Any],
    extras: dict[str, Any] | None = None,
) -> tuple[Path, Path]:
    doc = build_document(settings, extract, report, load, web)
    if extras:
        add_ran_sections(doc, extras["ran_report"], extras.get("ran_load"), settings)
        if extras.get("headline"):
            add_results_sections(doc, extras["headline"], extras.get("comparison") or [], settings)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    md_path = settings.reports_dir / "reporte_calidad.md"
    html_path = settings.web_dir / "reporte.html"
    md_path.write_text(render_markdown(doc), encoding="utf-8")
    html_path.write_text(render_html(doc), encoding="utf-8")
    log.info("Reportes: %s y %s", md_path.name, html_path.name)
    return md_path, html_path
