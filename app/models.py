from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey, Text
from app.db import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    password_hash = Column(String, nullable=False)
    ntfy_topic = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class SavedQuery(Base):
    __tablename__ = "saved_queries"

    id = Column(Integer, primary_key=True)
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

    id = Column(Integer, primary_key=True)
    query_id = Column(Integer, ForeignKey("saved_queries.id", ondelete="CASCADE"))
    started_at = Column(DateTime, nullable=False)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String)
    result_count = Column(Integer, default=0)
    new_count = Column(Integer, default=0)
    new_listings_json = Column(Text, default="[]")
    error_message = Column(Text, nullable=True)
