import argparse
import json
from pathlib import Path

from agentproof_api.app import create_app
from agentproof_api.settings import Settings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / "packages/contracts/openapi.json"
    schema = create_app(Settings(_env_file=None)).openapi()
    rendered = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    if args.check:
        if not output.exists() or output.read_text(encoding="utf-8") != rendered:
            raise SystemExit("OpenAPI is stale; run python scripts/manage.py contracts")
    else:
        output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
