import argparse
import json


def main() -> int:
    parser = argparse.ArgumentParser(description="AgentProof broker-only runner scaffold")
    parser.add_argument(
        "--check", action="store_true", help="Validate package; never start a browser"
    )
    args = parser.parse_args()
    if args.check:
        print(json.dumps({"component": "runner", "execution_enabled": False}))
        return 0
    parser.error("A broker-managed sandbox is required; browser execution refused.")


if __name__ == "__main__":
    raise SystemExit(main())
