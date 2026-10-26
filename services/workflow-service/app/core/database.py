from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine():
    url = get_settings().sqlalchemy_url
    options = {"check_same_thread": False} if str(url).startswith("sqlite") else {}
    return create_engine(url, pool_pre_ping=True, connect_args=options)


def get_db():
    with sessionmaker(bind=get_engine(), expire_on_commit=False)() as session:
        yield session
