from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VAR = (ROOT / "var").resolve()


def main() -> None:
    if VAR.parent != ROOT:
        raise SystemExit("Refusing to reset outside the repository var directory.")
    VAR.mkdir(exist_ok=True)
    for path in VAR.glob("*"):
        if path.is_file() and path.suffix in {".db", ".jsonl", ".sqlite", ".sqlite3"}:
            path.unlink()
    print(f"reset {VAR}")


if __name__ == "__main__":
    main()
