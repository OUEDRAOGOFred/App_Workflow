"""Scan tracked and new project files without printing matching secret values."""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    re.compile(rb"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    re.compile(rb"GOCSPX-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]


def main():
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    bad = []
    for raw in result.stdout.split(b"\0"):
        if not raw:
            continue
        path = ROOT / raw.decode()
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        if any(pattern.search(data) for pattern in PATTERNS):
            bad.append(str(path.relative_to(ROOT)))
    if bad:
        print("Secrets potentiels détectés dans : " + ", ".join(bad), file=sys.stderr)
        return 1
    print("Secret scan: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
