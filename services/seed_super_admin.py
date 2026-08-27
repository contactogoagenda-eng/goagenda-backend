import os

from services.db import supabase


def _buscar_usuario_por_email(email: str):
    pagina = 1
    while True:
        usuarios = supabase.auth.admin.list_users(page=pagina, per_page=200)
        if not usuarios:
            return None
        for usuario in usuarios:
            if usuario.email and usuario.email.lower() == email.lower():
                return usuario
        if len(usuarios) < 200:
            return None
        pagina += 1


def sembrar_super_admin_por_defecto():
    """
    Crea (si no existe) el super admin por defecto a partir de
    SUPER_ADMIN_EMAIL/SUPER_ADMIN_PASSWORD en el .env, y se asegura de que
    quede marcado en la tabla super_admins. Evita depender de un insert
    manual en Supabase la primera vez que se levanta el proyecto. Si las
    variables no estan configuradas, no hace nada. Es idempotente: correrlo
    de nuevo con el mismo email no crea un usuario duplicado ni le cambia
    la contraseña a uno que ya existe.
    """
    email = os.getenv("SUPER_ADMIN_EMAIL")
    password = os.getenv("SUPER_ADMIN_PASSWORD")
    if not email or not password:
        return

    try:
        usuario = _buscar_usuario_por_email(email)
        if usuario is None:
            respuesta = supabase.auth.admin.create_user(
                {"email": email, "password": password, "email_confirm": True}
            )
            usuario = respuesta.user
            print(f"Super admin creado: {email}")

        supabase.table("super_admins").upsert({"user_id": usuario.id}).execute()
        print(f"Super admin listo: {email}")
    except Exception as e:
        print(f"No se pudo sembrar el super admin por defecto ({email}): {e}")
