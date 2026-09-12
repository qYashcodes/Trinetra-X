from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    source = ROOT / "docs" / "demo_case.json"
    target = ROOT / "fixtures" / "tron_seed38.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(source.read_text(encoding="utf-8"))
    transfers = []
    path = data["dominant_path"]
    for index, hop in enumerate(path):
        transfers.append(
            {
                "transaction_id": hop["txids"][0],
                "block_timestamp": hop["ts_ms"],
                "from": path[index - 1]["address"] if index else "TVictimFundingFixture000000000000",
                "to": hop["address"],
                "value": str(hop["value_base"]),
                "type": "Transfer",
                "token_info": {
                    "symbol": "USDT",
                    "address": data["case"]["asset"]["contract"],
                    "decimals": data["case"]["asset"]["decimals"],
                    "name": "Tether USD",
                },
            }
        )
    target.write_text(json.dumps({"schema": "trinetra.fixture/1", "transfers": transfers}, indent=2), encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
