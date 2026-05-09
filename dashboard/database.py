"""SQLAlchemy models and database initialization for PyRunner."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship, sessionmaker

from dashboard.config import get_db_path


class Base(DeclarativeBase):
    pass


class Project(Base):
    __tablename__ = "projects"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, unique=True, nullable=False)
    repo_url = Column(Text, nullable=False)
    branch = Column(String, default="main")
    entrypoint = Column(String, default="main.py")
    type = Column(String, default="worker")  # worker | web | scheduled
    port = Column(Integer, nullable=True)
    auto_start = Column(Boolean, default=True)
    requires_display = Column(Boolean, default=False)
    schedule = Column(String, nullable=True)
    timeout_seconds = Column(Integer, default=3600)
    env_file = Column(String, default=".env")
    status = Column(String, default="registered")
    last_commit = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    git_retries = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    deploys = relationship("Deploy", back_populates="project", cascade="all, delete-orphan")
    schedules = relationship("Schedule", back_populates="project", cascade="all, delete-orphan")
    executions = relationship("Execution", back_populates="project", cascade="all, delete-orphan")


class Deploy(Base):
    __tablename__ = "deploys"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    old_commit = Column(String, nullable=True)
    new_commit = Column(String, nullable=True)
    status = Column(String, default="pending")  # success | failed | skipped
    duration_seconds = Column(Float, nullable=True)
    error_message = Column(Text, nullable=True)
    triggered_by = Column(String, default="poll")  # poll | manual | webhook
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="deploys")


class Schedule(Base):
    __tablename__ = "schedules"

    id = Column(Integer, primary_key=True, autoincrement=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    cron_expression = Column(String, nullable=False)
    entrypoint = Column(String, nullable=True)
    timeout_seconds = Column(Integer, default=3600)
    enabled = Column(Boolean, default=True)
    apscheduler_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    project = relationship("Project", back_populates="schedules")
    executions = relationship("Execution", back_populates="schedule", cascade="all, delete-orphan")


class Execution(Base):
    __tablename__ = "executions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    schedule_id = Column(Integer, ForeignKey("schedules.id"), nullable=True)
    project_id = Column(Integer, ForeignKey("projects.id"), nullable=False)
    trigger_time = Column(DateTime, nullable=True)
    start_time = Column(DateTime, nullable=True)
    end_time = Column(DateTime, nullable=True)
    duration_seconds = Column(Float, nullable=True)
    exit_code = Column(Integer, nullable=True)
    status = Column(String, default="running")  # running | success | failed | timeout | cancelled
    log_path = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    project = relationship("Project", back_populates="executions")
    schedule = relationship("Schedule", back_populates="executions")


class ActivityEvent(Base):
    __tablename__ = "activity"

    id = Column(Integer, primary_key=True, autoincrement=True)
    event_type = Column(String, nullable=False)  # deploy | execution | status_change | error
    project_name = Column(String, nullable=True)
    message = Column(Text, nullable=False)
    level = Column(String, default="info")  # info | warning | error | success
    created_at = Column(DateTime, default=datetime.utcnow)


# Engine and session factory
_engine = None
_SessionLocal = None


def get_engine():
    global _engine
    if _engine is None:
        db_path = get_db_path()
        db_path.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
        )

        @event.listens_for(_engine, "connect")
        def set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        Base.metadata.create_all(_engine)

        # Migrate: add entrypoint column to schedules if missing
        with _engine.connect() as conn:
            cols = [row[1] for row in conn.execute(text("PRAGMA table_info(schedules)"))]
            if "entrypoint" not in cols:
                conn.execute(text("ALTER TABLE schedules ADD COLUMN entrypoint VARCHAR"))
                conn.commit()

    return _engine


def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal


def get_db() -> Session:
    """Dependency for FastAPI routes."""
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database tables."""
    get_engine()
