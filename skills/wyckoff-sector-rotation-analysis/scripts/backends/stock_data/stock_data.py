#!/usr/bin/env python3
"""
A股数据接口 - 统一入口
==========================
获取A股行情数据：实时行情、K线、盘口、估值、财务、宏观、筛选

数据源：
  - 新浪接口：实时行情、K线、盘口
  - 腾讯接口：估值指标
  - AKShare：财务数据、宏观数据

Usage:
    python stock_data.py quote 600309                    # 获取实时行情
    python stock_data.py kline 600309 --scale 240        # 获取日线K线
    python stock_data.py orderbook 600309 --analyze     # 获取盘口分析
    python stock_data.py valuation 600309               # 获取估值指标
    python stock_data.py financial 600309               # 获取财务数据
    python stock_data.py macro --pmi                    # 获取PMI数据
    python stock_data.py screen 600309 000001           # 股票筛选
"""
import argparse
import subprocess
import sys
import os

# 脚本目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def run_script(script_name, args):
    """运行指定脚本"""
    script_path = os.path.join(SCRIPT_DIR, f"{script_name}.py")
    cmd = [sys.executable, script_path] + args
    result = subprocess.run(cmd)
    return result.returncode


def main():
    parser = argparse.ArgumentParser(
        description="A股数据接口 - 统一入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
子命令:
  quote       获取实时行情 (stock_quote.py)
  kline       获取历史K线 (stock_kline.py)
  orderbook   获取盘口数据 (stock_orderbook.py)
  valuation   获取估值指标 (stock_valuation.py)
  financial   获取财务数据 (stock_financial.py)
  macro       获取宏观数据 (stock_macro.py)
  screen      股票筛选 (stock_screen.py)

示例:
  %(prog)s quote 600309                       # 获取实时行情
  %(prog)s quote 600309 000001 --json        # 批量获取，JSON格式
  %(prog)s kline 600309 --scale 5 -n 100     # 5分钟K线，100条
  %(prog)s orderbook 600309 --analyze        # 盘口分析
  %(prog)s valuation 600309 000001            # 批量获取估值
  %(prog)s financial 600309 --report         # 获取财务报表
  %(prog)s macro --pmi --json                 # PMI数据，JSON格式
  %(prog)s screen 600309 000001 --max-pe 20  # 筛选PE<20的股票
        """
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # ========== quote 子命令 ==========
    quote_parser = subparsers.add_parser("quote", help="获取实时行情")
    quote_parser.add_argument("symbols", nargs="+", help="股票代码 (如 600309)")
    quote_parser.add_argument("--orderbook", "-o", action="store_true", help="显示五档盘口")
    quote_parser.add_argument("--valuation", "-v", action="store_true", help="显示估值指标 (PE/PB)")
    quote_parser.add_argument("--json", "-j", action="store_true", help="JSON格式输出")

    # ========== kline 子命令 ==========
    kline_parser = subparsers.add_parser("kline", help="获取历史K线")
    kline_parser.add_argument("symbol", help="股票代码 (如 600309)")
    kline_parser.add_argument("--scale", "-s", type=int, default=240,
                              choices=[5, 15, 30, 60, 240],
                              help="K线周期: 5/15/30/60/240(日线)")
    kline_parser.add_argument("--datalen", "-n", type=int, default=30,
                              help="返回数据条数 (默认30)")
    kline_parser.add_argument("--no-ma", action="store_true", help="不包含均线数据")
    kline_parser.add_argument("--json", "-j", action="store_true", help="JSON格式输出")
    kline_parser.add_argument("--analyze", "-a", action="store_true", help="显示量能分析")

    # ========== orderbook 子命令 ==========
    orderbook_parser = subparsers.add_parser("orderbook", help="获取盘口数据")
    orderbook_parser.add_argument("symbol", help="股票代码 (如 600309)")
    orderbook_parser.add_argument("--analyze", "-a", action="store_true", help="显示盘口分析")
    orderbook_parser.add_argument("--depth", "-d", action="store_true", help="显示盘口深度图")
    orderbook_parser.add_argument("--json", "-j", action="store_true", help="JSON格式输出")

    # ========== valuation 子命令 ==========
    valuation_parser = subparsers.add_parser("valuation", help="获取估值指标")
    valuation_parser.add_argument("symbols", nargs="+", help="股票代码 (如 600309)")
    valuation_parser.add_argument("--json", "-j", action="store_true", help="JSON格式输出")

    # ========== financial 子命令 ==========
    financial_parser = subparsers.add_parser("financial", help="获取财务数据")
    financial_parser.add_argument("symbols", nargs="+", help="股票代码 (如 600309)")
    financial_parser.add_argument("--json", "-j", action="store_true", help="JSON格式输出")
    financial_parser.add_argument("--report", "-r", action="store_true", help="获取财务报表")
    financial_parser.add_argument("--dividend", "-d", action="store_true", help="获取分红信息")
    financial_parser.add_argument("--report-type", choices=["balance", "profit", "cashflow"],
                                  default="balance", help="报表类型 (默认：资产负债表)")

    # ========== macro 子命令 ==========
    macro_parser = subparsers.add_parser("macro", help="获取宏观数据")
    macro_parser.add_argument("--dashboard", action="store_true", help="完整仪表板")
    macro_parser.add_argument("--rates", action="store_true", help="利率数据 (LPR, Shibor)")
    macro_parser.add_argument("--inflation", action="store_true", help="CPI/PPI数据")
    macro_parser.add_argument("--pmi", action="store_true", help="PMI数据")
    macro_parser.add_argument("--social-financing", action="store_true", help="社融+M2数据")
    macro_parser.add_argument("--cycle", action="store_true", help="经济周期评估")
    macro_parser.add_argument("--json", "-j", action="store_true", help="JSON格式输出")

    # ========== screen 子命令 ==========
    screen_parser = subparsers.add_parser("screen", help="股票筛选")
    screen_parser.add_argument("symbols", nargs="+", help="股票代码列表")
    screen_parser.add_argument("--json", "-j", action="store_true", help="JSON格式输出")
    screen_parser.add_argument("--show-failed", "-f", action="store_true", help="显示未通过原因")
    screen_parser.add_argument("--summary", "-s", action="store_true", help="显示筛选摘要")
    screen_parser.add_argument("--max-pe", type=float, default=30.0, help="市盈率上限 (默认：30)")
    screen_parser.add_argument("--max-pb", type=float, default=5.0, help="市净率上限 (默认：5)")
    screen_parser.add_argument("--min-roe", type=float, default=8.0, help="ROE下限 (默认：8%%)")
    screen_parser.add_argument("--max-debt-ratio", type=float, default=60.0, help="资产负债率上限 (默认：60%%)")
    screen_parser.add_argument("--min-gross-margin", type=float, default=0.0, help="毛利率下限 (默认：0%%)")
    screen_parser.add_argument("--min-net-margin", type=float, default=0.0, help="净利率下限 (默认：0%%)")
    screen_parser.add_argument("--min-revenue-growth", type=float, default=0.0, help="营收增长率下限 (默认：0%%)")
    screen_parser.add_argument("--min-market-cap", type=float, default=0.0, help="最小市值 (亿元)")
    screen_parser.add_argument("--max-market-cap", type=float, default=float('inf'), help="最大市值 (亿元)")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # 构建参数并调用对应脚本
    if args.command == "quote":
        cmd_args = args.symbols.copy()
        if args.orderbook:
            cmd_args.append("--orderbook")
        if args.valuation:
            cmd_args.append("--valuation")
        if args.json:
            cmd_args.append("--json")
        return run_script("stock_quote", cmd_args)

    elif args.command == "kline":
        cmd_args = [args.symbol]
        cmd_args.extend(["--scale", str(args.scale)])
        cmd_args.extend(["--datalen", str(args.datalen)])
        if args.no_ma:
            cmd_args.append("--no-ma")
        if args.json:
            cmd_args.append("--json")
        if args.analyze:
            cmd_args.append("--analyze")
        return run_script("stock_kline", cmd_args)

    elif args.command == "orderbook":
        cmd_args = [args.symbol]
        if args.analyze:
            cmd_args.append("--analyze")
        if args.depth:
            cmd_args.append("--depth")
        if args.json:
            cmd_args.append("--json")
        return run_script("stock_orderbook", cmd_args)

    elif args.command == "valuation":
        cmd_args = args.symbols.copy()
        if args.json:
            cmd_args.append("--json")
        return run_script("stock_valuation", cmd_args)

    elif args.command == "financial":
        cmd_args = args.symbols.copy()
        if args.json:
            cmd_args.append("--json")
        if args.report:
            cmd_args.append("--report")
            cmd_args.extend(["--report-type", args.report_type])
        if args.dividend:
            cmd_args.append("--dividend")
        return run_script("stock_financial", cmd_args)

    elif args.command == "macro":
        cmd_args = []
        if args.dashboard:
            cmd_args.append("--dashboard")
        if args.rates:
            cmd_args.append("--rates")
        if args.inflation:
            cmd_args.append("--inflation")
        if args.pmi:
            cmd_args.append("--pmi")
        if args.social_financing:
            cmd_args.append("--social-financing")
        if args.cycle:
            cmd_args.append("--cycle")
        if args.json:
            cmd_args.append("--json")
        return run_script("stock_macro", cmd_args)

    elif args.command == "screen":
        cmd_args = args.symbols.copy()
        if args.json:
            cmd_args.append("--json")
        if args.show_failed:
            cmd_args.append("--show-failed")
        if args.summary:
            cmd_args.append("--summary")
        cmd_args.extend(["--max-pe", str(args.max_pe)])
        cmd_args.extend(["--max-pb", str(args.max_pb)])
        cmd_args.extend(["--min-roe", str(args.min_roe)])
        cmd_args.extend(["--max-debt-ratio", str(args.max_debt_ratio)])
        cmd_args.extend(["--min-gross-margin", str(args.min_gross_margin)])
        cmd_args.extend(["--min-net-margin", str(args.min_net_margin)])
        cmd_args.extend(["--min-revenue-growth", str(args.min_revenue_growth)])
        cmd_args.extend(["--min-market-cap", str(args.min_market_cap)])
        if args.max_market_cap != float('inf'):
            cmd_args.extend(["--max-market-cap", str(args.max_market_cap)])
        return run_script("stock_screen", cmd_args)

    return 0


if __name__ == "__main__":
    sys.exit(main())