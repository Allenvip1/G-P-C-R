#!/usr/bin/env python3
"""解析 CME Daily Bulletin PG64（Metals Option Products），按到期月拆出 COMEX 黄金(OG)/白银(SO) 期权。

输出每个 (日期, 品种, 类型, 代码, 到期月) 一行：看涨/看跌 成交量、持仓量，以及到期日。
  kind = monthly  标准月度期权 OG / SO（和 PG02B 汇总口径一致，合计会精确等于 PG02B）
  kind = weekly   周五周度期权 OG1-OG4 / SO1-SO4（PG02B 汇总里不含）
周一至周四的周度期权在 PDF 文字里看涨看跌混排，无法区分，已忽略。
"""
import re
import collections
import datetime as dt

MONTHS = {m: i for i, m in enumerate("JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), 1)}

# CME 休市日（用于推算标准月度期权到期日：前一个月的倒数第 4 个营业日）
HOLIDAYS = {dt.date(*d) for d in [
    (2026, 1, 1), (2026, 1, 19), (2026, 2, 16), (2026, 4, 3), (2026, 5, 25), (2026, 6, 19),
    (2026, 7, 3), (2026, 9, 7), (2026, 11, 26), (2026, 12, 25),
    (2027, 1, 1), (2027, 1, 18), (2027, 2, 15), (2027, 3, 26), (2027, 5, 31), (2027, 6, 18),
    (2027, 7, 5), (2027, 9, 6), (2027, 11, 25), (2027, 12, 24),
    (2028, 1, 17), (2028, 2, 21), (2028, 4, 14), (2028, 5, 29), (2028, 6, 19),
    (2028, 7, 4), (2028, 9, 4), (2028, 11, 23), (2028, 12, 25),
]}


def _is_bday(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in HOLIDAYS


def monthly_expiry(month_label: str) -> dt.date:
    """NOV26 -> 前一个月（2026-10）倒数第 4 个营业日。"""
    y, m = 2000 + int(month_label[3:]), MONTHS[month_label[:3]]
    d = dt.date(y, m, 1) - dt.timedelta(days=1)                             # 前一个月最后一天
    n = 0
    while True:
        if _is_bday(d):
            n += 1
            if n == 4:
                return d
        d -= dt.timedelta(days=1)


def bulletin_date(text: str) -> dt.date:
    m = re.search(r"METALS OPTION PRODUCTS\s+\w{3}, (\w{3} \d{2}, \d{4})", text)
    if not m:
        raise ValueError("找不到 PG64 公报日期")
    return dt.datetime.strptime(m.group(1), "%b %d, %Y").date()


def weekly_expiries(text: str, day: dt.date) -> dict:
    """从开头的到期日索引取 OG1-4 / SO1-4 的到期日，如 'OG1 CALL 10/02'。"""
    out = {}
    for ln in text.splitlines():
        m = re.match(r"^((?:OG|SO)[1-4]) (?:CALL|PUT) (\d\d)/(\d\d)$", ln.strip())
        if m:
            mo, dd = int(m.group(2)), int(m.group(3))
            y = day.year + (1 if mo < day.month - 6 else 0)
            out[m.group(1)] = dt.date(y, mo, dd)
    return out


def monthly_expiries_from_index(text: str, day: dt.date) -> dict:
    """索引行 'OG CALL 10/27 11/24 ...'：到期日所在月的下一个月就是合约月（10/27 -> NOV26）。"""
    out = {}
    for ln in text.splitlines():
        m = re.match(r"^(OG|SO) CALL ((?:\d\d/\d\d ?)+)$", ln.strip())
        if not m:
            continue
        year, prev = day.year, None
        for tok in m.group(2).split():
            mo, dd = int(tok[:2]), int(tok[3:])
            if prev is None and mo < day.month - 1:   # 第一个日期已跨年（如 12 月的公报里出现 01/xx）
                year += 1
            elif prev is not None and mo < prev:       # 日期是升序的，月份变小说明跨年
                year += 1
            prev = mo
            d = dt.date(year, mo, dd)
            ny, nm = (d.year + (d.month == 12), d.month % 12 + 1)
            label = f"{list(MONTHS)[nm - 1]}{str(ny)[2:]}"
            out[(m.group(1), label)] = d
    return out


def _parse_total(ln: str):
    rest = ln[len("TOTAL"):].replace(",", "").strip()
    toks = re.sub(r"(\d)([+-])", r"\1 \2 ", rest).split()
    if "+" in toks or "-" in toks:
        s = next(i for i, t in enumerate(toks) if t in ("+", "-"))
        if s < 1:
            return None
        try:
            return sum(int(x) for x in toks[:s - 1]), int(toks[s - 1])
        except ValueError:
            return None
    try:
        nums = [int(x) for x in toks]
    except ValueError:
        return None
    if len(nums) >= 2 and nums[-1] == 0:
        return sum(nums[:-2]), nums[-2]
    return None


_HDR = re.compile(r"^([A-Z0-9]{2,4}) (CALL|PUT) .*OPTION")
_OTHER_HDR = re.compile(r"^[A-Z0-9]{2,4} [A-Z0-9]{2,4} .*(OPTION|OPT)\b")


def _totals(text: str):
    prod = mon = None
    res = collections.defaultdict(lambda: [0, 0])
    for ln in text.splitlines():
        s = ln.strip()
        m = _HDR.match(s)
        if m:
            newprod = (m.group(1), m.group(2))
            if newprod != prod:          # 同一产品跨页时页眉会重复，月份要沿用，否则跨页月份的 TOTAL 会丢
                mon = None
            prod = newprod
            continue
        if _OTHER_HDR.match(s) and not s.startswith("TOTAL"):
            prod = mon = None                      # 其它产品头（周一至周四周度、微型、其它品种）
            continue
        if re.match(r"^[A-Z]{3}\d{2}$", s):
            mon = s
            continue
        if s.startswith("TOTAL ") and prod and mon:
            r = _parse_total(s)
            if r:
                res[(prod[0], prod[1], mon)][0] += r[0]
                res[(prod[0], prod[1], mon)][1] += r[1]
    return res


def extract(text: str):
    """返回 (公报日期, rows)。rows 为 dict 列表，字段见 FIELDS。"""
    day = bulletin_date(text)
    wk = weekly_expiries(text, day)
    idx = monthly_expiries_from_index(text, day)
    tot = _totals(text)
    rows = []
    keys = {(c, m) for (c, s, m) in tot if c in ("OG", "SO", "OG1", "OG2", "OG3", "OG4", "SO1", "SO2", "SO3", "SO4")}
    for code, mon in sorted(keys):
        cv, co = tot.get((code, "CALL", mon), [0, 0])
        pv, po = tot.get((code, "PUT", mon), [0, 0])
        if not (cv or pv or co or po):
            continue
        weekly = code not in ("OG", "SO")
        expiry = wk.get(code) if weekly else idx.get((code, mon), monthly_expiry(mon))
        rows.append({
            "date": day.isoformat(),
            "metal": "gold" if code.startswith("OG") else "silver",
            "kind": "weekly" if weekly else "monthly",
            "code": code,
            "month": mon,
            "expiry": expiry.isoformat() if expiry else "",
            "call_vol": cv, "put_vol": pv, "call_oi": co, "put_oi": po,
        })
    return day, rows


FIELDS = ["date", "metal", "kind", "code", "month", "expiry", "call_vol", "put_vol", "call_oi", "put_oi"]


def check_against_pg02b(rows):
    """monthly 行合计，应等于 PG02B 的 OG/SO 汇总。"""
    s = collections.defaultdict(lambda: [0, 0, 0, 0])
    for r in rows:
        if r["kind"] == "monthly":
            t = s[r["metal"]]
            t[0] += r["call_vol"]; t[1] += r["put_vol"]; t[2] += r["call_oi"]; t[3] += r["put_oi"]
    return dict(s)


if __name__ == "__main__":
    import sys
    import pdfplumber
    with pdfplumber.open(sys.argv[1]) as pdf:
        text = "\n".join(p.extract_text() or "" for p in pdf.pages)
    day, rows = extract(text)
    print(day, len(rows), "行")
    print(check_against_pg02b(rows))
    for r in rows[:6] + rows[-4:]:
        print(r)
