from alembic import context
from app.core.database import Base, get_engine
from app.models.user import RefreshSession, User  # noqa: F401

with get_engine().connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
