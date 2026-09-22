-- Alias por zona de domicilio: permite que el dueño registre variantes del
-- nombre de una zona (ej. zona "El Poblado" con alias "Poblado", "Pblado")
-- para que el matching fuzzy en agent/tools.py:_resolver_zona tenga mas
-- candidatos exactos antes de recurrir a similitud aproximada. Ver tambien
-- domicilios.sql (tabla base home_visit_zones).
--
-- Correr en el SQL Editor de Supabase del proyecto.

ALTER TABLE home_visit_zones ADD COLUMN IF NOT EXISTS aliases text[] NOT NULL DEFAULT '{}';
