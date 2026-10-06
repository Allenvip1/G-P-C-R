#!/usr/bin/env python3
"""从 Yahoo Finance 抓 COMEX 黄金(GC=F)、白银(SI=F)期货连续合约的日线，写入 data/price_gold.csv、data/price_silver.csv。

只用标准库。任何一个品种抓取失败都只告警并保留旧文件，不会让整个更新流程失败。
"""
import csv
import datetime as dt
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
SYMBOLS = {"price_gold.csv": "GC=F", "price_silver.csv": "SI=F"}
HOSTS = ["query1.finance.yahoo.com", "query2.finance.yahoo.com"]
URL = "https://{host}/v8/finance/chart/{sym}?range=2y&interval=1d"
UA = "Mozilla/5.0"   # Yahoo 对更长的浏览器 UA 反而返回 429，简短的 UA 可用


def fetch(sym: str) -> dict:
    last_err = None
    for attempt in range(3):
        for host in HOSTS:
            url = URL.format(host=host, sym=sym)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=30) as r:
                    return json.loads(r.read().decode("utf-8"))
            except Exception as e:
                last_err = e
                try:
                    out = subprocess.run(["curl", "-s", "-m", "40", "-A", UA, url], capture_output=True, check=True).stdout
                    return json.loads(out.decode("utf-8"))
                except Exception as e2:
                    last_err = e2
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"Yahoo 请求失败：{last_err}")


def to_rows(payload: dict):
    res = payload["chart"]["result"][0]
    off = res["meta"]["gmtoffset"]
    q = res["indicators"]["quote"][0]
    rows = []
    for i, t in enumerate(res["timestamp"]):
        if q["close"][i] is None or q["open"][i] is None:
            continue
        day = dt.datetime.fromtimestamp(t + off, dt.timezone.utc).date().isoformat()
        rows.append((day, round(q["open"][i], 2), round(q["high"][i], 2), round(q["low"][i], 2), round(q["close"][i], 2)))
    rows.sort()
    return rows


def main() -> None:
    DATA.mkdir(exist_ok=True)
    for fname, sym in SYMBOLS.items():
        try:
            rows = to_rows(fetch(sym))
            if len(rows) < 100:
                raise ValueError(f"只取到 {len(rows)} 行，疑似异常")
            with (DATA / fname).open("w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["date", "open", "high", "low", "close"])
                w.writerows(rows)
            print(f"【成功】{sym} -> {fname}：{len(rows)} 行，{rows[0][0]} ~ {rows[-1][0]}")
        except Exception as e:
            print(f"【警告】{sym} 抓取失败，保留旧文件：{e}", file=sys.stderr)


if __name__ == "__main__":
    main()
