-- Ejecutar en Supabase: Dashboard > SQL Editor > New query > pegar y Run.
--
-- Introduce el modelo de roles: super admin (crea negocios y bloquea),
-- admin de negocio / dueño (empleado principal, "role='owner'" en employees),
-- y empleados con su propia agenda, horario y servicios. El registro de
-- dueños y empleados ahora pasa por codigos de invitacion de 6 caracteres
-- en vez del auto-registro libre anterior.

-- ---------------------------------------------------------------------
-- super_admins: la sola presencia de una fila marca a ese usuario como
-- super admin. No hay endpoint de auto-alta (seria un hueco de seguridad
-- obvio); el primer super admin se inserta a mano aqui mismo, una vez
-- tengas el user_id de esa cuenta en Supabase Auth:
--
--   insert into super_admins (user_id) values ('<uuid-del-usuario>');
-- ---------------------------------------------------------------------
create table if not exists super_admins (
  user_id uuid primary key references auth.users(id) on delete cascade,
  created_at timestamptz not null default now()
);
alter table super_admins enable row level security;

-- ---------------------------------------------------------------------
-- businesses: ahora un negocio puede existir sin dueño (lo crea el super
-- admin y queda "pendiente de reclamar" hasta que alguien valide el
-- codigo de invitacion tipo 'admin'), y puede bloquearse.
-- ---------------------------------------------------------------------
alter table businesses alter column owner_id drop not null;
alter table businesses add column if not exists blocked boolean not null default false;
-- El super admin solo pone el name al crear el negocio (el dueño completa
-- el whatsapp despues, desde /business-settings), asi que phone_number
-- tampoco puede seguir siendo obligatorio.
alter table businesses alter column phone_number drop not null;

-- ---------------------------------------------------------------------
-- invitation_codes: 6 caracteres (letras mayusculas + digitos). Un
-- codigo role='admin' lo crea un super admin para ligar un negocio a su
-- futuro dueño; uno role='employee' lo crea el dueño del negocio para
-- ligar a un empleado suyo. Se consume una sola vez.
-- ---------------------------------------------------------------------
create table if not exists invitation_codes (
  id uuid primary key default gen_random_uuid(),
  code text not null unique,
  business_id uuid not null references businesses(id) on delete cascade,
  role text not null check (role in ('admin', 'employee')),
  employee_name text,
  created_by uuid not null references auth.users(id),
  used_by uuid references auth.users(id),
  used_at timestamptz,
  created_at timestamptz not null default now()
);
alter table invitation_codes enable row level security;

-- ---------------------------------------------------------------------
-- employees: cada negocio tiene uno o mas empleados. El dueño del
-- negocio es tambien un empleado (role='owner', "empleado principal"),
-- ademas de los empleados normales (role='staff').
-- ---------------------------------------------------------------------
create table if not exists employees (
  id uuid primary key default gen_random_uuid(),
  business_id uuid not null references businesses(id) on delete cascade,
  user_id uuid not null references auth.users(id),
  name text,
  role text not null default 'staff' check (role in ('owner', 'staff')),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (business_id, user_id)
);
alter table employees enable row level security;

-- ---------------------------------------------------------------------
-- employee_hours: horario de trabajo propio de cada empleado (reemplaza
-- a business_hours como fuente de verdad para la disponibilidad real de
-- citas). business_hours se deja intacta para no romper la pantalla
-- actual de horario general del negocio en la app.
-- ---------------------------------------------------------------------
create table if not exists employee_hours (
  id uuid primary key default gen_random_uuid(),
  employee_id uuid not null references employees(id) on delete cascade,
  day text not null check (day in ('mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun')),
  is_open boolean not null default true,
  opening_time time,
  closing_time time,
  lunch_start time,
  lunch_end time,
  unique (employee_id, day)
);
alter table employee_hours enable row level security;

-- ---------------------------------------------------------------------
-- employee_services: a que servicios esta ligado cada empleado (para
-- filtrar que puede ofrecer/agendar, y que se muestra en su chat propio).
-- ---------------------------------------------------------------------
create table if not exists employee_services (
  employee_id uuid not null references employees(id) on delete cascade,
  service_id uuid not null references services(id) on delete cascade,
  primary key (employee_id, service_id)
);
alter table employee_services enable row level security;

-- ---------------------------------------------------------------------
-- appointments: cada cita queda ligada al empleado que la atiende, para
-- que los choques de horario se calculen por empleado (dos empleados si
-- pueden tener una cita a la misma hora).
-- ---------------------------------------------------------------------
alter table appointments add column if not exists employee_id uuid references employees(id);

-- ---------------------------------------------------------------------
-- Backfill: los negocios que ya existian bajo el modelo viejo (un solo
-- dueño, sin empleados) deben seguir funcionando igual. Se les crea su
-- "empleado principal" con el horario y los servicios que ya tenian, y
-- sus citas viejas quedan ligadas a ese empleado.
-- ---------------------------------------------------------------------
insert into employees (business_id, user_id, name, role)
select b.id, b.owner_id, null, 'owner'
from businesses b
where b.owner_id is not null
on conflict (business_id, user_id) do nothing;

insert into employee_hours (employee_id, day, is_open, opening_time, closing_time, lunch_start, lunch_end)
select e.id, bh.day, bh.is_open, bh.opening_time, bh.closing_time, bh.lunch_start, bh.lunch_end
from business_hours bh
join employees e on e.business_id = bh.business_id and e.role = 'owner'
on conflict (employee_id, day) do nothing;

insert into employee_services (employee_id, service_id)
select e.id, s.id
from services s
join employees e on e.business_id = s.business_id and e.role = 'owner'
where s.active = true
on conflict do nothing;

update appointments a
set employee_id = e.id
from employees e
where a.employee_id is null
  and e.business_id = a.business_id
  and e.role = 'owner';

-- RLS activado sin policies en todas las tablas nuevas: solo el backend
-- (service role key, que siempre se salta RLS) las lee o escribe, igual
-- que el resto de tablas de este proyecto (ver excluded_chats.sql).
