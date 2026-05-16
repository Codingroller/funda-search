from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str  # required — no default; startup raises ValidationError if unset
    admin_password: str = ""
    admin_username: str = "admin"
    ntfy_base_url: str = "https://ntfy.rominiek.nl"
    ntfy_token: str = ""
    db_path: str = "/data/funda.db"


settings = Settings()
