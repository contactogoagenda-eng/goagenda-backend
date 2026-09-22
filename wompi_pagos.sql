-- Integracion de pagos Wompi (idempotente: se puede volver a correr completo).
--
-- services.requires_payment / payment_type / payment_percentage /
--   payment_fixed_amount_cents / payment_description: configuracion de abono
--   por servicio. Si requires_payment=true, el bot genera un link de pago de
--   Wompi al agendar ese servicio (payment_type='percentage' calcula el monto
--   como % del precio del servicio; 'fixed' usa payment_fixed_amount_cents
--   directamente).
--
-- wompi_credentials: llaves de Wompi por negocio (multi-tenant), guardadas
--   SIEMPRE encriptadas (Fernet, services/wompi_encryption.py) con la llave
--   maestra WOMPI_ENCRYPTION_KEY (variable de entorno, nunca en la base de
--   datos). Una fila por negocio (business_id UNIQUE). Nunca se exponen
--   completas por la API: solo estado (is_configured, sandbox_mode, etc), ver
--   routes/wompi_settings_routes.py.
--
-- wompi_credentials_audit_log: auditoria de cada cambio sobre las
--   credenciales de un negocio (quien, cuando, que accion) - nunca guarda las
--   llaves en si, solo metadatos del cambio.
--
-- wompi_payment_requests: cada solicitud de pago enviada por el chat (un
--   link de pago de Wompi generado para una cita/servicio). Se actualiza via
--   webhook (routes/wompi_webhook_routes.py) cuando Wompi confirma el pago.
--
-- Correr en el SQL Editor de Supabase del proyecto.

ALTER TABLE services ADD COLUMN IF NOT EXISTS requires_payment boolean NOT NULL DEFAULT false;
ALTER TABLE services ADD COLUMN IF NOT EXISTS payment_type text;
ALTER TABLE services ADD COLUMN IF NOT EXISTS payment_percentage numeric;
ALTER TABLE services ADD COLUMN IF NOT EXISTS payment_fixed_amount_cents bigint;
ALTER TABLE services ADD COLUMN IF NOT EXISTS payment_description text;

ALTER TABLE services DROP CONSTRAINT IF EXISTS services_payment_type_check;
ALTER TABLE services ADD CONSTRAINT services_payment_type_check
  CHECK (payment_type IS NULL OR payment_type IN ('percentage', 'fixed'));

ALTER TABLE services DROP CONSTRAINT IF EXISTS services_payment_percentage_check;
ALTER TABLE services ADD CONSTRAINT services_payment_percentage_check
  CHECK (payment_percentage IS NULL OR (payment_percentage > 0 AND payment_percentage <= 100));


CREATE TABLE IF NOT EXISTS wompi_credentials (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL UNIQUE REFERENCES businesses(id) ON DELETE CASCADE,

  -- Encriptadas con Fernet (WOMPI_ENCRYPTION_KEY) antes de llegar aqui;
  -- nunca se guarda ni se expone texto plano.
  public_key_encrypted text NOT NULL,
  private_key_encrypted text NOT NULL,
  events_key_encrypted text NOT NULL,

  sandbox_mode boolean NOT NULL DEFAULT true,
  is_configured boolean NOT NULL DEFAULT true,

  merchant_id text,
  last_tested_at timestamptz,
  test_result text,

  created_by uuid,
  updated_by uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS wompi_credentials_business_id_idx ON wompi_credentials (business_id);


CREATE TABLE IF NOT EXISTS wompi_credentials_audit_log (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  user_id uuid,

  action text NOT NULL, -- 'CREATED' | 'UPDATED' | 'TESTED' | 'DELETED'
  sandbox_mode_before boolean,
  sandbox_mode_after boolean,
  description text,
  ip_address text,

  created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS wompi_credentials_audit_log_business_id_idx ON wompi_credentials_audit_log (business_id);
CREATE INDEX IF NOT EXISTS wompi_credentials_audit_log_created_at_idx ON wompi_credentials_audit_log (created_at DESC);


CREATE TABLE IF NOT EXISTS wompi_payment_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  business_id uuid NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
  service_id uuid REFERENCES services(id) ON DELETE SET NULL,
  appointment_id uuid REFERENCES appointments(id) ON DELETE SET NULL,
  session_id text, -- thread_id del chat (business_id:session_id) que origino la solicitud
  client_phone text,

  amount_in_cents bigint NOT NULL,
  description text,

  status text NOT NULL DEFAULT 'pending', -- 'pending' | 'paid' | 'expired' | 'cancelled'

  wompi_payment_link_id text NOT NULL,
  checkout_url text NOT NULL,
  wompi_transaction_id text,
  wompi_transaction_status text,

  expires_at timestamptz,
  paid_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS wompi_payment_requests_business_id_idx ON wompi_payment_requests (business_id);
CREATE INDEX IF NOT EXISTS wompi_payment_requests_status_idx ON wompi_payment_requests (status);
CREATE INDEX IF NOT EXISTS wompi_payment_requests_payment_link_id_idx ON wompi_payment_requests (wompi_payment_link_id);
