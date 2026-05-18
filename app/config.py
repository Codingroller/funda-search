from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str  # required — no default; startup raises ValidationError if unset
    admin_password: str = ""
    admin_username: str = "admin"
    db_path: str = "/data/funda.db"

    # Set to false only for local dev / testing over plain HTTP
    https_only: bool = True

    # VAPID keys for Web Push (generate with: python scripts/gen_vapid.py)
    vapid_public_key: str = ""
    vapid_private_key: str = ""
    vapid_subject: str = "mailto:r.schoonen@rominiek.nl"


settings = Settings()
