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

    # Competitive-bid uplift: the model fits comparable *asking* prices, but Dutch
    # homes typically sell above asking, so a winning bid sits over the fitted fair
    # value. Rather than a flat percentage, the uplift is now *market-hotness aware*
    # (see bid_comps.market_overbid): it scales with the local sell-through rate,
    # between `min` (cold market → bid at fair value) and `max` (red-hot → largest
    # premium), and falls back to `base` when the cohort is too thin to judge.
    bid_overbid_min: float = 0.0    # cold market → bid at fitted fair value
    bid_overbid_base: float = 0.03  # neutral fallback when the cohort is too thin
    bid_overbid_max: float = 0.05   # red-hot market ceiling (the old flat rate)


settings = Settings()
