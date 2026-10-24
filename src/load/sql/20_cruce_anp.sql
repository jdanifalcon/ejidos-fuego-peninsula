-- Cruce ejidos vs. ANP federales.
-- {anp} se sustituye desde Python por el nombre calificado de la tabla de ANP.

-- 1) Intersección: superficie de cada ejido dentro de cada ANP.
--    `geography` da hectáreas sobre el elipsoide. Se ignoran contactos < 1 ha
--    (límites compartidos digitalizados con pequeñas diferencias).
DROP TABLE IF EXISTS analisis.ejido_anp;
CREATE TABLE analisis.ejido_anp AS
SELECT e.clave,
       a.id_anp,
       a.nombre                                                   AS anp_nombre,
       a.cat_manejo,
       round((ST_Area(ST_Intersection(e.geom, a.geom)::geography) / 1e4)::numeric, 2) AS area_ha
FROM ran.nucleos_agrarios e
JOIN {anp} a ON e.geom && a.geom AND ST_Intersects(e.geom, a.geom);

DELETE FROM analisis.ejido_anp WHERE area_ha < 1;

-- "dentro" con tolerancia del 99 %: ST_CoveredBy fallaría por diferencias de digitalización.
ALTER TABLE analisis.ejido_anp ADD COLUMN pct_ejido numeric, ADD COLUMN relacion text;
UPDATE analisis.ejido_anp x
SET pct_ejido = round(100 * x.area_ha / e.area_ha::numeric, 1),
    relacion  = CASE WHEN x.area_ha / e.area_ha >= 0.99 THEN 'dentro' ELSE 'intersecta' END
FROM ran.nucleos_agrarios e
WHERE e.clave = x.clave;
ALTER TABLE analisis.ejido_anp ADD PRIMARY KEY (clave, id_anp);

-- 2) ANP más cercana a cada ejido (vecino más cercano con el operador KNN <->,
--    que usa el índice GiST; la distancia final se mide en geography).
DROP TABLE IF EXISTS analisis.ejido_anp_cercana;
CREATE TABLE analisis.ejido_anp_cercana AS
SELECT e.clave,
       n.id_anp,
       n.nombre AS anp_nombre,
       round((ST_Distance(e.geom::geography, n.geom::geography) / 1000)::numeric, 2) AS distancia_km
FROM ran.nucleos_agrarios e
CROSS JOIN LATERAL (
    SELECT a.id_anp, a.nombre, a.geom
    FROM {anp} a
    ORDER BY e.geom <-> a.geom
    LIMIT 1
) n;
ALTER TABLE analisis.ejido_anp_cercana ADD PRIMARY KEY (clave);
