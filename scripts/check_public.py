"""Inspect tracked/staged public files without printing detected secrets."""

import os
import re
import subprocess
import sys
from pathlib import Path

files = subprocess.check_output(["git", "ls-files", "--cached", "-z"]).decode().split("\0")
blocked = []
known_secrets = [
    os.environ[k]
    for k in ("TYPESAFE_API_KEY", "GITHUB_TOKEN", "GH_TOKEN")
    if len(os.environ.get(k, "")) >= 16
]
for name in filter(None, files):
    p = Path(name)
    if not p.is_file():
        continue
    if (
        any(x in p.parts for x in ("private", "data", "artifacts", "logs", "node_modules", ".venv"))
        or p.suffix in (".db", ".sqlite3", ".pkl", ".joblib")
        or (p.name.startswith(".env") and p.name != ".env.example")
    ):
        blocked.append(name + ": private path")
        continue
    text = p.read_text(errors="replace")
    if any(secret in text for secret in known_secrets):
        blocked.append(name + ": configured credential detected")
    if re.search(
        r"(?:sk-[A-Za-z0-9_-]{24,}|gh[pousr]_[A-Za-z0-9]{30,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)",
        text,
    ):
        blocked.append(name + ": secret pattern")
print("Public boundary check:", "PASS" if not blocked else "FAIL")
for item in blocked:
    print(item)
sys.exit(bool(blocked))
