from uuid import UUID
from typing import TYPE_CHECKING, Any, List

from sqlalchemy.dialects.postgresql import UUID as _PGUUID, BYTEA
from sqlalchemy import (
    Column,
    ForeignKey,
    and_,
    types as satypes,
    UniqueConstraint,
    func,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.schema import CheckConstraint
from sqlalchemy.orm import RelationshipProperty, foreign, relationship, remote, backref

Base = declarative_base()

# https://github.com/dropbox/sqlalchemy-stubs/issues/94
if TYPE_CHECKING:
    PGUUID = satypes.TypeEngine[UUID]
else:
    PGUUID = _PGUUID(as_uuid=True)


class User(Base):
    __tablename__ = "users"
    __tableargs__ = (CheckConstraint("username ~ '^[A-z][-A-z0-9]+$'"),)

    user_uuid = Column(PGUUID, primary_key=True)
    username = Column(
        satypes.String(length=200), nullable=False, unique=True, index=True
    )
    password_hash = Column(satypes.String, nullable=False)
    timezone = Column(satypes.String, nullable=False)
    registered = Column(satypes.DateTime(timezone=True), nullable=False)

    email_obj: "RelationshipProperty[UserEmail]" = relationship(
        "UserEmail", uselist=False, backref="user"
    )

    api_key: "RelationshipProperty[APIKey]" = relationship(
        "APIKey", uselist=False, backref="user"
    )


class UserEmail(Base):
    __tablename__ = "user_emails"

    user_uuid = Column(PGUUID, ForeignKey("users.user_uuid"), primary_key=True)
    email_address = Column(satypes.String(length=200), nullable=False, index=True)


class APIKey(Base):
    __tablename__ = "api_keys"

    user_uuid = Column(PGUUID, ForeignKey("users.user_uuid"), primary_key=True)
    api_key = Column(BYTEA(length=16), nullable=False, unique=True, index=True)


class Table(Base):
    __tablename__ = "tables"
    __tableargs__ = (CheckConstraint("table_name ~ '^[A-z][-A-z0-9]+$'"),)

    user_uuid = Column(PGUUID, ForeignKey("users.user_uuid"), primary_key=True)
    public = Column(satypes.Boolean, nullable=False)
    created = Column(
        satypes.DateTime(timezone=True), default=func.now(), nullable=False, index=True
    )
    table_name = Column(
        satypes.String(length=200), nullable=False, index=True, primary_key=True
    )
    licence_id = Column(
        satypes.SmallInteger, ForeignKey("data_licences.licence_id"), nullable=False
    )
    description = Column(
        satypes.String(length=200),
        nullable=False,
    )


class DataLicence(Base):
    __tablename__ = "data_licences"

    licence_id = Column(satypes.SmallInteger, primary_key=True, autoincrement=False)
    licence_name = Column(satypes.String)
