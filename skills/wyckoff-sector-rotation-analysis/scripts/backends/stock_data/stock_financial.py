#!/usr/bin/env python3
"""
A 股数据接口 - 财务分析模块
==========================
获取 A 股财务指标、财务报表、分红数据。

数据源：AKShare

Usage:
    python stock_financial.py 600309              # 获取财务指标
    python stock_financial.py 600309 --json       # JSON 格式输出
    python stock_financial.py 600309 --report     # 获取财务报表
    python stock_financial.py 600309 000001       # 批量获取
"""
import argparse
import json
import sys
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict
from datetime import datetime


@dataclass
class FinancialMetrics:
    """财务指标数据结构"""
    symbol: str                    # 股票代码
    name: str                      # 股票名称
    roe: Optional[float]           # 净资产收益率 (%)
    gross_margin: Optional[float]  # 毛利率 (%)
    net_margin: Optional[float]    # 净利率 (%)
    roa: Optional[float]           # 总资产报酬率 (%)
    debt_ratio: Optional[float]    # 资产负债率 (%)
    current_ratio: Optional[float] # 流动比率
    quick_ratio: Optional[float]   # 速动比率
    eps: Optional[float]           # 每股收益 (元)
    bvps: Optional[float]          # 每股净资产 (元)
    revenue: Optional[float]       # 营业收入 (元)
    net_profit: Optional[float]    # 净利润 (元)
    operating_cash_flow: Optional[float]  # 经营现金流 (元)
    report_date: str               # 报告期
    source: str                    # 数据来源


@dataclass
class FinancialReport:
    """财务报表数据结构"""
    symbol: str                    # 股票代码
    name: str                      # 股票名称
    report_type: str               # 报表类型 (balance/profit/cashflow)
    report_date: str               # 报告期
    data: Dict                     # 报表数据
    source: str                    # 数据来源


@dataclass
class DividendInfo:
    """分红信息数据结构"""
    symbol: str                    # 股票代码
    name: str                      # 股票名称
    dividend_year: str             # 分红年度
    dividend_plan: str             # 分红方案
    ex_dividend_date: str          # 除权除息日
    dividend_amount: float         # 每股分红 (元)
    source: str                    # 数据来源


def normalize_symbol(symbol: str) -> str:
    """标准化股票代码为 6 位"""
    sym = symbol.strip().replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    return sym.zfill(6)


def safe_float(val, default=None, unit_multiplier: Optional[float] = None) -> Optional[float]:
    """安全转换为浮点数，支持单位转换

    Args:
        val: 要转换的值
        default: 转换失败时的默认值
        unit_multiplier: 单位转换倍数（如万->元 用 10000）
    """
    if val is None or val == "" or val == "null" or val == "-" or val == "False" or val is False:
        return default

    # 处理百分比字符串，如 "9.82%"
    if isinstance(val, str):
        val = val.strip()
        percentage = False
        multiplier = 1.0

        if val.endswith("%"):
            percentage = True
            val = val[:-1]
        elif val.endswith("亿"):
            multiplier = 100000000.0
            val = val[:-1]
        elif val.endswith("万"):
            multiplier = 10000.0
            val = val[:-1]

        # 如果传入了 unit_multiplier，优先使用传入的
        if unit_multiplier is not None:
            multiplier = unit_multiplier

        try:
            result = float(val) * multiplier
            if percentage:
                result = result  # 保持百分比值本身
            return result
        except (ValueError, TypeError):
            return default

    try:
        if unit_multiplier is not None:
            return float(val) * unit_multiplier
        return float(val)
    except (ValueError, TypeError):
        return default


def safe_str(val, default="") -> str:
    """安全转换为字符串"""
    if val is None or val == "null" or val == "-":
        return default
    return str(val)


def fetch_financial_metrics(symbol: str) -> Optional[FinancialMetrics]:
    """
    从 AKShare 获取财务指标

    使用函数:
    - stock_financial_abstract_ths(): 获取财务指标 (按报告期)
    - stock_individual_info_em(): 获取基本信息
    """
    try:
        import akshare as ak

        sym = normalize_symbol(symbol)

        # 获取基本信息 (含股票名称)
        try:
            df_info = ak.stock_individual_info_em(symbol=sym)
            info_dict = {}
            for _, row in df_info.iterrows():
                key = str(row.iloc[0])
                value = row.iloc[1]
                info_dict[key] = value
            name = info_dict.get("股票简称", "")
        except Exception:
            name = ""

        # 获取财务指标 (同花顺数据源 - 按报告期)
        try:
            df_fin = ak.stock_financial_abstract_ths(symbol=sym, indicator="按报告期")

            if df_fin is None or len(df_fin) == 0:
                # 尝试按年度获取
                df_fin = ak.stock_financial_abstract_ths(symbol=sym, indicator="按年度")

            if df_fin is None or len(df_fin) == 0:
                return FinancialMetrics(
                    symbol=sym,
                    name=name,
                    roe=None,
                    gross_margin=None,
                    net_margin=None,
                    roa=None,
                    debt_ratio=None,
                    current_ratio=None,
                    quick_ratio=None,
                    eps=None,
                    bvps=None,
                    revenue=None,
                    net_profit=None,
                    operating_cash_flow=None,
                    report_date="",
                    source="akshare"
                )

            # 获取最新一期数据（数据按时间正序排列，最新的在最后）
            latest = df_fin.iloc[-1]

            # 报告期
            report_date = str(latest.get("报告期", ""))

            # 盈利能力指标
            roe = safe_float(latest.get("净资产收益率"))
            gross_margin = safe_float(latest.get("销售毛利率"))
            net_margin = safe_float(latest.get("销售净利率"))
            roa = safe_float(latest.get("总资产报酬率"))

            # 杠杆指标
            debt_ratio = safe_float(latest.get("资产负债率"))
            current_ratio = safe_float(latest.get("流动比率"))
            quick_ratio = safe_float(latest.get("速动比率"))

            # 每股指标
            eps = safe_float(latest.get("基本每股收益"))
            bvps = safe_float(latest.get("每股净资产"))

            # 规模指标 (数据本身带单位，如 "1.40 亿", "1375.30 万")
            revenue = safe_float(latest.get("营业总收入"))

            net_profit = safe_float(latest.get("净利润"))

            operating_cash_flow = safe_float(latest.get("每股经营现金流"))

            return FinancialMetrics(
                symbol=sym,
                name=name,
                roe=roe,
                gross_margin=gross_margin,
                net_margin=net_margin,
                roa=roa,
                debt_ratio=debt_ratio,
                current_ratio=current_ratio,
                quick_ratio=quick_ratio,
                eps=eps,
                bvps=bvps,
                revenue=revenue,
                net_profit=net_profit,
                operating_cash_flow=operating_cash_flow,
                report_date=report_date,
                source="akshare"
            )

        except Exception as e:
            print(f"获取财务指标失败：{e}", file=sys.stderr)
            return FinancialMetrics(
                symbol=sym,
                name=name,
                roe=None,
                gross_margin=None,
                net_margin=None,
                roa=None,
                debt_ratio=None,
                current_ratio=None,
                quick_ratio=None,
                eps=None,
                bvps=None,
                revenue=None,
                net_profit=None,
                operating_cash_flow=None,
                report_date="",
                source="akshare"
            )

    except Exception as e:
        print(f"AKShare 错误：{e}", file=sys.stderr)
        return None


def fetch_financial_report(symbol: str, report_type: str = "balance") -> Optional[FinancialReport]:
    """
    从 AKShare 获取财务报表

    参数:
        report_type: 报表类型
            - "balance": 资产负债表
            - "profit": 利润表
            - "cashflow": 现金流量表

    使用函数:
        - stock_balance_sheet_by_report_em(): 资产负债表
        - stock_profit_sheet_by_report_em(): 利润表
        - stock_cash_flow_sheet_by_report_em(): 现金流量表
    """
    try:
        import akshare as ak

        sym = normalize_symbol(symbol)

        # 确定股票市场前缀
        if sym.startswith("6"):
            symbol_with_prefix = f"SH{sym}"
        elif sym.startswith("0") or sym.startswith("3"):
            symbol_with_prefix = f"SZ{sym}"
        else:
            symbol_with_prefix = f"BJ{sym}"

        # 获取基本信息 (含股票名称)
        try:
            df_info = ak.stock_individual_info_em(symbol=sym)
            info_dict = {}
            for _, row in df_info.iterrows():
                key = str(row.iloc[0])
                value = row.iloc[1]
                info_dict[key] = value
            name = info_dict.get("股票简称", "")
        except Exception:
            name = ""

        # 根据报表类型获取数据
        df = None
        report_type_name = ""

        if report_type == "balance":
            try:
                df = ak.stock_balance_sheet_by_report_em(symbol=symbol_with_prefix)
                report_type_name = "资产负债表"
            except Exception:
                pass
        elif report_type == "profit":
            try:
                df = ak.stock_profit_sheet_by_report_em(symbol=symbol_with_prefix)
                report_type_name = "利润表"
            except Exception:
                pass
        elif report_type == "cashflow":
            try:
                df = ak.stock_cash_flow_sheet_by_report_em(symbol=symbol_with_prefix)
                report_type_name = "现金流量表"
            except Exception:
                pass

        if df is None or len(df) == 0:
            return FinancialReport(
                symbol=sym,
                name=name,
                report_type=report_type,
                report_date="",
                data={},
                source="akshare"
            )

        # 获取最新一期数据
        latest = df.iloc[0]
        report_date = str(latest.get("REPORT_DATE", ""))

        # 转换为字典（排除一些标准列）
        data_dict = {}
        exclude_cols = {"SECUCODE", "SECURITY_CODE", "SECURITY_NAME_ABBR", "ORG_CODE",
                        "ORG_TYPE", "REPORT_DATE", "REPORT_TYPE", "SECURITY_TYPE_CODE",
                        "NOTICE_DATE", "REPORT_DATE_NAME", "COMPANY_NAME"}
        for col in df.columns:
            if col not in exclude_cols:
                value = latest.get(col)
                if value is not None and str(value) != "nan":
                    data_dict[col] = value

        return FinancialReport(
            symbol=sym,
            name=name,
            report_type=report_type_name,
            report_date=report_date,
            data=data_dict,
            source="akshare"
        )

    except Exception as e:
        print(f"AKShare 错误：{e}", file=sys.stderr)
        return None


def fetch_dividend_info(symbol: str) -> List[DividendInfo]:
    """
    从 AKShare 获取分红信息

    使用函数:
    - stock_history_dividend_detail(): 获取分红数据
    """
    try:
        import akshare as ak
        import re

        sym = normalize_symbol(symbol)

        # 获取基本信息 (含股票名称)
        try:
            df_info = ak.stock_individual_info_em(symbol=sym)
            info_dict = {}
            for _, row in df_info.iterrows():
                key = str(row.iloc[0])
                value = row.iloc[1]
                info_dict[key] = value
            name = info_dict.get("股票简称", "")
        except Exception:
            name = ""

        # 获取分红数据
        df = ak.stock_history_dividend_detail(symbol=sym)

        if df is None or len(df) == 0:
            return []

        dividends = []
        for _, row in df.iterrows():
            # 从公告日期提取年度
            notice_date = str(row.get("公告日期", ""))
            dividend_year = notice_date[:4] if len(notice_date) >= 4 else ""

            ex_dividend_date = str(row.get("除权除息日", ""))
            if ex_dividend_date == "NaT" or ex_dividend_date == "nan":
                ex_dividend_date = ""

            # 构建分红方案
            send = safe_float(row.get("送股"), 0)
            transfer = safe_float(row.get("转增"), 0)
            cash = safe_float(row.get("派息"), 0)

            dividend_plan = ""
            if cash > 0:
                dividend_plan = f"10 派{cash}元 (含税)"
            if send > 0:
                dividend_plan += f" 10 送{send}"
            if transfer > 0:
                dividend_plan += f" 10 转{transfer}"

            dividend_amount = cash / 10 if cash > 0 else None  # 转换为每股分红

            dividends.append(DividendInfo(
                symbol=sym,
                name=name,
                dividend_year=dividend_year,
                dividend_plan=dividend_plan,
                ex_dividend_date=ex_dividend_date,
                dividend_amount=dividend_amount,
                source="akshare"
            ))

        return dividends

    except Exception as e:
        print(f"AKShare 错误：{e}", file=sys.stderr)
        return []


def get_financial_metrics(symbol: str) -> Optional[FinancialMetrics]:
    """
    获取财务指标 (带重试机制)
    """
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            metrics = fetch_financial_metrics(symbol)
            if metrics:
                return metrics
        except Exception as e:
            if attempt < max_attempts - 1:
                import time
                time.sleep(1.0 * (2 ** attempt))  # 指数退避
            else:
                print(f"获取财务指标失败：{e}", file=sys.stderr)
    return None


def get_financial_report(symbol: str, report_type: str = "balance") -> Optional[FinancialReport]:
    """
    获取财务报表 (带重试机制)
    """
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            report = fetch_financial_report(symbol, report_type)
            if report:
                return report
        except Exception as e:
            if attempt < max_attempts - 1:
                import time
                time.sleep(1.0 * (2 ** attempt))
            else:
                print(f"获取财务报表失败：{e}", file=sys.stderr)
    return None


def get_dividend_info(symbol: str) -> List[DividendInfo]:
    """
    获取分红信息 (带重试机制)
    """
    max_attempts = 3
    for attempt in range(max_attempts):
        try:
            dividends = fetch_dividend_info(symbol)
            if dividends:
                return dividends
        except Exception as e:
            if attempt < max_attempts - 1:
                import time
                time.sleep(1.0 * (2 ** attempt))
            else:
                print(f"获取分红信息失败：{e}", file=sys.stderr)
    return []


def format_financial_table(metrics: FinancialMetrics) -> str:
    """格式化输出财务指标表格"""
    lines = []
    lines.append(f"\n{'='*50}")
    lines.append(f"  {metrics.name} ({metrics.symbol})")
    lines.append(f"  财务指标 - 报告期：{metrics.report_date}")
    lines.append(f"{'='*50}")

    # 盈利能力
    lines.append(f"\n  盈利能力")
    lines.append(f"  {'─'*40}")
    roe_str = f"ROE: {metrics.roe:.2f}%" if metrics.roe else "ROE: --"
    gross_str = f"毛利率：{metrics.gross_margin:.2f}%" if metrics.gross_margin else "毛利率：--"
    net_str = f"净利率：{metrics.net_margin:.2f}%" if metrics.net_margin else "净利率：--"
    roa_str = f"ROA: {metrics.roa:.2f}%" if metrics.roa else "ROA: --"
    lines.append(f"  {roe_str}    {gross_str}")
    lines.append(f"  {net_str}    {roa_str}")

    # 杠杆指标
    lines.append(f"\n  杠杆指标")
    lines.append(f"  {'─'*40}")
    debt_str = f"资产负债率：{metrics.debt_ratio:.2f}%" if metrics.debt_ratio else "资产负债率：--"
    current_str = f"流动比率：{metrics.current_ratio:.2f}" if metrics.current_ratio else "流动比率：--"
    quick_str = f"速动比率：{metrics.quick_ratio:.2f}" if metrics.quick_ratio else "速动比率：--"
    lines.append(f"  {debt_str}")
    lines.append(f"  {current_str}    {quick_str}")

    # 每股指标
    lines.append(f"\n  每股指标")
    lines.append(f"  {'─'*40}")
    eps_str = f"EPS: ¥{metrics.eps:.2f}" if metrics.eps else "EPS: --"
    bvps_str = f"BVPS: ¥{metrics.bvps:.2f}" if metrics.bvps else "BVPS: --"
    lines.append(f"  {eps_str}    {bvps_str}")

    # 规模指标
    lines.append(f"\n  规模指标")
    lines.append(f"  {'─'*40}")
    if metrics.revenue:
        lines.append(f"  营业收入：{metrics.revenue / 100000000:.2f} 亿元")
    else:
        lines.append(f"  营业收入：--")
    if metrics.net_profit:
        lines.append(f"  净利润：{metrics.net_profit / 100000000:.2f} 亿元")
    else:
        lines.append(f"  净利润：--")
    if metrics.operating_cash_flow:
        lines.append(f"  经营现金流：{metrics.operating_cash_flow / 100000000:.2f} 亿元")
    else:
        lines.append(f"  经营现金流：--")

    lines.append(f"\n{'='*50}")
    lines.append(f"  数据来源：{metrics.source}")
    lines.append(f"{'='*50}\n")

    return "\n".join(lines)


def format_dividend_table(dividends: List[DividendInfo]) -> str:
    """格式化输出分红表格"""
    if not dividends:
        return "\n暂无分红数据\n"

    lines = []
    lines.append(f"\n{'='*60}")
    if dividends:
        lines.append(f"  {dividends[0].name} ({dividends[0].symbol})")
    lines.append(f"  分红信息")
    lines.append(f"{'='*60}")
    lines.append(f"  {'年度':<12} {'每股分红':<12} {'除权除息日':<15} {'分配方案':<20}")
    lines.append(f"  {'─'*55}")

    for div in dividends[:10]:  # 只显示最近 10 条
        amount_str = f"¥{div.dividend_amount:.2f}" if div.dividend_amount else "--"
        lines.append(f"  {div.dividend_year:<12} {amount_str:<12} {div.ex_dividend_date:<15} {div.dividend_plan[:20]}")

    lines.append(f"{'='*60}\n")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="A 股财务分析接口")
    parser.add_argument("symbols", nargs="+", help="股票代码 (如 600309)")
    parser.add_argument("--json", "-j", action="store_true", help="JSON 格式输出")
    parser.add_argument("--report", "-r", action="store_true", help="获取财务报表")
    parser.add_argument("--dividend", "-d", action="store_true", help="获取分红信息")
    parser.add_argument("--report-type", choices=["balance", "profit", "cashflow"],
                        default="balance", help="报表类型 (默认：资产负债表)")

    args = parser.parse_args()

    if args.report:
        # 获取财务报表
        if len(args.symbols) == 1:
            report = get_financial_report(args.symbols[0], args.report_type)
            if report:
                if args.json:
                    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
                else:
                    print(f"\n{'='*50}")
                    print(f"  {report.name} ({report.symbol})")
                    print(f"  {report.report_type} - 报告期：{report.report_date}")
                    print(f"{'='*50}")
                    for key, value in report.data.items():
                        print(f"  {key}: {value}")
                    print(f"{'='*50}\n")
            else:
                print(f"错误：无法获取 {args.symbols[0]} 的财务报表")
                sys.exit(1)
    elif args.dividend:
        # 获取分红信息
        if len(args.symbols) == 1:
            dividends = get_dividend_info(args.symbols[0])
            if dividends:
                if args.json:
                    print(json.dumps([asdict(d) for d in dividends], indent=2, ensure_ascii=False))
                else:
                    print(format_dividend_table(dividends))
            else:
                print(f"错误：无法获取 {args.symbols[0]} 的分红信息")
                sys.exit(1)
    else:
        # 获取财务指标
        if len(args.symbols) == 1:
            metrics = get_financial_metrics(args.symbols[0])
            if metrics:
                if args.json:
                    print(json.dumps(asdict(metrics), indent=2, ensure_ascii=False))
                else:
                    print(format_financial_table(metrics))
            else:
                print(f"错误：无法获取 {args.symbols[0]} 的财务数据")
                sys.exit(1)
        else:
            metrics_list = []
            for sym in args.symbols:
                metrics = get_financial_metrics(sym)
                if metrics:
                    metrics_list.append(metrics)
                else:
                    metrics_list.append({"symbol": normalize_symbol(sym), "error": "获取失败"})

            if args.json:
                output = [asdict(m) if isinstance(m, FinancialMetrics) else m for m in metrics_list]
                print(json.dumps(output, indent=2, ensure_ascii=False))
            else:
                for m in metrics_list:
                    if isinstance(m, FinancialMetrics):
                        print(format_financial_table(m))
                    else:
                        print(f"\n错误：{m.get('symbol')} - {m.get('error')}\n")


if __name__ == "__main__":
    main()
