"""Scan repository inputs without printing any discovered secret values."""

import subprocess
from pathlib import Path

from detect_secrets.core.secrets_collection import SecretsCollection
from detect_secrets.settings import default_settings

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    names = (
        subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
            timeout=30,
        )
        .decode()
        .split("\0")
    )
    secrets = SecretsCollection()
    with default_settings():
        for name in sorted(set(names)):
            if not name or name.endswith((".lock", ".ico", ".svg")) or name == "pnpm-lock.yaml":
                continue
            source_path = ROOT / name
            if source_path.is_file():
                secrets.scan_file(str(source_path))
    findings = secrets.json()
    if findings:
        for file_name, items in findings.items():
            print(f"Potential secrets: {Path(file_name).relative_to(ROOT)} ({len(items)} findings)")
            for item in items:
                print(f"  line {item['line_number']}: {item['type']}")
        raise SystemExit(1)
    print("PASS: detect-secrets found no secrets in tracked or nonignored candidate source files")


if __name__ == "__main__":
    main()
