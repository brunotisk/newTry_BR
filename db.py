import os
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()

BD = os.getenv("BD", "").strip().upper()

if BD == "HML":
    SUPABASE_URL = os.getenv("SUPABASE_URL_HOMOLOG")
    SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY_HOMOLOG")

elif BD == "PRD":
    SUPABASE_URL = os.getenv("SUPABASE_URL_PRD")
    SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_KEY_PRD")

else:
    raise RuntimeError(
        "BD inválido. Use BD=HML ou BD=PRD no arquivo .env."
    )

if not SUPABASE_URL:
    raise RuntimeError(
        f"URL do Supabase não configurada para o ambiente {BD}."
    )

if not SUPABASE_KEY:
    raise RuntimeError(
        f"Chave do Supabase não configurada para o ambiente {BD}."
    )

supabase: Client = create_client(
    SUPABASE_URL,
    SUPABASE_KEY
)