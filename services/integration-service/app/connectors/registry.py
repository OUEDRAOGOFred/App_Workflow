import importlib
import pkgutil

from app.connectors.base import BaseConnector

REGISTRY: dict[str, type[BaseConnector]] = {}


def register(cls: type[BaseConnector]) -> type[BaseConnector]:
    if cls.key in REGISTRY:
        raise ValueError("Duplicate connector registration")
    REGISTRY[cls.key] = cls
    return cls


def load_connectors() -> None:
    import app.connectors

    for module in pkgutil.iter_modules(app.connectors.__path__):
        if module.name not in {"base", "registry"}:
            importlib.import_module(f"app.connectors.{module.name}")
