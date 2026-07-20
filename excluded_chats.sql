-- Ejecutar en Supabase: Dashboard > SQL Editor > New query > pegar y Run.
-- Tabla de numeros excluidos del bot por negocio (familiares, proveedores,
-- u otros contactos que escriben al mismo WhatsApp del negocio y no deben
-- recibir respuestas automaticas).

create table if not exists excluded_chats (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  phone_number text not null,
  created_at timestamptz not null default now(),
  unique (business_id, phone_number)
);

-- RLS activado sin policies: solo el backend (que usa la service role key,
-- la cual siempre se salta RLS) puede leer o escribir esta tabla. La app
-- nunca la toca directo, siempre pasa por los endpoints /excluded-chats
-- del backend, que ya validan que el usuario sea dueño del negocio.
alter table excluded_chats enable row level security;
