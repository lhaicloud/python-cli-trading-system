"""Add proven losers from a revalidate_pool log to EXCLUDED_SYMBOLS in config.py."""
from __future__ import annotations

import re
import sys
from pathlib import Path

CONFIG_PATH = Path("app/config.py")
LINE_RE = re.compile(
    r"^\s+([A-Z0-9]+USDT): trades=\d+ net=(-[\d.]+).*-> not validated",
    re.MULTILINE,
)


def _add_to_excluded(symbol: str, text: str) -> tuple[str, bool]:
    if symbol in text:
        return text, False
    m = re.search(
        r'("LTCUSDT,OPUSDT,JUPUSDT,ADAUSDT(?:,AVAXUSDT)?(?:,[A-Z0-9]+)*)"',
        text,
    )
    if not m:
        m = re.search(r'("LTCUSDT,OPUSDT,JUPUSDT,ADAUSDT[^"]*)"', text)
    if not m:
        print(f"WARN: could not patch config.py for {symbol}", flush=True)
        return text, False
    old = m.group(1)
    if symbol in old:
        return text, False
    return text.replace(f'{old}"', f'{old},{symbol}"', 1), True


def main() -> None:
    log_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/revalidate_v5_universe3.log")
    text = log_path.read_text(encoding="utf-8", errors="replace")
    losers = sorted({m.group(1) for m in LINE_RE.finditer(text)})
    if not losers:
        print("No losers found in log.", flush=True)
        return

    cfg_text = CONFIG_PATH.read_text(encoding="utf-8")
    added: list[str] = []
    for sym in losers:
        cfg_text, changed = _add_to_excluded(sym, cfg_text)
        if changed:
            added.append(sym)

    if added:
        CONFIG_PATH.write_text(cfg_text, encoding="utf-8")
        print(f"Denylisted: {added}", flush=True)
    else:
        print(f"Losers already excluded: {losers}", flush=True)


if __name__ == "__main__":
    main()
