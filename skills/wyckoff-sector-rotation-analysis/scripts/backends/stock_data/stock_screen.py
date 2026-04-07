#!/usr/bin/env python3
"""
A 股数据接口 - 股票筛选模块
==========================
基于估值、财务指标筛选符合条件的股票。

数据源：腾讯接口 (估值) + AKShare (财务指标)

筛选条件:
    默认: PE<30, PB<5, ROE>8%, 资产负债率<60%
    支持自定义筛选条件

Usage:
    python stock_screen.py 600309 000001 600519          # 使用默认条件筛选
    python stock_screen.py 600309 000001 --max-pe 20     # 自定义 PE 上限
    python stock_screen.py 600309 000001 --min-roe 15    # 自定义 ROE 下限
    python stock_screen.py 600309 000001 --json          # JSON 格式输出
    python stock_screen.py 600309 000001 --show-failed   # 显示未通过原因
"""
import argparse
import json
import sys
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict
from datetime import datetime


# ==================== 导入现有模块 ====================

# 尝试从 stock_valuation 导入估值函数
try:
    from stock_valuation import get_valuation, normalize_symbol, safe_float, safe_int
except ImportError:
    # 如果导入失败，定义备用函数
    def normalize_symbol(symbol: str) -> str:
        sym = symbol.strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
        return sym.zfill(6)

    def safe_float(val, default=None):
        if val is None or val == "" or val == "null" or val == "-":
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    def safe_int(val, default=None):
        if val is None or val == "" or val == "null" or val == "-":
            return default
        try:
            return int(float(val))
        except (ValueError, TypeError):
            return default

    def get_valuation(symbol: str):
        return None


# ==================== 数据类 ====================

@dataclass
class ScreenResult:
    """筛选结果数据结构"""
    symbol: str                    # 股票代码
    name: str                      # 股票名称
    passed: bool                   # 是否通过筛选
    pe_ttm: Optional[float] = None # PE TTM
    pb: Optional[float] = None     # PB
    roe: Optional[float] = None    # ROE
    debt_ratio: Optional[float] = None  # 资产负债率
    gross_margin: Optional[float] = None  # 毛利率
    net_margin: Optional[float] = None    # 净利率
    revenue_growth: Optional[float] = None  # 营收增长率
    market_cap: Optional[float] = None    # 总市值 (元)
    fail_reasons: Optional[List[str]] = None  # 失败原因


# ==================== 财务数据获取 ====================

def fetch_financial_metrics(symbol: str) -> dict:
    """
    从 AKShare 获取财务指标

    使用函数：stock_financial_abstract_ths()
    """
    try:
        import akshare as ak

        sym = normalize_symbol(symbol)

        # 获取财务指标 (同花顺数据源 - 按报告期)
        try:
            df_fin = ak.stock_financial_abstract_ths(symbol=sym, indicator="按报告期")

            if df_fin is None or len(df_fin) == 0:
                # 尝试按年度获取
                df_fin = ak.stock_financial_abstract_ths(symbol=sym, indicator="按年度")

            if df_fin is None or len(df_fin) == 0:
                return {}

            # 获取最新一期数据
            latest = df_fin.iloc[-1]

            return {
                "roe": safe_float(latest.get("净资产收益率")),
                "gross_margin": safe_float(latest.get("销售毛利率")),
                "net_margin": safe_float(latest.get("销售净利率")),
                "roa": safe_float(latest.get("总资产报酬率")),
                "debt_ratio": safe_float(latest.get("资产负债率")),
                "current_ratio": safe_float(latest.get("流动比率")),
                "quick_ratio": safe_float(latest.get("速动比率")),
                "eps": safe_float(latest.get("基本每股收益")),
                "bvps": safe_float(latest.get("每股净资产")),
                "revenue_growth": safe_float(latest.get("营业总收入同比增长率")),
                "profit_growth": safe_float(latest.get("归母净利润同比增长率")),
            }

        except Exception as e:
            print(f"获取财务指标失败：{e}", file=sys.stderr)
            return {}

    except Exception as e:
        print(f"AKShare 错误：{e}", file=sys.stderr)
        return {}


def get_financial_metrics(symbol: str) -> dict:
    """获取财务指标 (带重试机制)"""
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            return fetch_financial_metrics(symbol)
        except Exception as e:
            if attempt < max_attempts - 1:
                import time
                time.sleep(1.0 * (2 ** attempt))
            else:
                print(f"获取财务指标失败：{e}", file=sys.stderr)
                return {}
    return {}


# ==================== 筛选逻辑 ====================

def get_default_filters() -> dict:
    """获取默认筛选条件"""
    return {
        "max_pe": 30.0,           # 市盈率 TTM < 30
        "max_pb": 5.0,            # 市净率 < 5
        "min_roe": 8.0,           # ROE > 8%
        "max_debt_ratio": 60.0,   # 资产负债率 < 60%
        "min_gross_margin": 0.0,  # 毛利率 > 0%
        "min_net_margin": 0.0,    # 净利率 > 0%
        "min_revenue_growth": 0.0, # 营收增长率 > 0%
        "min_market_cap": 0.0,    # 最小市值 (元)
        "max_market_cap": float('inf'),  # 最大市值 (元)
    }


def screen_single_stock(symbol: str, filters: dict) -> ScreenResult:
    """
    筛选单只股票

    Args:
        symbol: 股票代码
        filters: 筛选条件字典

    Returns:
        ScreenResult: 筛选结果
    """
    sym = normalize_symbol(symbol)
    result = ScreenResult(
        symbol=sym,
        name="",
        passed=True,
        fail_reasons=[]
    )

    # 1. 获取估值数据 (腾讯接口)
    valuation = get_valuation(sym)
    if valuation:
        result.name = valuation.name
        result.pe_ttm = valuation.pe_ttm
        result.pb = valuation.pb
        result.ps_ttm = valuation.ps_ttm
        result.market_cap = valuation.total_market_cap
    else:
        # 估值数据获取失败，尝试从 AKShare 获取
        try:
            import akshare as ak
            df_quote = ak.stock_zh_a_spot_em()
            if df_quote is not None and not df_quote.empty:
                row = df_quote[df_quote["代码"] == sym]
                if not row.empty:
                    row = row.iloc[0]
                    result.name = row.get("名称", "")
                    result.pe_ttm = safe_float(row.get("市盈率 - 动态"))
                    result.pb = safe_float(row.get("市净率"))
                    result.market_cap = safe_float(row.get("总市值")) * 100000000  # 亿元->元
        except Exception:
            pass

    # 2. 获取财务数据 (AKShare)
    financials = get_financial_metrics(sym)
    if financials:
        result.roe = financials.get("roe")
        result.gross_margin = financials.get("gross_margin")
        result.net_margin = financials.get("net_margin")
        result.debt_ratio = financials.get("debt_ratio")
        result.revenue_growth = financials.get("revenue_growth")

    # 3. 检查筛选条件
    fail_reasons = []

    # PE 检查
    pe = result.pe_ttm
    if pe is not None and pe <= 0:
        fail_reasons.append(f"PE {pe:.1f} 无效（亏损或数据异常）")
    elif pe is not None and pe > filters["max_pe"]:
        fail_reasons.append(f"PE {pe:.1f} > {filters['max_pe']:.1f}")

    # PB 检查
    pb = result.pb
    if pb is not None and pb > filters["max_pb"]:
        fail_reasons.append(f"PB {pb:.1f} > {filters['max_pb']:.1f}")

    # ROE 检查
    roe = result.roe
    if roe is not None and roe < filters["min_roe"]:
        fail_reasons.append(f"ROE {roe:.1f}% < {filters['min_roe']:.1f}%")

    # 资产负债率检查
    debt_ratio = result.debt_ratio
    if debt_ratio is not None and debt_ratio > filters["max_debt_ratio"]:
        fail_reasons.append(f"资产负债率 {debt_ratio:.1f}% > {filters['max_debt_ratio']:.1f}%")

    # 毛利率检查
    gross_margin = result.gross_margin
    if gross_margin is not None and gross_margin < filters["min_gross_margin"]:
        fail_reasons.append(f"毛利率 {gross_margin:.1f}% < {filters['min_gross_margin']:.1f}%")

    # 净利率检查
    net_margin = result.net_margin
    if net_margin is not None and net_margin < filters["min_net_margin"]:
        fail_reasons.append(f"净利率 {net_margin:.1f}% < {filters['min_net_margin']:.1f}%")

    # 营收增长率检查
    revenue_growth = result.revenue_growth
    if revenue_growth is not None and revenue_growth < filters["min_revenue_growth"]:
        fail_reasons.append(f"营收增长率 {revenue_growth:.1f}% < {filters['min_revenue_growth']:.1f}%")

    # 市值检查
    market_cap = result.market_cap
    if market_cap is not None:
        if market_cap < filters["min_market_cap"]:
            fail_reasons.append(f"市值 {market_cap/100000000:.1f}亿 < {filters['min_market_cap']/100000000:.1f}亿")
        if market_cap > filters["max_market_cap"]:
            fail_reasons.append(f"市值 {market_cap/100000000:.1f}亿 > {filters['max_market_cap']/100000000:.1f}亿")

    # 判断是否通过
    result.passed = len(fail_reasons) == 0
    result.fail_reasons = fail_reasons if fail_reasons else None

    return result


def screen_stocks(symbols: List[str], filters: Optional[dict] = None) -> List[ScreenResult]:
    """
    批量筛选股票

    Args:
        symbols: 股票代码列表
        filters: 筛选条件字典 (None 则使用默认条件)

    Returns:
        List[ScreenResult]: 筛选结果列表
    """
    if filters is None:
        filters = get_default_filters()

    results = []
    for sym in symbols:
        try:
            result = screen_single_stock(sym, filters)
            results.append(result)
        except Exception as e:
            results.append(ScreenResult(
                symbol=normalize_symbol(sym),
                name="",
                passed=False,
                fail_reasons=[f"获取数据失败：{e}"]
            ))

    return results


# ==================== 格式化输出 ====================

def format_screen_table(results: List[ScreenResult], show_failed: bool = False) -> str:
    """格式化输出筛选结果表格"""
    if not results:
        return "\n无筛选结果\n"

    lines = []
    lines.append(f"\n{'='*90}")
    lines.append(f"  股票筛选结果")
    lines.append(f"{'='*90}")

    # 统计
    passed_count = sum(1 for r in results if r.passed)
    failed_count = len(results) - passed_count

    lines.append(f"  总计：{len(results)} 只  通过：{passed_count} 只  未通过：{failed_count} 只")
    lines.append(f"{'─'*90}")

    # 通过筛选的股票
    lines.append(f"\n  【通过筛选的股票】")
    lines.append(f"  {'代码':<10} {'名称':<12} {'PE':>8} {'PB':>8} {'ROE(%)':>10} {'负债率 (%)':>10} {'毛利率 (%)':>10}")
    lines.append(f"  {'─'*78}")

    for r in results:
        if r.passed:
            pe_str = f"{r.pe_ttm:.1f}" if r.pe_ttm else "--"
            pb_str = f"{r.pb:.1f}" if r.pb else "--"
            roe_str = f"{r.roe:.1f}" if r.roe else "--"
            debt_str = f"{r.debt_ratio:.1f}" if r.debt_ratio else "--"
            gross_str = f"{r.gross_margin:.1f}" if r.gross_margin else "--"
            lines.append(f"  {r.symbol:<10} {r.name:<12} {pe_str:>8} {pb_str:>8} {roe_str:>10} {debt_str:>10} {gross_str:>10}")

    if passed_count == 0:
        lines.append(f"  无通过筛选的股票")

    # 显示未通过的股票（如果要求）
    if show_failed and failed_count > 0:
        lines.append(f"\n  【未通过筛选的股票】")
        lines.append(f"  {'代码':<10} {'名称':<12} {'未通过原因':<50}")
        lines.append(f"  {'─'*78}")

        for r in results:
            if not r.passed and r.fail_reasons:
                reason_str = "; ".join(r.fail_reasons[:2])  # 最多显示 2 个原因
                name = r.name if r.name else "未知"
                lines.append(f"  {r.symbol:<10} {name:<12} {reason_str:<50}")

    lines.append(f"\n{'='*90}")
    lines.append(f"  筛选时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"{'='*90}\n")

    return "\n".join(lines)


def screen_summary(results: List[ScreenResult]) -> str:
    """生成筛选摘要"""
    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]

    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"  筛选摘要")
    lines.append(f"{'='*60}")
    lines.append(f"  筛选股票数：{len(results)}")
    lines.append(f"  通过数量：{len(passed)} ({len(passed)/len(results)*100:.1f}%)")
    lines.append(f"  未通过数量：{len(failed)} ({len(failed)/len(results)*100:.1f}%)")

    if passed:
        lines.append(f"\n  【通过筛选的股票列表】")
        symbols = [f"{r.symbol}({r.name})" for r in passed]
        lines.append(f"  {', '.join(symbols)}")

        # 平均估值
        pe_values = [r.pe_ttm for r in passed if r.pe_ttm]
        pb_values = [r.pb for r in passed if r.pb]
        roe_values = [r.roe for r in passed if r.roe]

        avg_pe = sum(pe_values) / len(pe_values) if pe_values else 0
        avg_pb = sum(pb_values) / len(pb_values) if pb_values else 0
        avg_roe = sum(roe_values) / len(roe_values) if roe_values else 0

        lines.append(f"\n  【平均估值】")
        avg_parts = []
        if pe_values:
            avg_parts.append(f"平均 PE: {avg_pe:.1f}")
        if pb_values:
            avg_parts.append(f"平均 PB: {avg_pb:.1f}")
        if roe_values:
            avg_parts.append(f"平均 ROE: {avg_roe:.1f}%")
        if avg_parts:
            lines.append(f"  {'    '.join(avg_parts)}")
        else:
            lines.append(f"  暂无估值数据")

    lines.append(f"{'='*60}\n")
    return "\n".join(lines)


# ==================== CLI ====================

def main():
    parser = argparse.ArgumentParser(
        description="A 股股票筛选工具 (基于估值和财务指标)"
    )
    parser.add_argument("symbols", nargs="+",
                        help="股票代码列表 (如 600309 000001 600519)")
    parser.add_argument("--json", "-j", action="store_true",
                        help="JSON 格式输出")
    parser.add_argument("--show-failed", "-f", action="store_true",
                        help="显示未通过筛选的原因")
    parser.add_argument("--summary", "-s", action="store_true",
                        help="显示筛选摘要")

    # 筛选条件参数
    parser.add_argument("--max-pe", type=float, default=30.0,
                        help="市盈率上限 (默认：30)")
    parser.add_argument("--max-pb", type=float, default=5.0,
                        help="市净率上限 (默认：5)")
    parser.add_argument("--min-roe", type=float, default=8.0,
                        help="ROE 下限 (默认：8%%)")
    parser.add_argument("--max-debt-ratio", type=float, default=60.0,
                        help="资产负债率上限 (默认：60%%)")
    parser.add_argument("--min-gross-margin", type=float, default=0.0,
                        help="毛利率下限 (默认：0%%)")
    parser.add_argument("--min-net-margin", type=float, default=0.0,
                        help="净利率下限 (默认：0%%)")
    parser.add_argument("--min-revenue-growth", type=float, default=0.0,
                        help="营收增长率下限 (默认：0%%)")
    parser.add_argument("--min-market-cap", type=float, default=0.0,
                        help="最小市值 (单位：亿元，默认：0)")
    parser.add_argument("--max-market-cap", type=float, default=float('inf'),
                        help="最大市值 (单位：亿元，默认：inf)")

    args = parser.parse_args()

    # 构建筛选条件
    filters = {
        "max_pe": args.max_pe,
        "max_pb": args.max_pb,
        "min_roe": args.min_roe,
        "max_debt_ratio": args.max_debt_ratio,
        "min_gross_margin": args.min_gross_margin,
        "min_net_margin": args.min_net_margin,
        "min_revenue_growth": args.min_revenue_growth,
        "min_market_cap": args.min_market_cap * 100000000,  # 亿->元
        "max_market_cap": args.max_market_cap * 100000000,  # 亿->元
    }

    # 执行筛选
    results = screen_stocks(args.symbols, filters)

    # 输出结果
    if args.json:
        output = {
            "filters": {
                "max_pe": args.max_pe,
                "max_pb": args.max_pb,
                "min_roe": args.min_roe,
                "max_debt_ratio": args.max_debt_ratio,
                "min_gross_margin": args.min_gross_margin,
                "min_net_margin": args.min_net_margin,
                "min_revenue_growth": args.min_revenue_growth,
            },
            "total": len(results),
            "passed": sum(1 for r in results if r.passed),
            "failed": sum(1 for r in results if not r.passed),
            "results": [asdict(r) for r in results],
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        # 显示筛选条件
        print(f"\n筛选条件：PE<{args.max_pe}, PB<{args.max_pb}, ROE>{args.min_roe}%, 负债率<{args.max_debt_ratio}%")
        print(format_screen_table(results, show_failed=args.show_failed))
        if args.summary:
            print(screen_summary(results))


if __name__ == "__main__":
    main()
