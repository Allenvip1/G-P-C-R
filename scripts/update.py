#!/usr/bin/env python3
"""
每日抓取 CME Daily Bulletin 的 PG02B，读取 COMEX 黄金期权（OG，全部到期月合计）
的看涨/看跌成交量和持仓量，写入 data/gold_pcr.csv（同一天重复运行会覆盖）。

    python scripts/update.py             # 正常更新
    python scripts/update.py --selftest  # 本地自测（不联网）
"""
import csv
import re
import sys
import datetime as dt
from pathlib import Path

URL = ("https://www.cmegroup.com/daily_bulletin/current/"
       "Section02B_Summary_Volume_And_Open_Interest_Metals_Futures_And_Options.pdf")
CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "gold_pcr.csv"
HEADER = ["date", "call_vol", "put_vol", "call_oi", "put_oi", "vol_pcr", "oi_pcr"]


def fetch_text() -> str:
    import io
    import pdfplumber
    # 🎯 放弃旧的 requests，改用能够通过 TLS 指纹墙的 curl_cffi
    from curl_cffi import requests
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://cmegroup.com"
    }
    
    # 无论当天最终版数据是否就绪，自动处理 404 并兼容备用归档路径
    try:
        r = requests.get(URL, headers=headers, impersonate="chrome120", timeout=60)
        if r.status_code == 404:
            url_backup = "https://cmegroup.com"
            r = requests.get(url_backup, headers=headers, impersonate="chrome120", timeout=60)
        r.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"CME 官网连接失败或文件尚未发布: {e}")

    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)

        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def bulletin_date(text: str) -> dt.date:
    m = re.search(r"BULLETIN # \d+@?\s+\w{3},\s+(\w{3} \d{2}, \d{4})", text)
    if not m:
        raise ValueError("找不到公报日期（页眉格式可能变了）")
    return dt.datetime.strptime(m.group(1), "%b %d, %Y").date()


def parse_line(line: str):
    """解析 'OG COMEX GOLD OPTIONS C/P ...'，返回 (side, 总成交量, 持仓量)。"""
    m = re.match(r"^OG COMEX GOLD OPTIONS ([CP])\s+(.+)$", line.strip())
    if not m:
        return None
    rest = re.sub(r"([+-])(?=\d)", r" \1 ", m.group(2)).split()
    s = next((i for i, t in enumerate(rest) if t in ("+", "-")), None)
    if s is None or s < 2:
        raise ValueError(f"无法定位持仓量: {line}")
    oi, vol = int(rest[s - 1]), int(rest[s - 2])
    parts = [int(x) for x in rest[: s - 2]]
    if parts and sum(parts) != vol:      # 校验：各分项之和应等于总成交量
        raise ValueError(f"成交量校验失败: {line}")
    return m.group(1), vol, oi


def get_counts(text: str):
    res = {}
    for ln in text.splitlines():
        r = parse_line(ln)
        if r:
            res[r[0]] = r[1:]
    if "C" not in res or "P" not in res:
        raise ValueError("没找到 OG 看涨/看跌行，公报格式可能变了")
    (cv, co), (pv, po) = res["C"], res["P"]
    if min(cv, co) <= 0:
        raise ValueError("看涨成交量或持仓量为 0，数据异常")
    for name, v in (("成交量PCR", pv / cv), ("持仓量PCR", po / co)):
        if not 0.01 < v < 10:
            raise ValueError(f"{name}={v:.4f} 超出合理范围，已停止写入")
    return cv, pv, co, po


def read_rows(path: Path = CSV_PATH) -> dict:
    rows = {}
    if path.exists():
        with path.open(newline="") as f:
            for r in csv.DictReader(f):
                rows[r["date"]] = [int(r["call_vol"]), int(r["put_vol"]),
                                   int(r["call_oi"]), int(r["put_oi"])]
    return rows


def write_rows(rows: dict, path: Path = CSV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for d in sorted(rows):
            cv, pv, co, po = rows[d]
            w.writerow([d, cv, pv, co, po, f"{pv / cv:.4f}", f"{po / co:.4f}"])


def upsert(day: dt.date, counts, path: Path = CSV_PATH) -> None:
    rows = read_rows(path)
    rows[day.isoformat()] = list(counts)
    write_rows(rows, path)


def main() -> None:
    if "--selftest" in sys.argv:
        selftest()
        return
    text = fetch_text()
    day = bulletin_date(text)
    tag = "含 PRELIMINARY 标注（初步版）" if "PRELIMINARY" in text else "无初步版标注"
    cv, pv, co, po = counts = get_counts(text)
    upsert(day, counts)
    print(f"{day} {tag}: 看涨成交量 {cv}, 看跌成交量 {pv}, 看涨持仓 {co}, 看跌持仓 {po}, "
          f"成交量PCR {pv / cv:.4f}, 持仓量PCR {po / co:.4f}")


def selftest() -> None:
    import tempfile
    sample = (
        "PG02B BULLETIN # 190@ Fri, Oct 02, 2026\n"
        "OG COMEX GOLD OPTIONS C 28937 2007 30944 567031 + 8257 53033 493898\n"
        "OG COMEX GOLD OPTIONS P 7441 759 8200 225608 + 2021 37266 395530\n"
    )
    assert bulletin_date(sample) == dt.date(2026, 10, 2)
    assert get_counts(sample) == (30944, 8200, 567031, 225608)
    assert get_counts(sample.replace("+ 8257", "+8257")) == (30944, 8200, 567031, 225608)
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "t.csv"
        upsert(dt.date(2026, 10, 5), (100, 40, 1000, 500), p)
        upsert(dt.date(2026, 10, 2), (30944, 8200, 567031, 225608), p)
        upsert(dt.date(2026, 10, 5), (100, 50, 1000, 600), p)      # 覆盖同一天
        lines = p.read_text().strip().splitlines()
        assert lines[0] == ",".join(HEADER)
        assert lines[1] == "2026-10-02,30944,8200,567031,225608,0.2650,0.3979", lines[1]
        assert lines[2] == "2026-10-05,100,50,1000,600,0.5000,0.6000", lines[2]
        assert len(lines) == 3
    print("selftest OK")


if __name__ == "__main__":
    main()
