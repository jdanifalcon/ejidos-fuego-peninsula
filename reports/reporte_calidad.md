# Reporte de calidad · Ejidos, fuego y pérdida forestal

Generado automáticamente por el pipeline el 2026-10-24 07:39 UTC.

## 1. Capa de contexto: ANP federales (ArcGIS FeatureServer)

Fuente: [Federal Protected Areas in Mexico 2026](https://services2.arcgis.com/C8EMgrsFcRFL6LrL/arcgis/rest/services/CONAMP_Federal_Protected_Areas_in_Mexico_2026/FeatureServer/1).

### Resumen

| Verificación | Resultado |  |
|---|---|---|
| Entidades esperadas / recibidas | 232 / 232 | ✅ |
| Páginas descargadas | 5 × 50 (resultOffset/resultRecordCount) | ✅ |
| CRS de entrada → salida | EPSG:4326 → EPSG:4326 | ✅ |
| Geometrías nulas o vacías | 0 | ✅ |
| Geometrías inválidas → reparadas | 6 → 6 | ✅ |
| Claves duplicadas (id_anp) | 0 | ✅ |
| Geometrías duplicadas | 0 | ✅ |
| Registros con observaciones en ESTADOS | 13 | ⚠️ |
| Superficie con diferencia > 10 % | 12 | ⚠️ |
| ANP en la Península de Yucatán | 38 | ℹ️ |
| Cargado en PostGIS | 232 filas en `conanp.anp_federales` | ✅ |

### Geometrías inválidas

Se repararon con `shapely.make_valid` (equivalente a `ST_MakeValid`). Se conservan solo las partes poligonales y todo se uniforma a MultiPolygon. Cada registro reparado queda marcado en las columnas `geom_reparada` y `motivo_invalidez`.

| id_anp | Nombre | Motivo (GEOS) | ¿Válida después? |
|---|---|---|---|
| ANP00406047 | C.A.D.N.R. 004 Don Martín | Nested shells[-101.621469948779 28.1816705013926] | Sí |
| ANP00307035 | Papigochic | Ring Self-intersection[-107.8164739668 28.0464629733876] | Sí |
| ANP00103162 | Zona marina del Archipiélago de Espíritu Santo | Nested shells[-110.40139915045 24.5989382113294] | Sí |
| ANP85101182 | Pacífico Mexicano Profundo | Nested shells[-109.2000000002 20.9999999995004] | Sí |
| ANP00407221 | Semidesierto Zacatecano | Nested shells[-101.499327570911 24.6716801978829] | Sí |
| ANP00306225 | Ríos y Montañas de la Comarca Lagunera | Nested shells[-103.801177014008 25.8086301351727] | Sí |

### Duplicados

Por clave `id_anp`: **0**. Por geometría idéntica (WKB normalizado): **0**.

### Campos vacíos

| Campo | Vacíos | % | Interpretación |
|---|---|---|---|
| pm | 92 | 40% | Fecha de publicación del Programa de Manejo. Vacío = la ANP aún no tiene programa publicado. |
| cert_sinap | 155 | 67% | Certificado de incorporación al SINAP. Vacío = no incorporada. |

### Consistencia del campo ESTADOS

El campo original se conserva. Se agrega `estados_norm` con nombres oficiales en orden alfabético (p. ej. «Yucatán y Campeche» y «Campeche y Yucatán» → `Campeche; Yucatán`). Los textos libres (zonas marinas) quedan con `estados_norm` vacío y `estados_texto_libre = true` para revisión manual.

| id_anp | Nombre | ESTADOS original | Normalizado | Observación |
|---|---|---|---|---|
| ANP09703231 | Arrecifes del Golfo de México-Sur | Golfo de México | — | Texto libre, sin entidad federativa reconocible |
| ANP00103157 | Zona marina del Archipiélago de San Lorenzo | Se ubica en el Golfo de California | — | Texto libre, sin entidad federativa reconocible |
| ANP00101163 | Zona marina Bahía de los Ángeles, canales de Ballenas y de Salsipuedes | Se ubica en el Golfo de California | — | Texto libre, sin entidad federativa reconocible |
| ANP00108171 | Ventilas Hidrotermales de la Cuenca de Guaymas y de la Dorsal del Pacífico Oriental | Localizadas en el Golfo de California y en el Pacífico Norte | — | Texto libre, sin entidad federativa reconocible |
| ANP00603041 | Bosencheve | Estado de México y Michoacán | México; Michoacán | Escrito distinto a la forma más común: «Michoacán y Estado de México» |
| ANP00103162 | Zona marina del Archipiélago de Espíritu Santo | Se ubica en el Golfo de California | — | Texto libre, sin entidad federativa reconocible |
| ANP85101182 | Pacífico Mexicano Profundo | Se ubica en el Océano Pacífico | — | Texto libre, sin entidad federativa reconocible |
| ANP00103183 | Revillagigedo | Se ubica en el Océano Pacífico | — | Texto libre, sin entidad federativa reconocible |
| ANP02107057 | Islas del Golfo de California | B.C., B.C.S., Sonora y Sinaloa | Baja California; Baja California Sur; Sinaloa; Sonora | Usa abreviaturas |
| ANP00606187 | Peña Colorada | Queretaro | Querétaro | Variante ortográfica (acentos/mayúsculas); Escrito distinto a la forma más común: «Querétaro» |
| ANP00903207 | Nuevo Uxmal | Campeche y Yucatán | Campeche; Yucatán | Escrito distinto a la forma más común: «Yucatán y Campeche» |
| ANP00903212 | Bajos del Norte | Se ubica en el área conocida como Banco de Campeche en el Golfo de México | — | Texto libre, sin entidad federativa reconocible |
| ANP00503214 | Zona Marina de la Isla Isabel | Se ubica en el Golfo de California | — | Texto libre, sin entidad federativa reconocible |

### Superficie decretada vs. calculada

La referencia es el área **geodésica** (sobre el elipsoide WGS84). También se calcula en EPSG:6372 (México LCC): por ser una proyección conforme, su área difiere de la geodésica entre -1.087 % y 1.173 % (mediana -0.557 %). Para áreas oficiales conviene la geodésica o una proyección de áreas iguales.

ANP cuya geometría difiere más de 10 % de la superficie decretada (posibles cambios de poligonal, decretos con superficie redondeada o geometrías incompletas):

| id_anp | Nombre | Decretada (ha) | Calculada (ha) | Diferencia % |
|---|---|---|---|---|
| ANP00603016 | El Tepeyac | 1,500.00 | 249.65 | -83.36 |
| ANP00603012 | Fuentes Brotantes de Tlalpan | 129.00 | 22.04 | -82.91 |
| ANP00503065 | Isla Isabel | 194.17 | 79.81 | -58.90 |
| ANP00503005 | Cerro de Garnica | 1,936.00 | 978.24 | -49.47 |
| ANP00603011 | Cumbres del Ajusco | 920.00 | 501.78 | -45.46 |
| ANP00603053 | General Juan Álvarez | 528.00 | 346.12 | -34.45 |
| ANP00603022 | Xicoténcatl | 851.30 | 654.03 | -23.17 |
| ANP00407068 | Sierra de Álvarez | 16,900.00 | 14,100.26 | -16.57 |
| ANP00903128 | Arrecifes de Cozumel | 11,987.88 | 13,852.76 | 15.56 |
| ANP00403029 | El Sabinal | 8.00 | 9.24 | 15.50 |
| ANP00403039 | Los Novillos | 38.21 | 42.56 | 11.37 |
| ANP00603044 | Desierto del Carmen o de Nixcongo | 529.00 | 475.24 | -10.16 |

### ANP por categoría de manejo

| Clave | Categoría | ANP |
|---|---|---|
| PN | Parque Nacional | 79 |
| APFF | Área de Protección de Flora y Fauna | 57 |
| RB | Reserva de la Biosfera | 48 |
| SANT | Santuario | 28 |
| APRN | Área de Protección de Recursos Naturales | 15 |
| MN | Monumento Natural | 5 |

### Carga en PostGIS

| Comprobación | Resultado |
|---|---|
| Tabla | `conanp.anp_federales` |
| Filas | 232 |
| Geometrías inválidas en la base (ST_IsValid) | 0 |
| Tipos de geometría distintos | 1 |
| SRID | 4,326 |
| Superficie total (ha, geography) | 98,115,068.00 |
| Versión de PostGIS | 3.5.3 |

Índices: `anp_federales_pkey`, `idx_anp_federales_geom`

Consulta de ejemplo: ANP a menos de 50 km de Xpujil, Calakmul (`ST_DWithin` sobre `geography`).

| Nombre | Categoría | Distancia (km) |
|---|---|---|
| Calakmul | RB | 10.50 |
| Balam Kú | RB | 24.30 |
| Balam Kin | APFF | 47.50 |

### Exportación para el mapa web

`web/data/anp.geojson`: 232 ANP, simplificadas con tolerancia de 150 m en EPSG:6372 y coordenadas a 5 decimales → 0.8 MB.

## 2. Ejidos: núcleos agrarios del RAN

Fuente: KML de datos abiertos del Registro Agrario Nacional. Los atributos vienen en `ExtendedData/SchemaData`, que el driver KML de GDAL no lee; se usa un lector XML propio.

### Resumen por estado

| Estado | Registros leídos | Núcleos tras validar | Ejidos | Comunidades | Superficie (ha) |
|---|---|---|---|---|---|
| Campeche | 374 | 374 | 374 | 0 | 3,021,574.30 |
| Tabasco | 797 | 796 | 795 | 1 | 1,094,494.30 |

### Geometrías

CRS de entrada: `EPSG:4326` (KML es siempre WGS84). Coordenadas con Z (siempre 0) pasadas a 2D. Geometrías nulas: **0**. Inválidas reparadas con `make_valid`: **5**.

| Estado | Clave | Núcleo | Motivo (GEOS) | ¿Válida después? |
|---|---|---|---|---|
| camp | 04003105 | MAMANTEL (FCO.VILLA) | Ring Self-intersection[-91.0763437970855 18.4705342316711] | Sí |
| camp | 04009015 | HARO | Ring Self-intersection[-90.7899564278343 18.4382749609898] | Sí |
| camp | 04006035 | HOPELCHEN | Ring Self-intersection[-89.8292947918656 19.7758155192077] | Sí |
| camp | 04002001 | LERMA | Ring Self-intersection[-90.6061852778792 19.7964957046376] | Sí |
| tab | 27008064 | LA ESPERANZA | Ring Self-intersection[-93.6456132622154 17.9293959840095] | Sí |

### Claves duplicadas

| Estado | Clave | Núcleo | Registros | Programas | Traslape % | Acción |
|---|---|---|---|---|---|---|
| tab | 27008021 | CHICOACAN | 2 | FANAR, PROCEDE | 0.00 | Partes del mismo ejido: se unen 2 polígonos |

### Traslapes entre ejidos

Pares de ejidos distintos que se traslapan más de 1 ha: **1** (1.0 ha). Contactos menores (astillas por límites compartidos digitalizados distinto): 65. Como los traslapes reales son despreciables, los totales estatales se obtienen sumando ejidos sin doble conteo apreciable. Todos los pares quedan en `ran.traslapes`.

| Clave A | Núcleo A | Clave B | Núcleo B | ha | % de A | % de B |
|---|---|---|---|---|---|---|
| 04010008 | XBONIL | 04009053 | EL CENTENARIO | 1.03 | 0.00 | 0.00 |

### Normalización de texto

Se agregan `municipio_norm` y `programa_norm`; los valores originales se conservan.

| Campo | Original | Normalizado | Registros |
|---|---|---|---|
| municipio | BALANCAN | BALANCÁN | 55 |
| municipio | CARDENAS | CÁRDENAS | 60 |
| municipio | CUNDUACAN | CUNDUACÁN | 40 |
| municipio | HECELCHAKAN | HECELCHAKÁN | 13 |
| municipio | JALPA DE MENDEZ | JALPA DE MÉNDEZ | 20 |
| municipio | PARAISO | PARAÍSO | 14 |
| programa | Fanar | FANAR | 2 |
| programa | Procede | PROCEDE | 1,115 |

### Carga en PostGIS

`ran.nucleos_agrarios`: 1170 filas, 0 inválidas según `ST_IsValid`. `ran.traslapes`: 1 filas. Ejidos que intersectan alguna ANP: 188.

| Estado | Relación con ANP | Ejidos |
|---|---|---|
| Campeche | dentro | 11 |
| Campeche | fuera | 296 |
| Campeche | intersecta | 67 |
| Tabasco | dentro | 65 |
| Tabasco | fuera | 686 |
| Tabasco | intersecta | 45 |

## 3. Resultados 2015–2024

Pérdida forestal: Hansen GFC `v1_13` con dosel ≥ 30 % en 2000. Área quemada: MODIS MCD64A1. **Pérdida asociada a fuego**: pérdida dentro de área quemada del mismo año o del anterior. Sensibilidad: área quemada ampliada 500 m. Coincidencia espacial, no causalidad.

| Estado | Ejidos | Bosque 2000 (ha) | Pérdida (ha) | Pérdida en área quemada (ha) | % de la pérdida | % con buffer 500 m | r anual con focos FIRMS | Año pico |
|---|---|---|---|---|---|---|---|---|
| Campeche | 374 | 2,559,434.00 | 340,660.00 | 47,208.00 | 13.9 % | 31.4 % | 0.72 | 2,023 |
| Tabasco | 796 | 422,479.00 | 49,352.00 | 3,299.00 | 6.7 % | 15.8 % | 0.54 | 2,024 |

### Ejido más afectado por estado

| Estado | Ejido | Municipio | Pérdida en área quemada (ha) | % de su pérdida | Concentración: % en el 10 % de ejidos más afectados |
|---|---|---|---|---|---|
| Campeche | ITURBIDE | HOPELCHEN | 5,690.00 | 22.90 | 76.3 % |
| Tabasco | APATZINGAN | BALANCÁN | 218.00 | 22.50 | 95.3 % |

### Dentro y fuera de ANP

| Estado | Relación con ANP | Ejidos | Pérdida (ha) | En área quemada (ha) | % asociada a fuego | % del bosque 2000 perdido |
|---|---|---|---|---|---|---|
| Campeche | dentro | 11 | 2034 | 5 | 0.2 | 5.32 |
| Campeche | fuera | 296 | 198937 | 25366 | 12.8 | 16.39 |
| Campeche | intersecta | 67 | 139689 | 21837 | 15.6 | 10.69 |
| Tabasco | dentro | 65 | 5260 | 590 | 11.2 | 9.04 |
| Tabasco | fuera | 686 | 37238 | 2140 | 5.7 | 11.64 |
| Tabasco | intersecta | 45 | 6854 | 569 | 8.3 | 15.41 |

Gráficas: `docs/img/ranking_ejidos.png` y `docs/img/serie_anual.png`.
