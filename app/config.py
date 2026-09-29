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


@lru_cache
def get_settings() -> Settings:
    return Settings()
