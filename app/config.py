"""Variables de entorno del bot.

Todo lo que Alek puede ajustar desde EasyPanel sin tocar código vive aquí
(horarios de entrevista, tiempos del recordatorio y del seguimiento, etc.).
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Chatwoot ---------------------------------------------------------
    chatwoot_base_url: str = "https://crm.adlek.com.mx"
    chatwoot_account_id: int = 1
    # Solo se procesan mensajes de esta bandeja (WhatsApp Demos).
    chatwoot_inbox_id: int = 3
    # Token de acceso del Agent Bot. Vacío = modo de prueba: las acciones se
    # imprimen en el log en lugar de mandarse a Chatwoot.
    chatwoot_bot_token: str = ""
    # "Webhook Secret" del Agent Bot (Ajustes → Bots en Chatwoot). Si está
    # definido, cada evento debe traer una firma válida (X-Chatwoot-Signature).
    chatwoot_webhook_secret: str = ""
    # Alternativa/extra: llave en la URL (?key=...). Vacío = no se exige.
    webhook_key: str = ""
    # Tolerancia de reloj para la firma (segundos).
    signature_max_age_seconds: int = 300

    # --- Demo -------------------------------------------------------------
    # Tres horarios de entrevista separados por "|". Máximo 20 caracteres
    # cada uno (límite de los botones de WhatsApp).
    interview_slots: str = "Mié 30 sep 9:00|Mié 30 sep 11:30|Jue 1 oct 9:00"
    # Recordatorio simulado ("un día antes") y seguimiento por abandono.
    reminder_delay_seconds: int = 120
    followup_delay_seconds: int = 180
    # Muestra las líneas "(en esta demo...)".
    demo_mode: bool = True

    # --- IA (v2) ----------------------------------------------------------
    # Sin OPENAI_API_KEY el bot funciona igual que la v1 (textos fijos).
    openai_api_key: str = ""
    openai_model: str = "gpt-5.4-nano"
    openai_embedding_model: str = "text-embedding-3-small"
    openai_reasoning_effort: str = "low"
    openai_reasoning_max_tokens_floor: int = 2000
    ai_enabled: bool = True
    # Si la IA tarda más que esto, se usa el texto fijo.
    llm_timeout_seconds: float = 12.0
    memory_turns: int = 12
    chat_token_budget_per_day: int = 60000
    global_token_budget_per_day: int = 2000000

    # --- Supabase (proyecto "demos") --------------------------------------
    supabase_url: str = ""
    supabase_service_key: str = ""
    # Etiqueta que separa los documentos del RAG y los contactos de esta demo.
    demo_key: str = "reclutamiento"

    # --- Cal.com ----------------------------------------------------------
    cal_api_key: str = ""
    cal_event_type_id: int = 0
    cal_days_ahead: int = 7
    cal_max_slots: int = 9
    cal_slots_per_day: int = 3
    cal_attendee_email_domain: str = "candidatos.adlek.com.mx"
    timezone: str = "America/Mexico_City"

    # --- Buffer de entrada ------------------------------------------------
    # Junta los mensajes que el candidato manda seguidos y contesta todo junto.
    buffer_window_seconds: float = 6.0
    # Si tocó un botón, casi no se espera.
    buffer_button_seconds: float = 1.0

    # --- Operación --------------------------------------------------------
    db_path: str = "data/reclutamiento.db"
    # Pausa entre burbujas para que lleguen en orden a WhatsApp.
    send_delay_seconds: float = 1.0
    scheduler_tick_seconds: float = 5.0
    # Límite anti-spam / anti-bucle por conversación.
    chat_rate_limit_per_min: int = 30
    max_request_body_bytes: int = 1024 * 1024
    log_level: str = "INFO"

    @property
    def slots(self) -> list[str]:
        return [s.strip() for s in self.interview_slots.split("|") if s.strip()]

    @property
    def dry_run(self) -> bool:
        return not self.chatwoot_bot_token

    @property
    def ai_ready(self) -> bool:
        return self.ai_enabled and bool(self.openai_api_key)

    @property
    def supabase_ready(self) -> bool:
        return bool(self.supabase_url and self.supabase_service_key)

    @property
    def cal_ready(self) -> bool:
        return bool(self.cal_api_key and self.cal_event_type_id)


@lru_cache
def get_settings() -> Settings:
    return Settings()
