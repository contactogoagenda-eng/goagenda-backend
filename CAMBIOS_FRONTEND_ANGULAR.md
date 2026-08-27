# Prompt: cambios de frontend (Angular) para roles, invitaciones y empleados

Contexto: el backend de GoAgenda (FastAPI) cambió de un modelo "un usuario =
un negocio" a un modelo de roles con **super admin**, **dueños** y
**empleados**, todos registrados por un código de invitación de 6 caracteres.
Hay que actualizar la app Angular (el panel administrativo) para reflejar
esto. Todos los endpoints están agrupados por tag en `/docs` (Swagger) del
backend — usa eso como referencia de contrato exacto (request/response).

## 0. Modelo mental (léelo antes de tocar código)

- Un **super admin** no pertenece a ningún negocio: crea negocios y códigos
  de invitación de dueño, y puede bloquear negocios.
- Un **dueño** es, técnicamente, un empleado más (`role: "owner"` en
  `employees`) — es el "empleado principal" del negocio, con su propia
  agenda además de poder administrar el negocio completo.
- Un **empleado** (`role: "staff"`) tiene su propio horario, sus propios
  servicios asignados, y su propio enlace de chat.
- El registro es el mismo mecanismo para dueños y empleados: se registran
  en Supabase Auth (email/password, eso ya lo hace el Angular contra
  Supabase directamente, no contra este backend) y despues validan un
  código de 6 caracteres contra `POST /invitation-codes/redeem`.
- **`POST /businesses` (auto-registro libre) ya NO EXISTE.** Cualquier
  código Angular que lo llame hoy hay que eliminarlo o reemplazarlo.

## 1. Arranque de sesión y guards de ruta

- Justo después de que Supabase Auth confirme login (o signup), con el JWT
  ya disponible, llamar `GET /me`. Respuesta:

  ```json
  {
    "user_id": "...",
    "is_super_admin": false,
    "employments": [
      {
        "employee_id": "...",
        "business_id": "...",
        "business_name": "...",
        "business_blocked": false,
        "name": "...",
        "role": "owner",
        "active": true
      }
    ]
  }
  ```

- Crear (o actualizar si ya existe) un `SessionService` que:
  - Exponga el resultado de `/me` como estado reactivo (`Signal` o
    `BehaviorSubject`) para toda la app: `isSuperAdmin()`, `employments()`,
    `currentEmployment()` (el primero activo, hoy no hace falta selector
    multi-negocio).
  - Se llame una sola vez al iniciar sesión (o al recargar la app con un
    JWT ya guardado por el cliente de Supabase), antes de resolver
    cualquier ruta protegida.
- Añadir/actualizar un **interceptor HTTP** que agregue
  `Authorization: Bearer <jwt>` a toda request contra el backend (si no
  existe ya para el flujo actual).
- Reemplazar el `AuthGuard` actual por lógica de enrutamiento basada en la
  respuesta de `/me` (puede ser un `CanActivate`/`CanMatch` que espere a que
  `SessionService` haya cargado):
  - `is_super_admin === true` → redirigir a `/admin` (módulo super admin,
    sección 4).
  - `employments` vacío → redirigir a `/onboarding/codigo` (sección 2).
    Este es el caso de alguien recién registrado en Supabase Auth que
    todavía no validó ningún código.
  - `employments[0].role === "owner"` → panel de dueño normal (lo que ya
    existe hoy), usando `business_id` de ese employment.
  - `employments[0].role === "staff"` → módulo/vista de empleado (nueva,
    sección 6), usando `employee_id`/`business_id` de ese employment.

## 2. Ruta nueva: `/onboarding/codigo` — "Ingresa tu código de invitación"

- Un componente simple: un input de 6 caracteres (autoformatea a
  mayúsculas, valida letras+dígitos), botón "Validar".
- Llama `POST /invitation-codes/redeem` con `{ "code": "..." }`.
- Respuesta 200: `{ "business_id", "role": "admin" | "employee", "employee": {...} }`.
  - `role === "admin"`: el usuario acaba de reclamar un negocio recién
    creado por el super admin (que solo tiene `name`). Redirigir al flujo
    de "completar datos del negocio" (sección 3).
  - `role === "employee"`: redirigir directo al módulo de empleado
    (sección 6); no hay datos adicionales que completar de entrada.
  - Después de un redeem exitoso, volver a pedir `/me` (o refrescar
    `SessionService`) para que el resto de la app quede con el estado
    correcto.
- Errores a mostrar en el formulario (no como error genérico):
  - `404` "Código inválido o ya usado" → permitir reintentar.
  - `409` "Este negocio ya tiene un dueño asignado" → caso raro, alguien
    ya reclamó ese código de admin antes.

## 3. Flujo "completar datos del negocio" (solo la primera vez que un
   dueño reclama su negocio)

- El super admin solo puso `name` (y quizás `business_type`) al crear el
  negocio — falta como mínimo el WhatsApp.
- Reusa el componente de "Configuración del negocio" que ya existe, pero
  fuerza la navegación inicial de un dueño recién creado a esa pantalla
  (no al dashboard normal) hasta que complete
  `PUT /business-settings/info` con `phone_number`. No es un endpoint
  nuevo, solo un cambio de ruta inicial condicional.

## 4. Módulo nuevo: super admin (rutas bajo `/admin`, guard: `is_super_admin`)

- **Lista de negocios**: `GET /admin/businesses`. Tabla con nombre, si
  tiene dueño (`owner_id` no nulo) y estado `blocked`.
- **Crear negocio**: formulario (`name`, `business_type` opcional) →
  `POST /admin/businesses`.
- **Generar código de invitación de dueño**: botón en el detalle del
  negocio → `POST /admin/businesses/{business_id}/invitation-codes` →
  mostrar el `code` devuelto en un modal grande con botón "copiar". Se
  puede generar más de uno si el anterior se pierde (no hay límite).
- **Bloquear/desbloquear**: toggle → `PUT /admin/businesses/{business_id}/blocked`
  con `{ "blocked": true|false }`. Mostrar aviso: bloquear apaga el chat
  público **y** el panel del dueño de ese negocio (no es un bloqueo
  parcial).

## 5. Panel de dueño: sección nueva "Empleados"

- **Lista**: `GET /employees?business_id=...`. Mostrar nombre (o "Sin
  nombre" si el empleado no lo ha puesto todavía), rol (`owner`/`staff`),
  activo/inactivo. El propio dueño aparece aquí (`role: "owner"`) — no
  mostrar la opción de "eliminar" para esa fila (el backend igual la
  rechaza con 400 si se intenta).
- **Invitar empleado**: botón "+ Invitar empleado", campo opcional para
  sugerir su nombre → `POST /businesses/{business_id}/invitation-codes`
  con `{ "employee_name": "..." }` (puede omitirse) → modal mostrando el
  código generado, mismo patrón que en el módulo de super admin.
- **Eliminar (desactivar) empleado**: `DELETE /employees/{employee_id}` —
  borrado lógico; confirmar con un diálogo ("esta acción no se puede
  deshacer desde el panel").
- Por cada empleado, dos sub-vistas (accesibles también por el propio
  empleado desde su módulo, sección 6):
  - **Horario**: `GET`/`PUT /employees/{employee_id}/hours` — mismo
    componente de horario que ya existe para el negocio (mismos campos:
    `day`, `is_open`, `opening_time`, `closing_time`, `lunch_start`,
    `lunch_end`), reapuntado a este endpoint por empleado en vez del de
    negocio.
  - **Servicios**: `GET`/`PUT /employees/{employee_id}/services` —
    checklist de los servicios del negocio (`GET /services`), marcando
    cuáles ofrece ese empleado. El `PUT` manda `{ "service_ids": [...] }`
    con la lista completa (reemplaza, no es incremental). Esta edición es
    **solo del dueño**, no la exponer en el módulo de empleado.
- Por cada empleado, mostrar su enlace de chat propio
  (`https://<dominio-del-widget>/chat/{business_id}/{employee_id}`) con
  botón "copiar" y botón "generar tarjeta QR" que reusa el endpoint
  `POST /qr-card` ya existente (mismo body de siempre: `chat_link`,
  `business_name`, `whatsapp` — no cambió nada ahí, solo hay que pasarle
  el enlace de ese empleado en vez del general).

## 6. Módulo nuevo: vista de empleado (rol `staff`, o el dueño viendo su
   propio perfil de empleado)

- Editar su propio nombre: `PUT /employees/{employee_id}` con
  `{ "name": "..." }`.
- Editar su propio horario: mismo endpoint de la sección 5
  (`GET`/`PUT /employees/{employee_id}/hours` — el backend permite esto
  tanto al dueño como al propio empleado).
- Ver (solo lectura) qué servicios tiene asignados — **no** puede
  editarlos, eso es exclusivo del dueño.
- Ver/copiar su propio enlace de chat y generar su tarjeta QR, igual que
  en la sección 5.
- Nota para backlog: hoy no hay un endpoint para que el empleado vea
  "sus citas" filtradas — las citas agendadas por chat quedan con
  `employee_id`, pero no existe un `GET` dedicado para listarlas desde el
  panel. Si esta vista lo necesita, hay que pedir que se agregue al
  backend antes de construirla.

## 7. Widget de chat público (si vive en este mismo proyecto Angular)

- El chat general en `/chat/{business_id}/...` sigue funcionando igual.
- Agregar soporte para la variante `/chat/{business_id}/{employee_id}/...`
  con el MISMO contrato: `GET .../config`, `POST .../sessions`,
  `POST .../sessions/{session_id}/messages`,
  `GET .../sessions/{session_id}/messages`.
- En esta variante, `GET .../config` devuelve además `employee_id` y
  `employee_name` — úsalo para mostrar algo como "Agendando con Daniel"
  en el header del widget.
- `enabled: false` en `/config` ahora también puede significar que el
  negocio está bloqueado por el super admin (antes solo `plan === "none"`);
  el mensaje mostrado al cliente puede seguir siendo el mismo genérico de
  "agendamiento no disponible".

## Resumen de endpoints nuevos/cambiados a integrar

| Endpoint | Estado |
|---|---|
| `GET /me` | **Nuevo** — bootstrap de sesión, ver seccion 1 |
| `POST /invitation-codes/redeem` | **Nuevo** |
| `POST /businesses/{business_id}/invitation-codes` | **Nuevo** (dueño) |
| `POST /admin/businesses` | **Nuevo** (super admin) |
| `GET /admin/businesses` | **Nuevo** (super admin) |
| `PUT /admin/businesses/{business_id}/blocked` | **Nuevo** (super admin) |
| `POST /admin/businesses/{business_id}/invitation-codes` | **Nuevo** (super admin) |
| `GET /employees`, `PUT`/`DELETE /employees/{id}` | **Nuevos** |
| `GET`/`PUT /employees/{id}/hours` | **Nuevo** |
| `GET`/`PUT /employees/{id}/services` | **Nuevo** |
| `GET`/`POST /chat/{business_id}/{employee_id}/...` | **Nuevo** (chat por empleado) |
| `POST /businesses` | **ELIMINADO** — quitar toda referencia en el codigo Angular |
| `POST /appointments/manual` | **Cambiado** — ahora requiere `employee_id` en el body |
