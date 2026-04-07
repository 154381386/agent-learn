#!/usr/bin/env python3
"""
A股数据接口 - 实时行情模块
==========================
数据源优先级：新浪接口 > 腾讯接口 > AKShare

Usage:
    python stock_quote.py 600309              # 获取实时行情
    python stock_quote.py 600309 000001       # 批量获取
    python stock_quote.py 600309 --orderbook  # 含五档盘口
"""
import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from typing import Optional
from datetime import datetime


@dataclass
class Quote:
    """实时行情数据结构"""
    symbol: str
    name: str
    price: float
    prev_close: float
    open: float
    high: float
    low: float
    volume: int  # 成交量 (股)
    amount: float  # 成交额 (元)
    change: float = 0.0  # 涨跌额
    change_pct: float = 0.0  # 涨跌幅 (%)
    bid1_vol: int = 0
    bid1_price: float = 0.0
    bid2_vol: int = 0
    bid2_price: float = 0.0
    bid3_vol: int = 0
    bid3_price: float = 0.0
    bid4_vol: int = 0
    bid4_price: float = 0.0
    bid5_vol: int = 0
    bid5_price: float = 0.0
    ask1_vol: int = 0
    ask1_price: float = 0.0
    ask2_vol: int = 0
    ask2_price: float = 0.0
    ask3_vol: int = 0
    ask3_price: float = 0.0
    ask4_vol: int = 0
    ask4_price: float = 0.0
    ask5_vol: int = 0
    ask5_price: float = 0.0
    pe_ttm: Optional[float] = None  # 市盈率 (腾讯)
    pb: Optional[float] = None  # 市净率 (腾讯)
    timestamp: str = ""
    source: str = ""  # 数据来源


def normalize_symbol(symbol: str) -> str:
    """标准化股票代码为 6 位"""
    sym = symbol.strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    return sym.zfill(6)


def get_exchange_prefix(symbol: str) -> str:
    """根据股票代码判断交易所前缀"""
    sym = normalize_symbol(symbol)
    if sym.startswith(("6", "5")):
        return "sh"
    elif sym.startswith(("0", "3")):
        return "sz"
    elif sym.startswith(("4", "8")):
        return "bj"
    return "sh"


def safe_float(val, default=0.0) -> float:
    """安全转换为浮点数"""
    if val is None or val == "" or val == "null":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val, default=0) -> int:
    """安全转换为整数"""
    if val is None or val == "" or val == "null":
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def fetch_quote_sina(symbol: str) -> Optional[Quote]:
    """
    从新浪接口获取实时行情

    接口地址: http://hq.sinajs.cn/list={code}
    返回格式: var hq_str_sh600309="万华化学,今开,昨收,当前,最高,最低,...";
    字段索引: [0]=名称, [1]=今开, [2]=昨收, [3]=当前价, [4]=最高, [5]=最低,
              [6]=买一价, [7]=卖一价, [8]=成交量, [9]=成交额,
              [10-19]=买盘(量价交替), [20-29]=卖盘(量价交替)
    """
    sym = normalize_symbol(symbol)
    prefix = get_exchange_prefix(symbol)
    code = f"{prefix}{sym}"
    
    try:
        cmd = [
            "curl", "-s",
            "-H", "User-Agent: Mozilla/5.0",
            "-H", "Referer: https://finance.sina.com.cn/",
            f"http://hq.sinajs.cn/list={code}"
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=10)
        
        if not result.stdout:
            return None
        
        # 解码 GBK
        text = result.stdout.decode('gbk', errors='ignore')
        
        # 提取数据
        match = re.search(r'="([^"]*)"', text)
        if not match:
            return None
        
        fields = match.group(1).split(',')
        if len(fields) < 33:
            return None
        
        # 解析字段（新浪接口字段顺序：[1]=开盘价，[2]=昨收价，[3]=当前价）
        name = fields[0]
        open_price = safe_float(fields[1])   # 今开价
        prev_close = safe_float(fields[2])    # 昨收价
        price = safe_float(fields[3])         # 当前价/最新价
        high = safe_float(fields[4])          # 最高价
        low = safe_float(fields[5])           # 最低价
        volume = safe_int(fields[8])          # 成交量
        amount = safe_float(fields[9])        # 成交额
        
        # 计算涨跌
        change = round(price - prev_close, 2) if prev_close > 0 else 0
        change_pct = round((change / prev_close) * 100, 2) if prev_close > 0 else 0
        
        # 五档盘口（新浪接口格式：[量，价] 交替）
        # 买盘：[10]=量，[11]=价，[12]=量，[13]=价...
        # 卖盘：[20]=量，[21]=价，[22]=量，[23]=价...

        # 买盘
        bid1_vol = safe_int(fields[10])
        bid1_price = safe_float(fields[11])
        bid2_vol = safe_int(fields[12])
        bid2_price = safe_float(fields[13])
        bid3_vol = safe_int(fields[14])
        bid3_price = safe_float(fields[15])
        bid4_vol = safe_int(fields[16])
        bid4_price = safe_float(fields[17])
        bid5_vol = safe_int(fields[18])
        bid5_price = safe_float(fields[19])

        # 卖盘
        ask1_vol = safe_int(fields[20])
        ask1_price = safe_float(fields[21])
        ask2_vol = safe_int(fields[22])
        ask2_price = safe_float(fields[23])
        ask3_vol = safe_int(fields[24])
        ask3_price = safe_float(fields[25])
        ask4_vol = safe_int(fields[26])
        ask4_price = safe_float(fields[27])
        ask5_vol = safe_int(fields[28])
        ask5_price = safe_float(fields[29])
        
        # 时间
        date_str = fields[30] if len(fields) > 30 else ""
        time_str = fields[31] if len(fields) > 31 else ""
        timestamp = f"{date_str} {time_str}"
        
        return Quote(
            symbol=sym,
            name=name,
            price=price,
            prev_close=prev_close,
            open=open_price,
            high=high,
            low=low,
            volume=volume,
            amount=amount,
            change=change,
            change_pct=change_pct,
            bid1_vol=bid1_vol,
            bid1_price=bid1_price,
            bid2_vol=bid2_vol,
            bid2_price=bid2_price,
            bid3_vol=bid3_vol,
            bid3_price=bid3_price,
            bid4_vol=bid4_vol,
            bid4_price=bid4_price,
            bid5_vol=bid5_vol,
            bid5_price=bid5_price,
            ask1_vol=ask1_vol,
            ask1_price=ask1_price,
            ask2_vol=ask2_vol,
            ask2_price=ask2_price,
            ask3_vol=ask3_vol,
            ask3_price=ask3_price,
            ask4_vol=ask4_vol,
            ask4_price=ask4_price,
            ask5_vol=ask5_vol,
            ask5_price=ask5_price,
            timestamp=timestamp,
            source="sina"
        )
        
    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        print(f"新浪接口错误: {e}", file=sys.stderr)
        return None


def fetch_quote_tencent(symbol: str) -> Optional[Quote]:
    """
    从腾讯接口获取实时行情（含 PE/PB）
    
    接口地址: http://qt.gtimg.cn/q={code}
    返回格式: v_sh600309="1~万华化学~600309~...";
    """
    sym = normalize_symbol(symbol)
    prefix = get_exchange_prefix(symbol)
    code = f"{prefix}{sym}"
    
    try:
        cmd = ["curl", "-s", f"http://qt.gtimg.cn/q={code}"]
        result = subprocess.run(cmd, capture_output=True, timeout=10)
        
        if not result.stdout:
            return None
        
        # 解码 GBK
        text = result.stdout.decode('gbk', errors='ignore')
        
        # 提取数据
        match = re.search(r'="([^"]*)"', text)
        if not match:
            return None
        
        fields = match.group(1).split('~')
        if len(fields) < 50:
            return None
        
        # 解析字段
        name = fields[1]
        price = safe_float(fields[3])
        prev_close = safe_float(fields[4])
        open_price = safe_float(fields[5])
        volume = safe_int(fields[6]) * 100  # 腾讯单位是手，转换为股
        amount = safe_float(fields[8]) * 10000  # 腾讯单位是万元，转换为元
        high = safe_float(fields[33])
        low = safe_float(fields[34])
        change = safe_float(fields[31])
        change_pct = safe_float(fields[32])
        
        # PE/PB（腾讯接口：[52]=PE TTM, [46]=PB）
        pe_ttm = safe_float(fields[52], None) if len(fields) > 52 else None
        pb = safe_float(fields[46], None) if len(fields) > 46 else None
        
        # 五档盘口（腾讯格式：价格在前，数量在后；单位是手，转换为股）
        bid1_price = safe_float(fields[9])
        bid1_vol = safe_int(fields[10]) * 100
        bid2_price = safe_float(fields[11])
        bid2_vol = safe_int(fields[12]) * 100
        bid3_price = safe_float(fields[13])
        bid3_vol = safe_int(fields[14]) * 100
        bid4_price = safe_float(fields[15])
        bid4_vol = safe_int(fields[16]) * 100
        bid5_price = safe_float(fields[17])
        bid5_vol = safe_int(fields[18]) * 100

        ask1_price = safe_float(fields[19])
        ask1_vol = safe_int(fields[20]) * 100
        ask2_price = safe_float(fields[21])
        ask2_vol = safe_int(fields[22]) * 100
        ask3_price = safe_float(fields[23])
        ask3_vol = safe_int(fields[24]) * 100
        ask4_price = safe_float(fields[25])
        ask4_vol = safe_int(fields[26]) * 100
        ask5_price = safe_float(fields[27])
        ask5_vol = safe_int(fields[28]) * 100
        
        # 时间戳
        ts = fields[30] if len(fields) > 30 else ""
        if len(ts) >= 14:
            timestamp = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]} {ts[8:10]}:{ts[10:12]}:{ts[12:14]}"
        else:
            timestamp = ""
        
        return Quote(
            symbol=sym,
            name=name,
            price=price,
            prev_close=prev_close,
            open=open_price,
            high=high,
            low=low,
            volume=volume,
            amount=amount,
            change=change,
            change_pct=change_pct,
            bid1_vol=bid1_vol,
            bid1_price=bid1_price,
            bid2_vol=bid2_vol,
            bid2_price=bid2_price,
            bid3_vol=bid3_vol,
            bid3_price=bid3_price,
            bid4_vol=bid4_vol,
            bid4_price=bid4_price,
            bid5_vol=bid5_vol,
            bid5_price=bid5_price,
            ask1_vol=ask1_vol,
            ask1_price=ask1_price,
            ask2_vol=ask2_vol,
            ask2_price=ask2_price,
            ask3_vol=ask3_vol,
            ask3_price=ask3_price,
            ask4_vol=ask4_vol,
            ask4_price=ask4_price,
            ask5_vol=ask5_vol,
            ask5_price=ask5_price,
            pe_ttm=pe_ttm,
            pb=pb,
            timestamp=timestamp,
            source="tencent"
        )
        
    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        print(f"腾讯接口错误: {e}", file=sys.stderr)
        return None


def get_quote(symbol: str, include_valuation: bool = False) -> Optional[Quote]:
    """
    获取实时行情（带自动降级）
    
    优先级：新浪 > 腾讯
    """
    # 首选：新浪接口
    quote = fetch_quote_sina(symbol)
    if quote and quote.name:
        # 如果需要估值数据，用腾讯补充 PE/PB
        if include_valuation:
            tencent_quote = fetch_quote_tencent(symbol)
            if tencent_quote:
                quote.pe_ttm = tencent_quote.pe_ttm
                quote.pb = tencent_quote.pb
        return quote
    
    # 降级：腾讯接口
    quote = fetch_quote_tencent(symbol)
    if quote and quote.name:
        return quote
    
    return None


def get_quotes(symbols: list, include_valuation: bool = False) -> list:
    """批量获取实时行情"""
    results = []
    for sym in symbols:
        quote = get_quote(sym, include_valuation)
        if quote:
            results.append(quote)
        else:
            results.append({"symbol": normalize_symbol(sym), "error": "获取失败"})
    return results


def format_quote_table(quote: Quote) -> str:
    """格式化输出行情表格"""
    lines = []
    lines.append(f"\n{'='*50}")
    lines.append(f"  {quote.name} ({quote.symbol})")
    lines.append(f"{'='*50}")
    lines.append(f"  当前价: ¥{quote.price:.2f}    涨跌: {quote.change:+.2f} ({quote.change_pct:+.2f}%)")
    lines.append(f"  昨收: ¥{quote.prev_close:.2f}    今开: ¥{quote.open:.2f}")
    lines.append(f"  最高: ¥{quote.high:.2f}      最低: ¥{quote.low:.2f}")
    lines.append(f"  成交量: {quote.volume:,} 股")
    lines.append(f"  成交额: {quote.amount:,.0f} 元")
    
    if quote.pe_ttm or quote.pb:
        lines.append(f"{'─'*50}")
        pe_str = f"PE: {quote.pe_ttm:.2f}" if quote.pe_ttm else "PE: --"
        pb_str = f"PB: {quote.pb:.2f}" if quote.pb else "PB: --"
        lines.append(f"  {pe_str}    {pb_str}")
    
    lines.append(f"{'─'*50}")
    lines.append(f"  {'买盘':^20}    {'卖盘':^20}")
    lines.append(f"  {'─'*18}    {'─'*18}")
    lines.append(f"  买一: {quote.bid1_vol:>8,} 股 @ ¥{quote.bid1_price:.2f}    卖一: {quote.ask1_vol:>8,} 股 @ ¥{quote.ask1_price:.2f}")
    lines.append(f"  买二: {quote.bid2_vol:>8,} 股 @ ¥{quote.bid2_price:.2f}    卖二: {quote.ask2_vol:>8,} 股 @ ¥{quote.ask2_price:.2f}")
    lines.append(f"  买三: {quote.bid3_vol:>8,} 股 @ ¥{quote.bid3_price:.2f}    卖三: {quote.ask3_vol:>8,} 股 @ ¥{quote.ask3_price:.2f}")
    lines.append(f"  买四: {quote.bid4_vol:>8,} 股 @ ¥{quote.bid4_price:.2f}    卖四: {quote.ask4_vol:>8,} 股 @ ¥{quote.ask4_price:.2f}")
    lines.append(f"  买五: {quote.bid5_vol:>8,} 股 @ ¥{quote.bid5_price:.2f}    卖五: {quote.ask5_vol:>8,} 股 @ ¥{quote.ask5_price:.2f}")
    lines.append(f"{'='*50}")
    lines.append(f"  数据来源: {quote.source}    更新时间: {quote.timestamp}")
    lines.append(f"{'='*50}\n")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="A股实时行情接口")
    parser.add_argument("symbols", nargs="+", help="股票代码 (如 600309)")
    parser.add_argument("--orderbook", "-o", action="store_true", help="显示五档盘口")
    parser.add_argument("--valuation", "-v", action="store_true", help="显示估值指标 (PE/PB)")
    parser.add_argument("--json", "-j", action="store_true", help="JSON 格式输出")
    
    args = parser.parse_args()
    
    if len(args.symbols) == 1:
        quote = get_quote(args.symbols[0], args.valuation)
        if quote:
            if args.json:
                print(json.dumps(asdict(quote), indent=2, ensure_ascii=False))
            else:
                print(format_quote_table(quote))
        else:
            print(f"错误: 无法获取 {args.symbols[0]} 的行情数据")
            sys.exit(1)
    else:
        quotes = get_quotes(args.symbols, args.valuation)
        if args.json:
            output = [asdict(q) if isinstance(q, Quote) else q for q in quotes]
            print(json.dumps(output, indent=2, ensure_ascii=False))
        else:
            for q in quotes:
                if isinstance(q, Quote):
                    print(format_quote_table(q))
                else:
                    print(f"\n错误: {q.get('symbol')} - {q.get('error')}\n")


if __name__ == "__main__":
    main()