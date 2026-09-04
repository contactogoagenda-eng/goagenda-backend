-- Ejecutar en Supabase: Dashboard > SQL Editor > New query > pegar y Run.
-- Registra, por negocio y numero de cliente, cuando fue el ultimo mensaje
-- que el cliente le escribio a ese negocio por WhatsApp. Es lo que usa el
-- worker de recordatorios (services/reminder_service.py) para saber si un
-- envio puede ir como texto libre (dentro de la ventana de 24h de Meta) o
-- necesita un message template aprobado (primera vez que se le escribe,
-- o ya paso mas de 24h desde su ultimo mensaje).

create table if not exists whatsapp_client_contacts (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  client_phone text not null,
  last_inbound_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (business_id, client_phone)
);

-- RLS activado sin policies: solo el backend (que usa la service role key,
-- la cual siempre se salta RLS) toca esta tabla, desde el webhook de
-- WhatsApp y el worker de recordatorios.
alter table whatsapp_client_contacts enable row level security;
