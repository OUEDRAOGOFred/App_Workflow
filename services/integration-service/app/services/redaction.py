"""Prevent connector responses from reflecting stored authentication secrets."""


def redact(value, credentials: dict):
    secrets = [
        item
        for key, item in credentials.items()
        if key not in {"email", "base_url"} and isinstance(item, str) and item
    ]

    def clean(item):
        if isinstance(item, str):
            for secret in secrets:
                if item == secret:
                    return "[REDACTED]"
                if len(secret) >= 8:
                    item = item.replace(secret, "[REDACTED]")
            return item
        if isinstance(item, list):
            return [clean(child) for child in item]
        if isinstance(item, dict):
            return {clean(key): clean(child) for key, child in item.items()}
        return item

    return clean(value)
