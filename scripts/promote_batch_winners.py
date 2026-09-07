"""Promote validated symbols from a revalidate_pool log into coin_pool.json."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

POOL_PATH = Path("coin_pool.json")
LINE_RE = re.compile(
    r"^\s+([A-Z0-9]+USDT): trades=\d+ net=[+\-]?[\d.]+ wr=[\d%]+ -> VALIDATED",
    re.MULTILINE,
)


def main() -> None:
    log_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/revalidate_v5_universe3.log")
    text = log_path.read_text(encoding="utf-8", errors="replace")
    winners = sorted({m.group(1) for m in LINE_RE.finditer(text)})
    if not winners:
        print("No validated symbols in log.", flush=True)
        return

    with POOL_PATH.open(encoding="utf-8-sig") as f:
        data = json.load(f)
    pool = [s.upper() for s in data["pool"]]
    added = [s for s in winners if s not in pool]
    if not added:
        print(f"Pool already has winners: {winners}", flush=True)
        return

    pool.extend(added)
    data["pool"] = pool
    with POOL_PATH.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    print(f"Promoted: {added} -> pool={pool}", flush=True)


if __name__ == "__main__":
    main()
