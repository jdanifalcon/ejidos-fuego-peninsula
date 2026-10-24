/*
 * Visor Ejidos: pérdida forestal en área quemada por ejido (Campeche y Tabasco, fase 1).
 * Archivos estáticos generados por el pipeline (src/process/export_web.py):
 *   data/ejidos.geojson    ejidos con métricas del periodo y serie anual (propiedad "serie")
 *   data/resultados.json   cifras por estado, serie estatal y comparación con ANP
 *   data/anp.geojson       ANP federales (capa de contexto del ArcGIS FeatureServer)
 * No necesita servidor ni base de datos: funciona tal cual en GitHub Pages.
 */

// Cambia esta URL por la de tu repositorio.
const REPO_URL = "https://github.com/jdanifalcon/ejidos-fuego-peninsula";

const COLORS = { loss: "#2a78d6", fire: "#eb6834", anp: "#1b7f5a", zero: "#ecebe6", edge: "#8a8a82" };
const RAMP = ["#fbd9c6", "#f6b08c", "#ee8255", "#d95926", "#a8431b"]; // secuencial, un solo tono
const METRICS = {
  perdida_fuego_ha: (v) => fmtInt(v),
  pct_fuego_area: (v) => (v < 1 ? v.toFixed(2) : v.toFixed(1)),
};

// Mapas base: todos son servicios REST de teselas de ArcGIS, públicos y sin API key.
const ESRI = "https://server.arcgisonline.com/ArcGIS/rest/services";
const BASEMAPS = {
  gris: { name: "Gris claro", url: `${ESRI}/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}`, labels: `${ESRI}/Canvas/World_Light_Gray_Reference/MapServer/tile/{z}/{y}/{x}`, maxZoom: 16 },
  satelite: { name: "Imagen satelital", url: `${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`, labels: `${ESRI}/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}`, maxZoom: 18 },
  topo: { name: "Topográfico", url: `${ESRI}/World_Topo_Map/MapServer/tile/{z}/{y}/{x}`, labels: null, maxZoom: 18 },
};
const ATTRIBUTION = 'Mapa base: Esri, HERE, Garmin, Maxar, &copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> · Datos: UMD/Hansen, NASA, RAN, CONANP';

const state = { estado: null, metric: "perdida_fuego_ha", breaks: {} };
const fmtInt = (v) => new Intl.NumberFormat("es-MX", { maximumFractionDigits: 0 }).format(v ?? 0);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const title = (s) => String(s ?? "").toLowerCase().replace(/(^|[\s(.])\p{L}/gu, (m) => m.toUpperCase());
const fold = (s) => String(s ?? "").normalize("NFD").replace(/\p{M}/gu, "").toLowerCase();
const $ = (id) => document.getElementById(id);

// ---------- Mapa ----------
const map = L.map("map", { zoomControl: false, preferCanvas: true, minZoom: 4, worldCopyJump: true });
map.attributionControl.setPrefix('<a href="https://leafletjs.com">Leaflet</a>');
L.control.zoom({ position: "bottomright" }).addTo(map).getContainer().classList.add("zoom-sq");
map.createPane("anp").style.zIndex = 450;
map.getPane("anp").style.pointerEvents = "none";
map.createPane("labels").style.zIndex = 650;
map.getPane("labels").style.pointerEvents = "none";

let baseLayer, labelLayer;
function setBasemap(key) {
  const b = BASEMAPS[key];
  if (baseLayer) map.removeLayer(baseLayer);
  if (labelLayer) map.removeLayer(labelLayer);
  baseLayer = L.tileLayer(b.url, { maxZoom: b.maxZoom, attribution: ATTRIBUTION }).addTo(map);
  labelLayer = b.labels ? L.tileLayer(b.labels, { maxZoom: b.maxZoom, pane: "labels" }) : null;
  if (labelLayer && $("lyr-labels").checked) labelLayer.addTo(map);
}

// ---------- Escala numérica (1 : N) con barra, y norte ----------
const ScaleControl = L.Control.extend({
  options: { position: "bottomleft" },
  onAdd() {
    this._div = L.DomUtil.create("div", "scale-ctl");
    map.on("zoomend moveend resize", () => this.update());
    return this._div;
  },
  update() {
    const y = map.getSize().y / 2;
    const metersPerPx = map.distance(map.containerPointToLatLng([0, y]), map.containerPointToLatLng([100, y])) / 100;
    const denominator = Math.round((metersPerPx * 96) / 0.0254); // 96 dpi
    const target = metersPerPx * 140; // ~140 px de barra
    const pow = 10 ** Math.floor(Math.log10(target));
    const nice = [1, 2, 5, 10].map((f) => f * pow).filter((v) => v <= target).pop();
    const px = nice / metersPerPx;
    const label = (m) => (nice >= 1000 ? `${m / 1000} km` : `${m} m`);
    this._div.innerHTML = `<div class="ratio">1 : ${new Intl.NumberFormat("en-US").format(denominator)}</div>
      <div class="bar" style="width:${px}px"><i></i><i></i></div>
      <div class="ticks" style="width:${px}px"><span style="left:0">0</span><span style="left:100%">${label(nice)}</span></div>`;
  },
});

const NorthControl = L.Control.extend({
  options: { position: "bottomright" },
  onAdd() {
    const div = L.DomUtil.create("div", "north-ctl");
    div.title = "Norte";
    div.innerHTML = `<svg viewBox="0 0 40 52" width="100%" height="100%" aria-label="Norte">
      <text x="20" y="12" text-anchor="middle" font-size="13" font-weight="700" fill="#1f2933" font-family="Roboto, Segoe UI, sans-serif">N</text>
      <polygon points="20,15 30,48 20,41" fill="#1f2933"/><polygon points="20,15 10,48 20,41" fill="#fff" stroke="#1f2933" stroke-width="1.2"/></svg>`;
    return div;
  },
});

// ---------- Menú lateral y acordeones ----------
function setDrawer(open) {
  document.body.classList.toggle("drawer-open", open);
  $("menu-btn").setAttribute("aria-expanded", String(open));
  setTimeout(() => map.invalidateSize(), 260);
}
function openSection(key) {
  setDrawer(true);
  const sec = document.querySelector(`.acc[data-acc="${key}"]`);
  sec.classList.add("open");
  sec.scrollIntoView({ behavior: "smooth", block: "start" });
}

// ---------- Clases de color (cuantiles de valores > 0, comunes a todos los estados) ----------
function quantileBreaks(values, n = RAMP.length) {
  const v = values.filter((x) => x > 0).sort((a, b) => a - b);
  return v.length ? Array.from({ length: n - 1 }, (_, i) => v[Math.floor(((i + 1) / n) * (v.length - 1))]) : [];
}
function colorFor(value) {
  if (!(value > 0)) return COLORS.zero;
  const b = state.breaks[state.metric];
  let i = 0;
  while (i < b.length && value > b[i]) i++;
  return RAMP[i];
}
function renderScale() {
  const b = state.breaks[state.metric];
  const fmt = METRICS[state.metric];
  const cells = RAMP.map((c, i) => `<div><i style="background:${c}"></i>${i < b.length ? `≤ ${fmt(b[i])}` : `> ${fmt(b.at(-1))}`}</div>`);
  $("legend-scale").innerHTML = `<div><i style="background:${COLORS.zero};outline:1px solid ${COLORS.edge};outline-offset:-1px"></i>0</div>${cells.join("")}`;
}

// ---------- Popups con mini serie (Chart.js) ----------
let popupChart, stateChart;
function chartOptions(legend) {
  return {
    animation: false,
    responsive: false,
    plugins: {
      legend: { display: legend, labels: { boxWidth: 10, font: { size: 10 } } },
      tooltip: { callbacks: { label: (c) => `${c.dataset.label}: ${fmtInt(c.raw)} ha` } },
    },
    scales: {
      x: { grid: { display: false }, ticks: { font: { size: 9 }, callback: (v, i) => `’${String(RESULTS.anios[i]).slice(2)}` } },
      y: { beginAtZero: true, grid: { color: "#e4e7eb" }, ticks: { font: { size: 9 }, callback: (v) => fmtInt(v) } },
    },
  };
}

function popupHtml(p) {
  const rel = {
    dentro: `Dentro de ANP: ${esc(p.anp_nombres)}`,
    intersecta: `${p.pct_en_anp} % dentro de ANP: ${esc(p.anp_nombres)}`,
    fuera: `A ${p.distancia_anp_km} km de la ANP más cercana (${esc(p.anp_cercana)})`,
  }[p.relacion_anp];
  const rows = [
    ["Superficie del ejido", `${fmtInt(p.area_ha)} ha`],
    ["Bosque en 2000", `${fmtInt(p.bosque2000_ha)} ha`],
    ["Pérdida forestal", `${fmtInt(p.perdida_ha)} ha`],
    ["…en área quemada", `${fmtInt(p.perdida_fuego_ha)} ha${p.pct_perdida_fuego != null ? ` (${p.pct_perdida_fuego} %)` : ""}`],
    ["Área quemada (ha-año)", fmtInt(p.quemada_ha)],
    ["Focos FIRMS (px·día)", fmtInt(p.focos_firms)],
    ["Lugar en su estado", `#${p.rank_fuego_ha}`],
  ];
  return `<div class="popup">
    <h3>${esc(title(p.nombre))}</h3>
    <p class="sub">${esc(title(p.municipio_norm))}, ${esc(RESULTS.estados[p.estado])} · clave ${esc(p.clave)}</p>
    <table>${rows.map(([k, v]) => `<tr><td>${k}</td><td class="num">${v}</td></tr>`).join("")}</table>
    ${rel ? `<p class="anp">🌿 ${rel}</p>` : ""}
    <canvas width="270" height="120" aria-label="Serie anual del ejido"></canvas>
  </div>`;
}

function drawPopupChart(canvas, serie) {
  if (popupChart) popupChart.destroy();
  popupChart = new Chart(canvas, {
    type: "bar",
    data: {
      labels: RESULTS.anios,
      datasets: [
        { label: "Pérdida", data: serie.p, backgroundColor: COLORS.loss + "59", borderRadius: 3, grouped: false },
        { label: "En área quemada", data: serie.f, backgroundColor: COLORS.fire, borderRadius: 3, grouped: false },
      ],
    },
    options: chartOptions(true),
  });
}

// ---------- Resultados por estado ----------
function stateBounds(code) {
  return L.geoJSON({ type: "FeatureCollection", features: EJIDOS.features.filter((f) => f.properties.estado === code) }).getBounds();
}
function fitPadding() {
  const drawer = document.body.classList.contains("drawer-open") && window.innerWidth > 700;
  return { paddingTopLeft: [drawer ? 380 : 30, 80], paddingBottomRight: [30, 40] };
}

function renderState(code, fly = true) {
  state.estado = code;
  const c = RESULTS.cifras[code];
  document.querySelectorAll("#state-tabs button").forEach((b) => b.classList.toggle("active", b.dataset.state === code));
  $("hero-pct").textContent = `${c.pct_perdida_fuego} %`;
  $("kpi-loss").textContent = fmtInt(c.perdida_ha);
  $("kpi-fire").textContent = fmtInt(c.perdida_fuego_ha);
  $("kpi-ejidos").textContent = fmtInt(c.ejidos);
  $("sens-note").textContent =
    `Con el área quemada ampliada ${RESULTS.parametros.buffer_sensibilidad_m} m: ${c.pct_perdida_fuego_buffer} %. ` +
    (c.r_firms_anual != null ? `Correlación anual con focos FIRMS: r = ${c.r_firms_anual}.` : "");

  const s = RESULTS.serie_estatal[code];
  if (stateChart) stateChart.destroy();
  const canvas = $("state-chart");
  canvas.width = canvas.parentElement.clientWidth || 300;
  stateChart = new Chart(canvas, {
    type: "line",
    data: {
      labels: RESULTS.anios,
      datasets: [
        { label: "Pérdida forestal", data: s.perdida_ha, borderColor: COLORS.loss, backgroundColor: COLORS.loss, borderWidth: 2, pointRadius: 3 },
        { label: "En área quemada", data: s.perdida_fuego_ha, borderColor: COLORS.fire, backgroundColor: COLORS.fire + "26", borderWidth: 2, pointRadius: 3, fill: true },
      ],
    },
    options: chartOptions(false),
  });

  const top = EJIDOS.features
    .filter((f) => f.properties.estado === code)
    .sort((a, b) => b.properties.perdida_fuego_ha - a.properties.perdida_fuego_ha)
    .slice(0, 10);
  $("ranking").innerHTML = top
    .map(({ properties: p }) => `<li data-clave="${esc(p.clave)}"><span class="name">${esc(title(p.nombre))}</span>
      <span class="meta">${esc(title(p.municipio_norm))} · ${fmtInt(p.perdida_fuego_ha)} ha en área quemada (${p.pct_perdida_fuego ?? 0} % de su pérdida)</span></li>`)
    .join("");
  if (fly) map.flyToBounds(stateBounds(code), { ...fitPadding(), duration: 0.8 });
}

function zoomToEjido(clave) {
  const layer = layerByClave[clave];
  if (!layer) return;
  if (window.innerWidth <= 700) setDrawer(false);
  map.flyToBounds(layer.getBounds(), { ...fitPadding(), maxZoom: 12, duration: 0.8 });
  map.once("moveend", () => layer.openPopup());
}

// ---------- Buscador ----------
function setupSearch() {
  const input = $("search");
  const list = $("search-results");
  const index = EJIDOS.features.map(({ properties: p }) => ({
    clave: p.clave, text: fold(`${p.nombre} ${p.municipio_norm} ${p.clave}`),
    label: title(p.nombre), sub: `${title(p.municipio_norm)}, ${RESULTS.estados[p.estado]}`,
  }));
  let matches = [];
  let active = 0;
  const render = () => {
    list.hidden = !matches.length;
    list.innerHTML = matches
      .map((m, i) => `<li role="option" data-clave="${esc(m.clave)}" aria-selected="${i === active}">${esc(m.label)}<small>${esc(m.sub)}</small></li>`)
      .join("");
  };
  input.addEventListener("input", () => {
    const q = fold(input.value.trim());
    matches = q.length < 2 ? [] : index.filter((m) => m.text.includes(q)).slice(0, 8);
    active = 0;
    render();
  });
  input.addEventListener("keydown", (e) => {
    if (!matches.length) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      active = (active + (e.key === "ArrowDown" ? 1 : matches.length - 1)) % matches.length;
      render();
      e.preventDefault();
    } else if (e.key === "Enter") {
      pick(matches[active].clave);
    } else if (e.key === "Escape") {
      matches = [];
      render();
    }
  });
  list.addEventListener("click", (e) => {
    const li = e.target.closest("li");
    if (li) pick(li.dataset.clave);
  });
  function pick(clave) {
    const m = index.find((x) => x.clave === clave);
    input.value = m ? m.label : "";
    matches = [];
    render();
    zoomToEjido(clave);
  }
}

// ---------- Herramienta de medición ----------
const measure = { on: false, points: [], line: null, label: null };
function toggleMeasure(btn) {
  measure.on = !measure.on;
  btn.classList.toggle("active", measure.on);
  document.body.classList.toggle("measuring", measure.on);
  map.doubleClickZoom[measure.on ? "disable" : "enable"]();
  if (!measure.on) {
    if (measure.line) map.removeLayer(measure.line);
    if (measure.label) map.removeLayer(measure.label);
    measure.points = [];
    measure.line = measure.label = null;
  }
}
map.on("click", (e) => {
  if (!measure.on) return;
  measure.points.push(e.latlng);
  if (!measure.line) measure.line = L.polyline([], { color: "#2f3b4c", weight: 3, dashArray: "6 6", interactive: false }).addTo(map);
  measure.line.setLatLngs(measure.points);
  let total = 0;
  for (let i = 1; i < measure.points.length; i++) total += map.distance(measure.points[i - 1], measure.points[i]);
  const text = total >= 1000 ? `${(total / 1000).toFixed(2)} km` : `${Math.round(total)} m`;
  if (!measure.label) measure.label = L.tooltip({ permanent: true, direction: "right", className: "measure-label" });
  measure.label.setLatLng(e.latlng).setContent(text).addTo(map);
});

// ---------- Arranque ----------
let RESULTS, EJIDOS, ejidoLayer, anpLayer;
const layerByClave = {};

map.addControl(new NorthControl());
const scaleControl = new ScaleControl();
map.addControl(scaleControl);
setBasemap("gris");
map.fitBounds([[17.3, -94.2], [21.7, -89.0]]);
scaleControl.update();
if (window.innerWidth > 700) setDrawer(true);
// Si el contenedor cambió de tamaño al cargar (p. ej. un iframe), recalcula.
window.addEventListener("load", () => map.invalidateSize());

Promise.all(["data/resultados.json", "data/ejidos.geojson", "data/anp.geojson"].map((u) => fetch(u).then((r) => r.json())))
  .then(([results, ejidos, anp]) => {
    RESULTS = results;
    EJIDOS = ejidos;
    // El driver GeoJSON puede escribir "serie" como objeto o como texto JSON.
    for (const f of EJIDOS.features) {
      const s = f.properties.serie;
      f.properties.serie = typeof s === "string" ? JSON.parse(s || "{}") : s || {};
    }
    for (const m of Object.keys(METRICS)) state.breaks[m] = quantileBreaks(EJIDOS.features.map((f) => f.properties[m]));
    $("years").textContent = `${RESULTS.anios[0]}–${RESULTS.anios.at(-1)}`;
    $("thr").textContent = RESULTS.parametros.umbral_cobertura;
    $("repo-link").href = REPO_URL;

    anpLayer = L.geoJSON(anp, { pane: "anp", interactive: false, style: { color: COLORS.anp, weight: 1.6, dashArray: "5 4", fill: false } }).addTo(map);
    ejidoLayer = L.geoJSON(EJIDOS, {
      style: (f) => ({ color: COLORS.edge, weight: 0.5, fillColor: colorFor(f.properties[state.metric]), fillOpacity: 0.85 }),
      onEachFeature: (f, layer) => {
        layerByClave[f.properties.clave] = layer;
        layer.bindTooltip(title(f.properties.nombre), { sticky: true, direction: "top" });
        layer.bindPopup(() => popupHtml(f.properties), { maxWidth: 300, minWidth: 280 });
        layer.on("popupopen", (e) => {
          if (measure.on) return layer.closePopup(); // al medir, el clic solo agrega puntos
          drawPopupChart(e.popup.getElement().querySelector("canvas"), f.properties.serie);
        });
        layer.on("mouseover", () => layer.setStyle({ weight: 2, color: "#1f2933" }));
        layer.on("mouseout", () => ejidoLayer.resetStyle(layer));
      },
    }).addTo(map);

    // Estados
    $("state-tabs").innerHTML = Object.entries(RESULTS.estados).map(([c, n]) => `<button role="tab" data-state="${c}">${esc(n)}</button>`).join("");
    $("state-tabs").addEventListener("click", (e) => e.target.dataset.state && renderState(e.target.dataset.state));

    // Simbología
    $("metric-tabs").addEventListener("click", (e) => {
      const metric = e.target.dataset.metric;
      if (!metric) return;
      state.metric = metric;
      document.querySelectorAll("#metric-tabs button").forEach((b) => b.classList.toggle("active", b === e.target));
      ejidoLayer.setStyle((f) => ({ fillColor: colorFor(f.properties[metric]) }));
      renderScale();
    });

    // Capas
    $("lyr-ejidos").addEventListener("change", (e) => (e.target.checked ? ejidoLayer.addTo(map) : map.removeLayer(ejidoLayer)));
    $("lyr-anp").addEventListener("change", (e) => (e.target.checked ? anpLayer.addTo(map) : map.removeLayer(anpLayer)));
    $("lyr-labels").addEventListener("change", (e) => labelLayer && (e.target.checked ? labelLayer.addTo(map) : map.removeLayer(labelLayer)));
    document.querySelectorAll(".info-btn").forEach((btn) =>
      btn.addEventListener("click", () => {
        const info = $(btn.dataset.info);
        info.hidden = !info.hidden;
        btn.classList.toggle("on", !info.hidden);
      }),
    );

    // Mapas base (miniatura = una tesela del propio servicio)
    $("basemaps").innerHTML = Object.entries(BASEMAPS)
      .map(([key, b]) => `<label class="basemap"><input type="radio" name="basemap" value="${key}" ${key === "gris" ? "checked" : ""}>
        <img src="${b.url.replace("{z}/{y}/{x}", "6/28/15")}" alt="" loading="lazy"><span>${b.name}</span></label>`)
      .join("");
    $("basemaps").addEventListener("change", (e) => setBasemap(e.target.value));

    // Ranking
    $("ranking").addEventListener("click", (e) => {
      const li = e.target.closest("li");
      if (li) zoomToEjido(li.dataset.clave);
    });

    setupSearch();
    renderScale();
    renderState(Object.keys(RESULTS.estados)[0]);
  })
  .catch((err) => {
    console.error(err);
    $("hero-pct").textContent = "—";
    $("sens-note").textContent = "No se pudieron cargar los datos. Si abriste el archivo directamente, sirve la carpeta con: python -m http.server -d web 8000";
  });

// Menú, acordeones y herramientas (no dependen de los datos)
$("menu-btn").addEventListener("click", () => setDrawer(!document.body.classList.contains("drawer-open")));
$("drawer-close").addEventListener("click", () => setDrawer(false));
document.querySelectorAll(".acc-head").forEach((h) => h.addEventListener("click", () => h.parentElement.classList.toggle("open")));
document.querySelectorAll(".tool").forEach((btn) =>
  btn.addEventListener("click", () => {
    const tool = btn.dataset.tool;
    if (tool === "measure") toggleMeasure(btn);
    if (tool === "extent" && state.estado) map.flyToBounds(stateBounds(state.estado), { ...fitPadding(), duration: 0.8 });
    if (tool === "about") openSection("about");
    if (tool === "report") window.open("reporte.html", "_blank", "noopener");
  }),
);
document.addEventListener("keydown", (e) => e.key === "Escape" && measure.on && toggleMeasure(document.querySelector('[data-tool="measure"]')));
