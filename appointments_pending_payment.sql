-- Agenda-con-abono (version final): para servicios con requires_payment,
-- la cita se crea DE INMEDIATO al generar el link de pago, con
-- status='pending_payment' - bloqueando el cupo desde ese momento, no
-- solo cuando Wompi confirma el pago. Pasa a 'confirmed' cuando el pago
-- se aprueba, o a 'cancelled' si el link vence sin pagar (ver
-- agent/tools.py:crear_cita y services/appointment_confirmation.py).
--
-- El indice unico parcial de abajo es la defensa real contra la
-- condicion de carrera (dos clientes intentando el mismo horario casi al
-- mismo tiempo): la base de datos rechaza el segundo insert directamente
-- (la app lo atrapa - ver services/appointment_confirmation.py:
-- crear_cita_pendiente_pago - y le ofrece otro horario al cliente) en vez
-- de permitir un choque silencioso. Cubre 'confirmed' y 'pending_payment'
-- (ambos bloquean el cupo); las canceladas no cuentan, asi que un
-- horario liberado se puede reusar.
--
-- LIMITACION CONOCIDA: el indice compara el mismo scheduled_at exacto, no
-- rangos que se solapan con duraciones distintas (eso requeriria una
-- restriccion EXCLUDE con rangos y la extension btree_gist). Cubre el
-- caso real de esta app: el bot siempre ofrece horas de una lista fija
-- (generar_horas_disponibles), asi que la carrera tipica es exactamente
-- "dos clientes tomando el mismo horario mostrado", no horarios distintos
-- que se solapan por coincidencia.
--
-- Correr en el SQL Editor de Supabase del proyecto.

CREATE UNIQUE INDEX IF NOT EXISTS appointments_employee_slot_unique_idx
  ON appointments (employee_id, scheduled_at)
  WHERE status IN ('confirmed', 'pending_payment');
