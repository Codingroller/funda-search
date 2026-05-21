from datetime import datetime
from sqlalchemy import Column, Float, Index, Integer, String, Boolean, DateTime, ForeignKey, Text
from app.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    ntfy_topic = Column(String, nullable=True, default="")  # orphaned — kept for existing DBs
    is_admin = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    endpoint = Column(String, nullable=False, unique=True)
    p256dh = Column(String, nullable=False)
    auth = Column(String, nullable=False)
    user_agent = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)


class InviteToken(Base):
    __tablename__ = "invite_tokens"

    token = Column(String, primary_key=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, index=True)
    used_at = Column(DateTime, nullable=True)
    used_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)


class SavedQuery(Base):
    __tablename__ = "saved_queries"

    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                     nullable=False, index=True)
    name = Column(String, nullable=False)
    params_json = Column(Text, nullable=False)
    interval_minutes = Column(Integer, default=60)
    enabled = Column(Boolean, default=True)
    last_run_at = Column(DateTime, nullable=True)
    last_run_status = Column(String, nullable=True)
    consecutive_errors = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class SeenListing(Base):
    __tablename__ = "seen_listings"

    query_id = Column(
        Integer,
        ForeignKey("saved_queries.id", ondelete="CASCADE"),
        primary_key=True,
    )
    global_id = Column(String, primary_key=True)
    first_seen_at = Column(DateTime, default=datetime.utcnow)


class RunLog(Base):
    __tablename__ = "run_logs"
    __table_args__ = (
        Index("ix_run_logs_query_started", "query_id", "started_at"),
    )

    id = Column(Integer, primary_key=True)
    query_id = Column(Integer, ForeignKey("saved_queries.id", ondelete="CASCADE"), index=True)
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String)
    result_count = Column(Integer, default=0)
    new_count = Column(Integer, default=0)
    new_listings_json = Column(Text, default="[]")
    all_listings_json = Column(Text, default="[]")
    error_message = Column(Text, nullable=True)


class CbsBuurt(Base):
    __tablename__ = "cbs_buurt"

    buurtcode = Column(String, primary_key=True)
    buurtnaam = Column(String, nullable=False)
    wijkcode = Column(String, nullable=True, index=True)
    gemeentecode = Column(String, nullable=True)
    bbox_min_lon = Column(Float, nullable=False)
    bbox_min_lat = Column(Float, nullable=False)
    bbox_max_lon = Column(Float, nullable=False)
    bbox_max_lat = Column(Float, nullable=False)
    properties_json = Column(Text, nullable=False)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class CbsWijk(Base):
    __tablename__ = "cbs_wijk"

    wijkcode = Column(String, primary_key=True)
    wijknaam = Column(String, nullable=False)
    gemeentecode = Column(String, nullable=True)
    bbox_min_lon = Column(Float, nullable=False)
    bbox_min_lat = Column(Float, nullable=False)
    bbox_max_lon = Column(Float, nullable=False)
    bbox_max_lat = Column(Float, nullable=False)
    properties_json = Column(Text, nullable=False)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class CbsGemeente(Base):
    __tablename__ = "cbs_gemeente"

    gemeentecode = Column(String, primary_key=True)
    gemeentenaam = Column(String, nullable=True)
    crime_json = Column(Text, nullable=False, default="{}")
    safety_json = Column(Text, nullable=False, default="{}")
    fetched_at = Column(DateTime, default=datetime.utcnow)


class ListingCache(Base):
    __tablename__ = "listing_cache"

    global_id = Column(String, primary_key=True)
    payload_json = Column(Text, nullable=False)
    fetched_at = Column(DateTime, default=datetime.utcnow)


class SharingConnection(Base):
    __tablename__ = "sharing_connections"
    __table_args__ = (
        Index("ix_sharing_from_to_status", "from_user_id", "to_user_id", "status"),
    )

    id           = Column(Integer, primary_key=True)
    from_user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    to_user_id   = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                          nullable=False, index=True)
    status       = Column(String, default="pending")  # pending | accepted | declined
    created_at   = Column(DateTime, default=datetime.utcnow)
    responded_at = Column(DateTime, nullable=True)


class LikedListing(Base):
    __tablename__ = "liked_listings"

    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    global_id = Column(String, primary_key=True)
    payload_json = Column(Text, default="{}")   # card data snapshot
    notes = Column(Text, default="")
    agent_contacted = Column(Boolean, default=False, nullable=False)
    viewing_date = Column(String, nullable=True)       # ISO "YYYY-MM-DD"
    walter_living_bid = Column(Integer, nullable=True) # whole euros
    bid_amount = Column(Integer, nullable=True)        # whole euros
    liked_at = Column(DateTime, default=datetime.utcnow)
    listing_status = Column(String, nullable=True)     # active | sold | under_reservation | withdrawn


class WozValue(Base):
    __tablename__ = "woz_values"

    global_id = Column(String, primary_key=True)
    postcode = Column(String, nullable=True, index=True)
    huisnummer = Column(Integer, nullable=True)
    huisnummertoevoeging = Column(String, nullable=True)
    nummeraanduiding_id = Column(String, nullable=True)
    latest_woz_eur = Column(Integer, nullable=True)
    latest_peildatum = Column(String, nullable=True)   # ISO date YYYY-MM-DD
    history_json = Column(Text, nullable=False, default="[]")
    fetched_at = Column(DateTime, default=datetime.utcnow)
    last_error = Column(String, nullable=True)         # set on failure for negative-cache


class BidEstimate(Base):
    __tablename__ = "bid_estimates"

    global_id = Column(String, primary_key=True)
    asking_price = Column(Integer, nullable=True)
    low = Column(Integer, nullable=False)
    recommended = Column(Integer, nullable=False)
    high = Column(Integer, nullable=False)
    comparables_count = Column(Integer, nullable=False)
    median_price_per_m2 = Column(Float, nullable=True)
    confidence = Column(String, default="normal")   # normal | low | unavailable
    adjustments_json = Column(Text, nullable=False, default="[]")
    computed_at = Column(DateTime, default=datetime.utcnow)
    # v2.0 columns (nullable for backward compat with old rows)
    model_version = Column(String, nullable=True)
    tier = Column(String, nullable=True)
    n_active = Column(Integer, nullable=True)
    n_sold = Column(Integer, nullable=True)
    r2 = Column(Float, nullable=True)
    residual_std = Column(Float, nullable=True)
