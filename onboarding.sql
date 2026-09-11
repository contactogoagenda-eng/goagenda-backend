-- Onboarding guiado para negocios nuevos.
--
-- Se agregan con default `true`/1 primero para que los negocios YA
-- EXISTENTES queden marcados como "ya onboarded" sin tocarlos uno por uno
-- (el requerimiento explicitamente excluye migrar negocios existentes).
-- Recien despues se cambia el default de la columna a false/1, para que
-- SOLO los negocios creados de aqui en adelante entren al wizard.
--
-- Correr una sola vez en el SQL Editor de Supabase del proyecto.

ALTER TABLE businesses ADD COLUMN onboarding_completed boolean NOT NULL DEFAULT true;
ALTER TABLE businesses ALTER COLUMN onboarding_completed SET DEFAULT false;

ALTER TABLE businesses ADD COLUMN onboarding_step smallint NOT NULL DEFAULT 1;
