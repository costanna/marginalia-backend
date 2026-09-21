import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UiLanguage(enum.StrEnum):
    CA = "ca"
    ES = "es"
    EN = "en"


class ThemePreference(enum.StrEnum):
    LIGHT = "light"
    DARK = "dark"
    SYSTEM = "system"


class TargetLevel(enum.StrEnum):
    """Level a user aims for. A1 is excluded: the app targets learners from A2 to C2."""

    A2 = "A2"
    B1 = "B1"
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"


def _text_enum[E: enum.StrEnum](enum_class: type[E], name: str) -> Enum:
    """Store enums as VARCHAR + CHECK constraint instead of native PostgreSQL enum types.

    Native enums are painful to alter in migrations (ALTER TYPE cannot run in a transaction
    block on older versions and values cannot be removed); a CHECK constraint is trivial to change.
    """
    return Enum(
        enum_class,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=16,
        values_callable=lambda cls: [member.value for member in cls],
    )


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    # Always stored lowercase (normalised at the API boundary); the unique index enforces it.
    email: Mapped[str] = mapped_column(String(320), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(80))
    ui_language: Mapped[UiLanguage] = mapped_column(
        _text_enum(UiLanguage, "ui_language"), default=UiLanguage.ES
    )
    theme_preference: Mapped[ThemePreference] = mapped_column(
        _text_enum(ThemePreference, "theme_preference"), default=ThemePreference.SYSTEM
    )
    target_level: Mapped[TargetLevel | None] = mapped_column(
        _text_enum(TargetLevel, "target_level"), default=None
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
