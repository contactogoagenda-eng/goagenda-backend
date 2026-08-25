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
