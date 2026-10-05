#!/usr/bin/env python3
"""
每日抓取 CME Daily Bulletin 的 PG02B，读取 COMEX 黄金期权（OG）和白银期权（SO）
的看涨/看跌成交量和持仓量（全部到期月合计），写入 data/gold_pcr.csv（同一天重复运行会覆盖）。
"""
import csv
import re
import sys
import datetime as dt
from pathlib import Path

URL = ("https://cmegroup.com"
       "Section02B_Summary_Volume_And_Open_Interest_Metals_Futures_And_Options.pdf")
CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "gold_pcr.csv"

# 更新表头，加入白银的 6 个核心数据列
HEADER = [
    "date", 
    "call_vol", "put_vol", "call_oi", "put_oi", "vol_pcr", "oi_pcr",
    "silver_call_vol", "silver_put_vol", "silver_call_oi", "silver_put_oi", "silver_vol_pcr", "silver_oi_pcr"
]

def fetch_text() -> str:
    import io
    import pdfplumber
    from curl_cffi import requests
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://cmegroup.com"
    }
    
    try:
        r = requests.get(URL, headers=headers, impersonate="chrome120", timeout=60)
        # 🎯 彻底修正此处：补齐了备份历史通道域名后的关键斜杠 '/'
        if r.status_code == 404:
            url_backup = "https://cmegroup.com"
            r = requests.get(url_backup, headers=headers, impersonate="chrome120", timeout=60)
        r.raise_for_status()
    except Exception as e:
        raise RuntimeError(f"CME 官网连接失败或文件尚未发布: {e}")

    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        return "\n".join(p.extract_text() or "" for p in pdf.pages)


def bulletin_date(text: str) -> dt.date:
    m = re.search(r"BULLETIN # \d+@?\s+\w{3},\s+(\w{3} \d{2}, \d{4})", text)
    if not m:
        raise ValueError("找不到公报日期（页眉格式可能变了）")
    return dt.datetime.strptime(m.group(1), "%b %d, %Y").date()


def parse_line(line: str):
    """同时匹配黄金(OG)和白银(SO)期权的看涨/看跌行"""
    m = re.match(r"^(OG|SO) COMEX (GOLD|SILVER) OPTIONS ([CP])\s+(.+)$", line.strip())
    if not m:
        return None
    
    product_code = m.group(1) # OG 或 SO
    side = m.group(3)         # C 或 P
    
    rest = re.sub(r"([+-])(?=\d)", r" \1 ", m.group(4)).split()
    s = next((i for i, t in enumerate(rest) if t in ("+", "-")), None)
    if s is None or s < 2:
        return None
    
    oi, vol = int(rest[s - 1]), int(rest[s - 2])
    parts = [int(x) for x in rest[: s - 2]]
    if parts and sum(parts) != vol:
        return None
        
    return product_code, side, vol, oi


def get_counts(text: str):
    gold_res = {}
    silver_res = {}
    
    for ln in text.splitlines():
        r = parse_line(ln)
        if r:
            prod, side, vol, oi = r
            if prod == "OG":
                gold_res[side] = [vol, oi]
            elif prod == "SO":
                silver_res[side] = [vol, oi]
                
    # 校验黄金数据
    if "C" not in gold_res or "P" not in gold_res:
        raise ValueError("没找到 OG 黄金看涨/看跌行")
    
    # 🎯 完美容错占位：如果周末没有抓到白银期权行，赋予安全列表避免后续运算崩溃
    if "C" not in silver_res or "P" not in silver_res:
        silver_res["C"] = [0, 0]
        silver_res["P"] = [0, 0]
        
    cv, pv, co, po = gold_res["C"] + gold_res["P"]
    scv, spv, sco, spo = silver_res["C"] + silver_res["P"]
    
    return cv, pv, co, po, scv, spv, sco, spo


def read_rows(path: Path = CSV_PATH) -> dict:
    rows = {}
    if path.exists():
        with path.open(newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                rows[r["date"]] = [
                    int(r.get("call_vol", 0)), int(r.get("put_vol", 0)), int(r.get("call_oi", 0)), int(r.get("put_oi", 0)),
                    int(r.get("silver_call_vol", 0)), int(r.get("silver_put_vol", 0)), int(r.get("silver_call_oi", 0)), int(r.get("silver_put_oi", 0))
                ]
    return rows


def write_rows(rows: dict, path: Path = CSV_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for d in sorted(rows):
            cv, pv, co, po, scv, spv, sco, spo = rows[d]
            # 🎯 零除安全保护：确保当看涨成交量或持仓量为 0 时输出 0.0000 从而不报错
            v_pcr = f"{pv / cv:.4f}" if cv else "0.0000"
            o_pcr = f"{po / co:.4f}" if co else "0.0000"
            sv_pcr = f"{spv / scv:.4f}" if scv else "0.0000"
            so_pcr = f"{spo / sco:.4f}" if sco else "0.0000"
            
            w.writerow([d, cv, pv, co, po, v_pcr, o_pcr, scv, spv, sco, spo, sv_pcr, so_pcr])


def upsert(day: dt.date, counts, path: Path = CSV_PATH) -> None:
    rows = read_rows(path)
    rows[day.isoformat()] = list(counts)
    write_rows(rows, path)


def main() -> None:
    text = fetch_text()
    day = bulletin_date(text)
    tag = "含 PRELIMINARY 标注" if "PRELIMINARY" in text else "无初步版标注"
    
    counts = get_counts(text)
    upsert(day, counts)
    print(f"【成功】{day} ({tag}) 黄金与白银双通道数据已成功更新并写入 CSV 库！")


if __name__ == "__main__":
    main()
