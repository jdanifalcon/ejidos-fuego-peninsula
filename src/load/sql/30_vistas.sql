-- Vistas de resultados (se recrean en cada corrida).

-- Resumen por ejido + relación con ANP + geometría: lo que consume el mapa web.
DROP VIEW IF EXISTS analisis.v_comparacion_anp;
DROP VIEW IF EXISTS analisis.v_resumen_ejido;
CREATE VIEW analisis.v_resumen_ejido AS
WITH rel AS (
    SELECT clave,
           sum(area_ha)                                          AS anp_ha,
           string_agg(anp_nombre, '; ' ORDER BY area_ha DESC)    AS anp_nombres,
           bool_or(relacion = 'dentro')                          AS dentro
    FROM analisis.ejido_anp
    GROUP BY clave
)
SELECT r.*,
       CASE WHEN rel.dentro THEN 'dentro'
            WHEN rel.anp_ha > 0 THEN 'intersecta'
            ELSE 'fuera' END                                     AS relacion_anp,
       round(100 * coalesce(rel.anp_ha, 0) / r.area_ha::numeric, 1) AS pct_en_anp,
       rel.anp_nombres,
       CASE WHEN rel.anp_ha IS NULL THEN c.distancia_km ELSE 0 END AS distancia_anp_km,
       c.anp_nombre                                              AS anp_cercana,
       e.geom
FROM analisis.resumen_ejido r
JOIN ran.nucleos_agrarios e USING (clave)
LEFT JOIN rel USING (clave)
LEFT JOIN analisis.ejido_anp_cercana c USING (clave);

-- ¿La pérdida asociada a fuego es distinta dentro y fuera de ANP?
CREATE VIEW analisis.v_comparacion_anp AS
SELECT estado,
       relacion_anp,
       count(*)                                                  AS ejidos,
       round(sum(area_ha)::numeric)                              AS area_ha,
       round(sum(perdida_ha)::numeric)                           AS perdida_ha,
       round(sum(perdida_fuego_ha)::numeric)                     AS perdida_fuego_ha,
       round((100 * sum(perdida_fuego_ha) / nullif(sum(perdida_ha), 0))::numeric, 1) AS pct_perdida_fuego,
       round((100 * sum(perdida_ha) / nullif(sum(bosque2000_ha), 0))::numeric, 2)    AS pct_perdida_bosque
FROM analisis.v_resumen_ejido
GROUP BY estado, relacion_anp
ORDER BY estado, relacion_anp;
