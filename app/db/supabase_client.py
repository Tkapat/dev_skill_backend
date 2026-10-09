from supabase import create_client

from app.core.config import settings

sb = create_client(settings.supabase_url, settings.supabase_service_role_key)
