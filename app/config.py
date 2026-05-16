from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    secret_key: str = "change-me"
    admin_password: str = ""
    ntfy_base_url: str = "https://ntfy.romininek.nl"
    ntfy_token: str = ""
    db_path: str = "/data/funda.db"


settings = Settings()
