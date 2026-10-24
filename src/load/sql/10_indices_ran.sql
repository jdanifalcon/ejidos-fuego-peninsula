-- Claves, índices y comentarios de las tablas cargadas desde Python.
ALTER TABLE ran.nucleos_agrarios ADD PRIMARY KEY (clave);
CREATE INDEX IF NOT EXISTS idx_nucleos_agrarios_geom ON ran.nucleos_agrarios USING GIST (geom);
CREATE INDEX IF NOT EXISTS idx_nucleos_agrarios_estado ON ran.nucleos_agrarios (estado);
COMMENT ON TABLE ran.nucleos_agrarios IS 'Núcleos agrarios (ejidos y comunidades) del RAN, validados: make_valid, claves duplicadas resueltas, texto normalizado.';

CREATE INDEX IF NOT EXISTS idx_traslapes_geom ON ran.traslapes USING GIST (geom);
COMMENT ON TABLE ran.traslapes IS 'Pares de ejidos que se traslapan más que OVERLAP_MIN_HA (control de calidad).';

ALTER TABLE analisis.metricas_ejido_anual ADD PRIMARY KEY (clave, anio);
ALTER TABLE analisis.metricas_ejido_anual
    ADD CONSTRAINT fk_metricas_ejido FOREIGN KEY (clave) REFERENCES ran.nucleos_agrarios (clave);
COMMENT ON TABLE analisis.metricas_ejido_anual IS
    'Hectáreas por ejido y año: Hansen GFC (pérdida), MODIS MCD64A1 (quemada), pérdida en área quemada del año o el anterior, focos FIRMS.';
COMMENT ON COLUMN analisis.metricas_ejido_anual.perdida_fuego_ha IS 'Pérdida forestal dentro de área quemada MCD64A1 del mismo año o del anterior.';
COMMENT ON COLUMN analisis.metricas_ejido_anual.perdida_fuego_buffer_ha IS 'Igual, con el área quemada dilatada FIRE_BUFFER_SENS_M metros (sensibilidad).';
COMMENT ON COLUMN analisis.metricas_ejido_anual.focos_firms IS 'Pixeles de 1 km × días con foco de calor FIRMS (MODIS) con confianza mínima.';

ALTER TABLE analisis.resumen_ejido ADD PRIMARY KEY (clave);
ALTER TABLE analisis.metricas_estado_anual ADD PRIMARY KEY (estado, anio);
