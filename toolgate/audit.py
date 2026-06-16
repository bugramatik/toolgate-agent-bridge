"""SQLAlchemy model and writer for the audit table."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

from toolgate.executor import ToolResult


class Base(DeclarativeBase):
    """Declarative base for the audit schema."""


class AuditRecord(Base):
    """One row per tool invocation, written after the call returns."""

    __tablename__ = "audit_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    call_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    tool: Mapped[str] = mapped_column(String(128), index=True)
    arguments: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list)
    ok: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    def as_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool": self.tool,
            "arguments": self.arguments,
            "scopes": self.scopes,
            "ok": self.ok,
            "status": self.status,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class AuditWriter:
    """Writes audit rows and reads the recent feed."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    @classmethod
    def from_url(cls, database_url: str) -> "AuditWriter":
        engine = create_engine(database_url, pool_pre_ping=True, future=True)
        return cls(engine)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    async def record(
        self,
        call_id: str,
        tool: str,
        arguments: dict[str, Any],
        result: ToolResult,
        scopes: set[str],
    ) -> None:
        """Write one row; a failure to write is logged by the caller, not raised."""
        row = AuditRecord(
            call_id=call_id or str(uuid.uuid4()),
            tool=tool,
            arguments=json.loads(json.dumps(arguments, default=str)),
            scopes=sorted(scopes),
            ok=result.ok,
            status=result.status,
            latency_ms=result.latency_ms,
            error=result.error,
        )
        with Session(self.engine) as session:
            session.add(row)
            session.commit()

    async def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the newest rows first, as plain dictionaries."""
        limit = max(1, min(limit, 500))
        with Session(self.engine) as session:
            rows = session.scalars(
                select(AuditRecord).order_by(AuditRecord.id.desc()).limit(limit)
            ).all()
            return [row.as_dict() for row in rows]
