"""JSON-only expression lookup: no eval, attributes, calls or executable code."""

import json
import re

EXPRESSION = re.compile(r"{{\s*([^{}]+?)\s*}}")
PATH = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_-]*(?:(?:\.[A-Za-z_][A-Za-z0-9_-]*)|(?:\[\d+\]))*$"
)


class MappingError(ValueError):
    pass


def lookup(context: dict, path: str):
    if not PATH.fullmatch(path):
        raise MappingError("Expression de mapping invalide")
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_-]*|\d+", path)
    if tokens[0] not in {"input", "trigger", "nodes"} or any(
        token.startswith("__") for token in tokens
    ):
        raise MappingError("Racine ou champ de mapping interdit")
    value = context
    for token in tokens:
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and token.isdigit() and int(token) < len(value):
            value = value[int(token)]
        else:
            raise MappingError("Le mapping référence une sortie absente")
    return value


def resolve(value, context: dict, depth: int = 0):
    if depth > 40:
        raise MappingError("Mapping trop profond")
    if isinstance(value, dict):
        return {key: resolve(item, context, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve(item, context, depth + 1) for item in value]
    if not isinstance(value, str):
        return value
    whole = EXPRESSION.fullmatch(value.strip())
    if whole:
        return lookup(context, whole.group(1).strip())

    def replace(match):
        item = lookup(context, match.group(1).strip())
        return (
            item
            if isinstance(item, str)
            else json.dumps(item, ensure_ascii=False, separators=(",", ":"))
        )

    remainder = EXPRESSION.sub("", value)
    if "{{" in remainder or "}}" in remainder:
        raise MappingError("Expression de mapping incomplète")
    return EXPRESSION.sub(replace, value)


def ensure_size(value, limit: int = 8000000) -> None:
    size = 0
    try:
        for chunk in json.JSONEncoder(allow_nan=False, ensure_ascii=False).iterencode(
            value
        ):
            size += len(chunk.encode("utf-8"))
            if size > limit:
                raise MappingError("Données du nœud trop volumineuses")
    except (TypeError, ValueError):
        raise MappingError("Données JSON invalides ou trop volumineuses") from None
