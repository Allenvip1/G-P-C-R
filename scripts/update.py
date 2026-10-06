#!/usr/bin/env python3
"""
每日抓取 CME Daily Bulletin 的 PG02B（金属期货与期权汇总），读取 COMEX 黄金期权（OG）
和白银期权（SO）的看涨/看跌成交量和持仓量，写入 data/gold_pcr.csv。
若当天 PDF 尚未发布或返回的不是正规 PDF，自动安全退出，保留历史数据。
"""
import csv
import io
import re
import sys
import datetime as dt
from pathlib import Path

URL = (
    "https://www.cmegroup.com/daily_bulletin/current/"
    "Section02B_Summary_Volume_And_Open_Interest_Metals_Futures_And_Options.pdf"
)
URL_PG64 = (
    "https://www.cmegroup.com/daily_bulletin/current/"
    "Section64_Metals_Option_Products.pdf"
)
REFERER = "https://www.cmegroup.com/"
CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "gold_pcr.csv"
EXPIRY_CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "by_expiry.csv"

HEADER = [
    "date",
    "call_vol", "put_vol", "call_oi", "put_oi", "vol_pcr", "oi_pcr",
    "silver_call_vol", "silver_put_vol", "silver_call_oi", "silver_put_oi",
    "silver_vol_pcr", "silver_oi_pcr",
]


def fetch_text() -> str:
    import pdfplumber
    from curl_cffi import requests

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": REFERER,
    }

    try:
        r = requests.get(URL, headers=headers, impersonate="chrome120", timeout=60)

        if r.status_code == 404:
            print("【提示】CME 当天的 PDF 尚未发布（404）。安全退出，保留历史数据。")
            sys.exit(0)

        r.raise_for_status()

        if not r.content.startswith(b"%PDF"):
            print("【提示】CME 返回的不是 PDF（可能是占位网页或拦截页）。安全退出，保留历史数据。")
            sys.exit(0)

    except SystemExit:
        raise
    except Exception as e:
        raise RuntimeError(f"CME 官网连接或文件校验异常: {e}")

    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def bulletin_date(text: str) -> dt.date:
    m = re.search(r"BULLETIN # \d+@?\s+\w{3},\s+(\w{3} \d{2}, \d{4})", text)
    if not m:
        raise ValueError("找不到公报日期（页眉格式可能变了）")
    return dt.datetime.strptime(m.group(1), "%b %d, %Y").date()


def parse_line(line: str):
    """同时匹配黄金(OG)和白银(SO)期权的看涨/看跌汇总行。"""
    m = re.match(r"^(OG|SO) COMEX (GOLD|SILVER) OPTIONS ([CP])\s+(.+)$", line.strip())
    if not m:
        return None

    product_code = m.group(1)  # OG 或 SO
    side = m.group(3)          # C 或 P

    rest = re.sub(r"([+-])(?=\d)", r" \1 ", m.group(4)).split()
    s = next((i for i, t in enumerate(rest) if t in ("+", "-")), None)
    if s is None or s < 2:
        return None

    try:
        oi, vol = int(rest[s - 1]), int(rest[s - 2])
        parts = [int(x) for x in rest[: s - 2]]
    except ValueError:
        return None
    if parts and sum(parts) != vol:
        return None

    return product_code, side, vol, oi


def get_counts(text: str):
    gold_res, silver_res = {}, {}

    for ln in text.splitlines():
        r = parse_line(ln)
        if r:
            prod, side, vol, oi = r
            if prod == "OG":
                gold_res[side] = [vol, oi]
            elif prod == "SO":
                silver_res[side] = [vol, oi]

    if "C" not in gold_res or "P" not in gold_res:
        raise ValueError("没找到 OG 黄金看涨/看跌行")

    cv, co = gold_res["C"]
    pv, po = gold_res["P"]

    if "C" in silver_res and "P" in silver_res:
        scv, sco = silver_res["C"]
        spv, spo = silver_res["P"]
    else:
        # 白银没读到就留空（None），不要写 0，否则图上会掉到 0
        print("【警告】没找到 SO 白银看涨/看跌行，白银列将留空。")
        scv = spv = sco = spo = None

    return [cv, pv, co, po, scv, spv, sco, spo]


def _int_or_none(v):
    if v is None:
        return None
    v = str(v).strip()
    if v == "":
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


def _ratio(num, den):
    if num is None or not den:
        return ""
    return f"{num / den:.4f}"


def read_rows(path: Path = CSV_PATH) -> dict:
    rows = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                d = (r.get("date") or "").strip()
                if not d:
                    continue
                rows[d] = [
                    _int_or_none(r.get("call_vol")), _int_or_none(r.get("put_vol")),
                    _int_or_none(r.get("call_oi")), _int_or_none(r.get("put_oi")),
                    _int_or_none(r.get("silver_call_vol")), _int_or_none(r.get("silver_put_vol")),
                    _int_or_none(r.get("silver_call_oi")), _int_or_none(r.get("silver_put_oi")),
                ]
    return rows


def write_rows(rows: dict, path: Path = CSV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for d in sorted(rows):
            cv, pv, co, po, scv, spv, sco, spo = rows[d]
            w.writerow([
                d,
                "" if cv is None else cv, "" if pv is None else pv,
                "" if co is None else co, "" if po is None else po,
                _ratio(pv, cv), _ratio(po, co),
                "" if scv is None else scv, "" if spv is None else spv,
                "" if sco is None else sco, "" if spo is None else spo,
                _ratio(spv, scv), _ratio(spo, sco),
            ])


def upsert(day: dt.date, counts, path: Path = CSV_PATH) -> None:
    rows = read_rows(path)
    rows[day.isoformat()] = list(counts)
    write_rows(rows, path)


def fetch_pg64_text() -> str:
    """下载 PG64（金属期权明细）。失败时抛异常，由调用方决定是否忽略，不影响 PG02B 的更新。"""
    import pdfplumber
    from curl_cffi import requests

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": REFERER,
    }
    r = requests.get(URL_PG64, headers=headers, impersonate="chrome120", timeout=120)
    r.raise_for_status()
    if not r.content.startswith(b"%PDF"):
        raise RuntimeError("PG64 返回的不是 PDF")
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        return chr(10).join(p.extract_text() or "" for p in pdf.pages)


def upsert_expiry(rows: list, path: Path = EXPIRY_CSV_PATH) -> None:
    """按日期整天替换 by_expiry.csv 里的行（同一天的初步版会被最终版覆盖）。"""
    import pg64

    existing = []
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            existing = list(csv.DictReader(f))
    days = {r["date"] for r in rows}
    kept = [r for r in existing if r["date"] not in days]
    merged = kept + [{k: r[k] for k in pg64.FIELDS} for r in rows]
    merged.sort(key=lambda r: (r["date"], r["metal"], r["kind"], r["code"], r["month"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=pg64.FIELDS)
        w.writeheader()
        w.writerows(merged)


def update_by_expiry(pg02b_day: dt.date, pg02b_counts) -> None:
    """抓取 PG64，按到期月拆分并写入 data/by_expiry.csv；任何失败只告警，不影响 PG02B。"""
    try:
        import pg64

        text = fetch_pg64_text()
        day, rows = pg64.extract(text)
        if not rows:
            raise ValueError("PG64 没解析出任何行")
        tot = pg64.check_against_pg02b(rows)
        g = tot.get("gold", [0, 0, 0, 0])
        exp = [pg02b_counts[0], pg02b_counts[1], pg02b_counts[2], pg02b_counts[3]]
        if day == pg02b_day and g != exp:
            print(f"【警告】PG64 黄金月度合计 {g} 与 PG02B {exp} 不一致，仍写入但请留意。")
        upsert_expiry(rows)
        print(f"【成功】{day} 按到期月拆分已写入 {len(rows)} 行（黄金月度合计 {g}）")
    except Exception as e:
        print(f"【警告】按到期月拆分失败，已跳过（不影响 PG02B 数据）: {e}")


def main() -> None:
    text = fetch_text()
    day = bulletin_date(text)
    tag = "含 PRELIMINARY 标注" if "PRELIMINARY" in text else "无初步版标注"

    counts = get_counts(text)
    upsert(day, counts)
    print(f"【成功】{day}（{tag}）黄金与白银数据已同步：{counts}")
    update_by_expiry(day, counts)


if __name__ == "__main__":
    main()
