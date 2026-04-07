#!/usr/bin/env python3
"""
A股数据接口 - 历史K线模块
===========================
数据源：新浪接口（唯一支持K线的外部接口）

支持的K线周期：
  - 5分钟、15分钟、30分钟、60分钟
  - 日线、周线、月线

Usage:
    python stock_kline.py 600309                    # 日线，默认30条
    python stock_kline.py 600309 --scale 5         # 5分钟线
    python stock_kline.py 600309 --datalen 60      # 日线60条
    python stock_kline.py 600309 --scale 240 -n 100 --ma  # 日线100条含均线
    python stock_kline.py 600309 --json            # JSON格式输出
"""
import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, asdict
from typing import Optional, List
from datetime import datetime


@dataclass
class KLine:
    """K线数据结构"""
    date: str  # 日期或时间
    open: float
    high: float
    low: float
    close: float
    volume: int  # 成交量 (股)
    ma5_vol: Optional[int] = None  # 5日均量
    ma10_vol: Optional[int] = None  # 10日均量
    ma30_vol: Optional[int] = None  # 30日均量
    ma5_price: Optional[float] = None  # 5日均价
    ma10_price: Optional[float] = None  # 10日均价
    ma30_price: Optional[float] = None  # 30日均价


@dataclass
class KLineResult:
    """K线查询结果"""
    symbol: str
    scale: int
    datalen: int
    count: int
    start_date: str
    end_date: str
    klines: List[KLine]
    source: str = "sina"


def normalize_symbol(symbol: str) -> str:
    """标准化股票代码为6位"""
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


def fetch_kline_sina(symbol: str, scale: int = 240, datalen: int = 30, ma: bool = True) -> Optional[KLineResult]:
    """
    从新浪接口获取K线数据
    
    接口地址: https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData
    
    Args:
        symbol: 股票代码
        scale: K线周期
            - 5: 5分钟
            - 15: 15分钟
            - 30: 30分钟
            - 60: 60分钟
            - 240: 日线
            - 480: 周线
            - 960: 月线
        datalen: 返回数据条数 (最大1000)
        ma: 是否包含均线数据
    
    Returns:
        KLineResult 或 None
    """
    sym = normalize_symbol(symbol)
    prefix = get_exchange_prefix(symbol)
    code = f"{prefix}{sym}"
    
    ma_param = "yes" if ma else "no"
    
    try:
        url = f"https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData?symbol={code}&scale={scale}&ma={ma_param}&datalen={datalen}"
        
        cmd = ["curl", "-s", url]
        result = subprocess.run(cmd, capture_output=True, timeout=30)
        
        if not result.stdout:
            return None
        
        # 解析 JSONP 响应: /*...*/=([...]);
        text = result.stdout.decode('utf-8', errors='ignore')
        match = re.search(r'\[(.*)\]', text, re.DOTALL)
        
        if not match:
            return None
        
        json_str = f"[{match.group(1)}]"
        data = json.loads(json_str)
        
        if not data:
            return None
        
        # 解析K线数据
        klines = []
        for item in data:
            kline = KLine(
                date=item.get('day', ''),
                open=safe_float(item.get('open')),
                high=safe_float(item.get('high')),
                low=safe_float(item.get('low')),
                close=safe_float(item.get('close')),
                volume=safe_int(item.get('volume')),
                ma5_vol=safe_int(item.get('ma_volume5'), None),
                ma10_vol=safe_int(item.get('ma_volume10'), None),
                ma30_vol=safe_int(item.get('ma_volume30'), None),
                ma5_price=safe_float(item.get('ma_price5'), None),
                ma10_price=safe_float(item.get('ma_price10'), None),
                ma30_price=safe_float(item.get('ma_price30'), None),
            )
            klines.append(kline)
        
        return KLineResult(
            symbol=sym,
            scale=scale,
            datalen=datalen,
            count=len(klines),
            start_date=klines[0].date if klines else "",
            end_date=klines[-1].date if klines else "",
            klines=klines,
            source="sina"
        )
        
    except subprocess.TimeoutExpired:
        print("错误: 请求超时", file=sys.stderr)
        return None
    except json.JSONDecodeError as e:
        print(f"错误: JSON解析失败 - {e}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return None


def get_kline(symbol: str, scale: int = 240, datalen: int = 30, ma: bool = True) -> Optional[KLineResult]:
    """
    获取K线数据
    
    Args:
        symbol: 股票代码
        scale: K线周期 (5/15/30/60/240/480/960)
        datalen: 返回数据条数
        ma: 是否包含均线
    
    Returns:
        KLineResult 或 None
    """
    return fetch_kline_sina(symbol, scale, datalen, ma)


def format_kline_table(result: KLineResult, show_ma: bool = True) -> str:
    """格式化输出K线表格"""
    scale_names = {
        5: "5分钟",
        15: "15分钟",
        30: "30分钟",
        60: "60分钟",
        240: "日线",
        480: "周线",
        960: "月线",
    }
    
    lines = []
    lines.append(f"\n{'='*80}")
    lines.append(f"  {result.symbol} - {scale_names.get(result.scale, '未知')} K线")
    lines.append(f"  数据范围: {result.start_date} ~ {result.end_date}    共 {result.count} 条")
    lines.append(f"{'='*80}")
    
    if show_ma and result.klines[0].ma5_vol:
        # 包含均线的格式
        lines.append(f"  {'日期':<12} {'开盘':>8} {'最高':>8} {'最低':>8} {'收盘':>8} {'成交量(手)':>12} {'MA5均量':>12}")
        lines.append(f"  {'-'*80}")
        for k in result.klines:  # 显示全部数据
            vol_hand = k.volume // 100
            ma5_str = f"{k.ma5_vol//100:,}" if k.ma5_vol else "-"
            lines.append(f"  {k.date:<12} {k.open:>8.2f} {k.high:>8.2f} {k.low:>8.2f} {k.close:>8.2f} {vol_hand:>12,} {ma5_str:>12}")
    else:
        # 简洁格式
        lines.append(f"  {'日期':<12} {'开盘':>8} {'最高':>8} {'最低':>8} {'收盘':>8} {'成交量(手)':>12}")
        lines.append(f"  {'-'*60}")
        for k in result.klines:  # 显示全部数据
            vol_hand = k.volume // 100
            lines.append(f"  {k.date:<12} {k.open:>8.2f} {k.high:>8.2f} {k.low:>8.2f} {k.close:>8.2f} {vol_hand:>12,}")
    
    lines.append(f"{'='*80}\n")
    
    return "\n".join(lines)


def analyze_volume(result: KLineResult) -> dict:
    """
    分析K线量能
    
    Returns:
        {
            'avg_volume': 平均成交量,
            'max_volume': 最大成交量,
            'min_volume': 最小成交量,
            'volume_spike_days': 放量天数,
            'volume_shrink_days': 缩量天数,
        }
    """
    if not result.klines:
        return {}
    
    volumes = [k.volume for k in result.klines]
    avg_volume = sum(volumes) / len(volumes)
    max_volume = max(volumes)
    min_volume = min(volumes)
    
    # 放量/缩量判断
    volume_spike_days = sum(1 for v in volumes if v > avg_volume * 1.5)
    volume_shrink_days = sum(1 for v in volumes if v < avg_volume * 0.5)
    
    return {
        'avg_volume': int(avg_volume),
        'max_volume': max_volume,
        'min_volume': min_volume,
        'volume_spike_days': volume_spike_days,
        'volume_shrink_days': volume_shrink_days,
    }


def format_analysis(result: KLineResult) -> str:
    """格式化输出量能分析"""
    analysis = analyze_volume(result)
    
    if not analysis:
        return ""
    
    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"  量能分析")
    lines.append(f"{'='*60}")
    lines.append(f"  平均成交量: {analysis['avg_volume']:,} 股 ({analysis['avg_volume']//100:,} 手)")
    lines.append(f"  最大成交量: {analysis['max_volume']:,} 股 ({analysis['max_volume']//100:,} 手)")
    lines.append(f"  最小成交量: {analysis['min_volume']:,} 股 ({analysis['min_volume']//100:,} 手)")
    lines.append(f"  放量天数: {analysis['volume_spike_days']} 天 (量比>1.5)")
    lines.append(f"  缩量天数: {analysis['volume_shrink_days']} 天 (量比<0.5)")
    lines.append(f"{'='*60}\n")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="A股历史K线接口")
    parser.add_argument("symbol", help="股票代码 (如 600309)")
    parser.add_argument("--scale", "-s", type=int, default=240,
                        choices=[5, 15, 30, 60, 240],
                        help="K线周期: 5/15/30/60/240(日线)")
    parser.add_argument("--datalen", "-n", type=int, default=30,
                        help="返回数据条数 (默认30，最大1000)")
    parser.add_argument("--no-ma", action="store_true",
                        help="不包含均线数据")
    parser.add_argument("--json", "-j", action="store_true",
                        help="JSON格式输出")
    parser.add_argument("--analyze", "-a", action="store_true",
                        help="显示量能分析")
    
    args = parser.parse_args()
    
    result = get_kline(args.symbol, args.scale, args.datalen, ma=not args.no_ma)
    
    if not result:
        print(f"错误: 无法获取 {args.symbol} 的K线数据")
        sys.exit(1)
    
    if args.json:
        output = asdict(result)
        output['klines'] = [asdict(k) for k in result.klines]
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        print(format_kline_table(result, show_ma=not args.no_ma))
        if args.analyze:
            print(format_analysis(result))


if __name__ == "__main__":
    main()