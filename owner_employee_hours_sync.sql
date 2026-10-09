-- Regla de negocio: el empleado principal (employees.role = 'owner') hereda
-- SIEMPRE el horario del negocio (business_hours). La disponibilidad del
-- chat y de las citas manuales se calcula con employee_hours, pero la
-- pestaña "Horario" del panel edita business_hours: antes de esta regla
-- quedaban desincronizados y el chat dejaba de ofrecer horas que el dueño
-- si habia configurado (ej. Barberia Elegant: sabado 7am en Horario, pero
-- el empleado seguia con el 9am por defecto de create_employee).
--
-- Desde el backend, routes/business_hours_routes.py ya propaga cada cambio
-- (services/db.py:sincronizar_horario_empleado_principal). Este script solo
-- alinea los datos que ya existian. Es idempotente: se puede correr mas de
-- una vez sin efecto adicional.
--
-- Correr en el SQL Editor de Supabase del proyecto.

insert into employee_hours (employee_id, day, is_open, opening_time, closing_time, lunch_start, lunch_end)
select e.id, bh.day, bh.is_open, bh.opening_time, bh.closing_time, bh.lunch_start, bh.lunch_end
from business_hours bh
join employees e on e.business_id = bh.business_id and e.role = 'owner'
on conflict (employee_id, day) do update set
  is_open = excluded.is_open,
  opening_time = excluded.opening_time,
  closing_time = excluded.closing_time,
  lunch_start = excluded.lunch_start,
  lunch_end = excluded.lunch_end;

-- Un dia sin fila en business_hours se muestra como cerrado en el panel:
-- el principal lo hereda cerrado.
insert into employee_hours (employee_id, day, is_open, opening_time, closing_time, lunch_start, lunch_end)
select e.id, d.day, false, null, null, null, null
from employees e
cross join (values ('mon'), ('tue'), ('wed'), ('thu'), ('fri'), ('sat'), ('sun')) as d(day)
where e.role = 'owner'
  and not exists (select 1 from business_hours bh where bh.business_id = e.business_id and bh.day = d.day)
  and exists (select 1 from business_hours bh where bh.business_id = e.business_id)
on conflict (employee_id, day) do update set
  is_open = false,
  opening_time = null,
  closing_time = null,
  lunch_start = null,
  lunch_end = null;
