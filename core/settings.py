import os

from dotenv import load_dotenv

load_dotenv()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CHAT_MODEL_NAME = os.getenv("CHAT_MODEL_NAME") or "gpt-4.1-mini"

DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "Falta DATABASE_URL: la cadena de conexion directa a Postgres de Supabase "
        "(Project Settings > Database > Connection string, puerto 5432, no el "
        "transaction pooler de 6543). La usa el checkpointer del agente de chat."
    )

# Pool de conexiones del checkpointer (agent/graph.py). El pooler de
# Supabase en modo sesion admite solo 15 clientes EN TOTAL, compartidos
# por todos los procesos conectados a la misma base (produccion, un
# uvicorn local apuntando a la misma base, scripts...). Con el default de
# psycopg_pool (4 a 10 conexiones por proceso) dos procesos bastaban para
# agotarlo, y el chat respondia 500 ("No se pudo enviar el mensaje"). Las
# operaciones del checkpointer son cortas, asi que pocas conexiones
# alcanzan.
DB_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE") or 1)
DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE") or 4)
# Segundos que una peticion espera por una conexion libre antes de fallar
# (default de psycopg_pool: 30s, que se sumaba a la espera del cliente).
DB_POOL_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT") or 10)
