#!/usr/bin/env python3
"""
A 股数据接口 - 估值指标模块
==========================
获取 A 股估值相关数据：PE、PB、市值、股本等。

数据源：腾讯接口

Usage:
    python stock_valuation.py 600309              # 获取估值指标
    python stock_valuation.py 600309 --json       # JSON 格式输出
    python stock_valuation.py 600309 000001       # 批量获取
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
class Valuation:
    """估值指标数据结构"""
    symbol: str                           # 股票代码
    name: str                             # 股票名称
    pe_ttm: Optional[float]               # 市盈率 TTM
    pb: Optional[float]                   # 市净率
    ps_ttm: Optional[float]               # 市销率 TTM
    total_market_cap: Optional[float]     # 总市值 (元)
    circulating_market_cap: Optional[float]  # 流通市值 (元)
    total_shares: Optional[float]         # 总股本 (股)
    circulating_shares: Optional[float]   # 流通股本 (股)
    source: str                           # 数据来源


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


def safe_float(val, default=None) -> Optional[float]:
    """安全转换为浮点数"""
    if val is None or val == "" or val == "null" or val == "-":
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_int(val, default=None) -> Optional[int]:
    """安全转换为整数"""
    if val is None or val == "" or val == "null" or val == "-":
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def fetch_valuation(symbol: str) -> Optional[Valuation]:
    """
    从腾讯接口获取估值指标

    接口地址：http://qt.gtimg.cn/q={code}
    返回格式：v_sh600309="1~万华化学~600309~...";

    字段说明 (根据实际数据分析):
    [38] = 市盈率动
    [39] = PE TTM (市盈率)
    [40] = 静市盈率
    [41] = 最高
    [42] = 最低
    [43] = PS TTM (市销率)
    [44] = 总市值 (亿元)
    [45] = 总市值 (亿元) 重复
    [46] = 市净率
    [65] = 总股本 (亿股)
    [72] = 总股本 (股)
    [73] = 流通股本 (股)
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
        name = fields[1] if len(fields) > 1 else ""

        # PE TTM (字段 39)
        pe_ttm = safe_float(fields[39]) if len(fields) > 39 else None

        # PB 市净率 (字段 46)
        pb = safe_float(fields[46]) if len(fields) > 46 else None

        # PS TTM (字段 43)
        ps_ttm = safe_float(fields[43]) if len(fields) > 43 else None

        # 市值数据 (单位：亿元)
        # 总市值 (字段 44)
        total_market_cap = None
        if len(fields) > 44:
            val = safe_float(fields[44])
            if val is not None and val > 0:
                total_market_cap = val * 100000000  # 亿元 -> 元

        # 流通市值 = 流通股本 * 股价
        circulating_market_cap = None

        # 股本数据 (单位：股)
        # 总股本 (字段 72)
        total_shares = None
        if len(fields) > 72:
            val = safe_float(fields[72])
            if val is not None and val > 0:
                total_shares = val  # 本身就是股

        # 流通股本 (字段 73)
        circulating_shares = None
        if len(fields) > 73:
            val = safe_float(fields[73])
            if val is not None and val > 0:
                circulating_shares = val  # 本身就是股
                # 计算流通市值
                if total_market_cap and total_shares and circulating_shares:
                    # 流通市值 = 总市值 * (流通股本/总股本)
                    circulating_market_cap = total_market_cap * (circulating_shares / total_shares)

        return Valuation(
            symbol=sym,
            name=name,
            pe_ttm=pe_ttm,
            pb=pb,
            ps_ttm=ps_ttm,
            total_market_cap=total_market_cap,
            circulating_market_cap=circulating_market_cap,
            total_shares=total_shares,
            circulating_shares=circulating_shares,
            source="tencent"
        )

    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        print(f"获取估值数据错误：{e}", file=sys.stderr)
        return None


def get_valuation(symbol: str) -> Optional[Valuation]:
    """
    获取估值指标

    数据源：腾讯接口
    """
    return fetch_valuation(symbol)


def get_valuations(symbols: list) -> list:
    """批量获取估值指标"""
    results = []
    for sym in symbols:
        valuation = get_valuation(sym)
        if valuation:
            results.append(valuation)
        else:
            results.append({"symbol": normalize_symbol(sym), "error": "获取失败"})
    return results


def format_valuation_table(valuation: Valuation) -> str:
    """格式化输出估值表格"""
    lines = []
    lines.append(f"\n{'='*50}")
    lines.append(f"  {valuation.name} ({valuation.symbol})")
    lines.append(f"{'='*50}")

    # 估值指标
    lines.append(f"\n  估值指标")
    lines.append(f"  {'─'*40}")
    pe_str = f"PE: {valuation.pe_ttm:.2f}" if valuation.pe_ttm else "PE: --"
    pb_str = f"PB: {valuation.pb:.2f}" if valuation.pb else "PB: --"
    ps_str = f"PS: {valuation.ps_ttm:.2f}" if valuation.ps_ttm else "PS: --"
    lines.append(f"  {pe_str}    {pb_str}    {ps_str}")

    # 市值数据
    lines.append(f"\n  市值数据")
    lines.append(f"  {'─'*40}")
    if valuation.total_market_cap:
        lines.append(f"  总市值：{valuation.total_market_cap / 100000000:.2f} 亿元")
    else:
        lines.append(f"  总市值：--")
    if valuation.circulating_market_cap:
        lines.append(f"  流通市值：{valuation.circulating_market_cap / 100000000:.2f} 亿元")
    else:
        lines.append(f"  流通市值：--")

    # 股本数据
    lines.append(f"\n  股本结构")
    lines.append(f"  {'─'*40}")
    if valuation.total_shares:
        lines.append(f"  总股本：{valuation.total_shares / 100000000:.2f} 亿股")
    else:
        lines.append(f"  总股本：--")
    if valuation.circulating_shares:
        lines.append(f"  流通股本：{valuation.circulating_shares / 100000000:.2f} 亿股")
    else:
        lines.append(f"  流通股本：--")

    lines.append(f"\n{'='*50}")
    lines.append(f"  数据来源：{valuation.source}")
    lines.append(f"{'='*50}\n")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="A 股估值指标接口")
    parser.add_argument("symbols", nargs="+", help="股票代码 (如 600309)")
    parser.add_argument("--json", "-j", action="store_true", help="JSON 格式输出")

    args = parser.parse_args()

    if len(args.symbols) == 1:
        valuation = get_valuation(args.symbols[0])
        if valuation:
            if args.json:
                print(json.dumps(asdict(valuation), indent=2, ensure_ascii=False))
            else:
                print(format_valuation_table(valuation))
        else:
            print(f"错误：无法获取 {args.symbols[0]} 的估值数据")
            sys.exit(1)
    else:
        valuations = get_valuations(args.symbols)
        if args.json:
            output = [asdict(v) if isinstance(v, Valuation) else v for v in valuations]
            print(json.dumps(output, indent=2, ensure_ascii=False))
        else:
            for v in valuations:
                if isinstance(v, Valuation):
                    print(format_valuation_table(v))
                else:
                    print(f"\n错误：{v.get('symbol')} - {v.get('error')}\n")


if __name__ == "__main__":
    main()