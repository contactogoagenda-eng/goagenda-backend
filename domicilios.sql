-- Servicios a domicilio (idempotente: se puede volver a correr completo).
--
-- services.offers_home_visit: el dueño marca (con un switch en el panel) que
--   servicios se pueden prestar a domicilio.
-- appointments.is_home_visit / address: la cita se pidio a domicilio y la
--   direccion donde debe ir el negocio.
-- appointments.home_visit_zone / home_visit_fee: municipio/zona atendida y el
--   recargo de domicilio cobrado (copiado de la zona al momento de agendar).
-- home_visit_zones: politicas de domicilio por negocio - donde SI se hacen
--   domicilios y cuanto cuesta el recargo en cada zona (se agregan a mano).
--
-- businesses.home_visits_enabled: flag general - el dueño activa/desactiva los
--   domicilios desde Ajustes. Desactivado (default) todo se comporta como antes.
--
-- Correr en el SQL Editor de Supabase del proyecto.

ALTER TABLE businesses ADD COLUMN IF NOT EXISTS home_visits_enabled boolean NOT NULL DEFAULT false;
ALTER TABLE services ADD COLUMN IF NOT EXISTS offers_home_visit boolean NOT NULL DEFAULT false;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS is_home_visit boolean NOT NULL DEFAULT false;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS address text;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS home_visit_zone text;
ALTER TABLE appointments ADD COLUMN IF NOT EXISTS home_visit_fee numeric NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS home_visit_zones (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  name text NOT NULL,
  fee numeric NOT NULL DEFAULT 0,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS home_visit_zones_business_id_idx ON home_visit_zones (business_id);
