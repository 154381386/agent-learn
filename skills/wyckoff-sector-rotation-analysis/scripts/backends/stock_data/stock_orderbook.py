#!/usr/bin/env python3
"""
A股数据接口 - 盘口数据模块
==========================
获取和分析A股五档买卖盘口数据

功能：
  - 五档买卖盘口
  - 盘口深度分析
  - 买卖力量对比

Usage:
    python stock_orderbook.py 600309                    # 获取盘口数据
    python stock_orderbook.py 600309 --analyze         # 盘口分析
    python stock_orderbook.py 600309 --depth           # 盘口深度
    python stock_orderbook.py 600309 --json            # JSON输出
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
class OrderLevel:
    """单个价位档位"""
    volume: int  # 挂单量 (股)
    price: float  # 价格 (元)


@dataclass
class OrderBook:
    """盘口数据结构"""
    symbol: str  # 股票代码
    name: str  # 股票名称
    price: float  # 当前价
    prev_close: float  # 昨收价
    open: float  # 今开价
    high: float  # 最高价
    low: float  # 最低价
    volume: int  # 成交量 (股)
    amount: float  # 成交额 (元)
    timestamp: str  # 时间戳
    
    # 五档买盘
    bid1: OrderLevel
    bid2: OrderLevel
    bid3: OrderLevel
    bid4: OrderLevel
    bid5: OrderLevel
    
    # 五档卖盘
    ask1: OrderLevel
    ask2: OrderLevel
    ask3: OrderLevel
    ask4: OrderLevel
    ask5: OrderLevel
    
    source: str = "sina"  # 数据来源


@dataclass
class OrderBookAnalysis:
    """盘口分析结果"""
    symbol: str
    name: str
    
    # 买卖力量
    total_bid_volume: int  # 买盘总挂单 (股)
    total_ask_volume: int  # 卖盘总挂单 (股)
    bid_ask_ratio: float  # 买卖比 (买盘/卖盘)
    
    # 价格位置
    bid_price_range: float  # 买盘价格区间 (元)
    ask_price_range: float  # 卖盘价格区间 (元)
    price_spread: float  # 买卖价差 (元)
    price_spread_pct: float  # 买卖价差 (%)
    
    
    # 综合判断
    sentiment: str  # 市场情绪: bullish/bearish/neutral
    sentiment_score: float  # 情绪分数 (-100 到 100)


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


def fetch_orderbook_sina(symbol: str) -> Optional[OrderBook]:
    """
    从新浪接口获取盘口数据
    
    接口地址: http://hq.sinajs.cn/list={code}
    返回格式: var hq_str_sh600309="万华化学,89.69,89.71,...";
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
        
        # 解析基础字段（新浪接口字段顺序：[1]=开盘价，[2]=昨收价，[3]=当前价）
        name = fields[0]
        open_price = safe_float(fields[1])   # 今开价
        prev_close = safe_float(fields[2])    # 昨收价
        price = safe_float(fields[3])         # 当前价/最新价
        high = safe_float(fields[4])          # 最高价
        low = safe_float(fields[5])           # 最低价
        volume = safe_int(fields[8])          # 成交量
        amount = safe_float(fields[9])        # 成交额
        
        # 时间
        date_str = fields[30] if len(fields) > 30 else ""
        time_str = fields[31] if len(fields) > 31 else ""
        timestamp = f"{date_str} {time_str}"
        
        # 解析五档买盘 (字段10-19: 买一量,买一价,...买五量,买五价)
        bid1 = OrderLevel(volume=safe_int(fields[10]), price=safe_float(fields[11]))
        bid2 = OrderLevel(volume=safe_int(fields[12]), price=safe_float(fields[13]))
        bid3 = OrderLevel(volume=safe_int(fields[14]), price=safe_float(fields[15]))
        bid4 = OrderLevel(volume=safe_int(fields[16]), price=safe_float(fields[17]))
        bid5 = OrderLevel(volume=safe_int(fields[18]), price=safe_float(fields[19]))
        
        # 解析五档卖盘 (字段20-29: 卖一量,卖一价,...卖五量,卖五价)
        ask1 = OrderLevel(volume=safe_int(fields[20]), price=safe_float(fields[21]))
        ask2 = OrderLevel(volume=safe_int(fields[22]), price=safe_float(fields[23]))
        ask3 = OrderLevel(volume=safe_int(fields[24]), price=safe_float(fields[25]))
        ask4 = OrderLevel(volume=safe_int(fields[26]), price=safe_float(fields[27]))
        ask5 = OrderLevel(volume=safe_int(fields[28]), price=safe_float(fields[29]))
        
        return OrderBook(
            symbol=sym,
            name=name,
            price=price,
            prev_close=prev_close,
            open=open_price,
            high=high,
            low=low,
            volume=volume,
            amount=amount,
            timestamp=timestamp,
            bid1=bid1, bid2=bid2, bid3=bid3, bid4=bid4, bid5=bid5,
            ask1=ask1, ask2=ask2, ask3=ask3, ask4=ask4, ask5=ask5,
            source="sina"
        )
        
    except subprocess.TimeoutExpired:
        return None
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        return None


def get_orderbook(symbol: str) -> Optional[OrderBook]:
    """获取盘口数据"""
    return fetch_orderbook_sina(symbol)


def analyze_orderbook(ob: OrderBook) -> OrderBookAnalysis:
    """
    分析盘口数据
    
    Returns:
        OrderBookAnalysis: 盘口分析结果
    """
    # 买盘总挂单
    total_bid = ob.bid1.volume + ob.bid2.volume + ob.bid3.volume + ob.bid4.volume + ob.bid5.volume
    
    # 卖盘总挂单
    total_ask = ob.ask1.volume + ob.ask2.volume + ob.ask3.volume + ob.ask4.volume + ob.ask5.volume
    
    # 买卖比
    bid_ask_ratio = total_bid / total_ask if total_ask > 0 else 0
    
    # 价格区间
    bid_price_range = ob.bid1.price - ob.bid5.price if ob.bid5.price > 0 else 0
    ask_price_range = ob.ask5.price - ob.ask1.price if ob.ask1.price > 0 else 0
    
    # 买卖价差
    price_spread = ob.ask1.price - ob.bid1.price if ob.ask1.price > 0 and ob.bid1.price > 0 else 0
    price_spread_pct = (price_spread / ob.bid1.price * 100) if ob.bid1.price > 0 else 0

    # 市场情绪判断
    sentiment_score = 0

    # 买卖比影响 (-50 到 50)
    if bid_ask_ratio > 2:
        sentiment_score += 50
    elif bid_ask_ratio > 1.5:
        sentiment_score += 35
    elif bid_ask_ratio > 1.2:
        sentiment_score += 20
    elif bid_ask_ratio < 0.5:
        sentiment_score -= 50
    elif bid_ask_ratio < 0.67:
        sentiment_score -= 35
    elif bid_ask_ratio < 0.8:
        sentiment_score -= 20

    # 价差影响 (-50 到 50)
    if price_spread_pct < 0.1:
        sentiment_score += 50
    elif price_spread_pct < 0.2:
        sentiment_score += 25
    elif price_spread_pct > 0.5:
        sentiment_score -= 50
    elif price_spread_pct > 0.3:
        sentiment_score -= 25

    # 情绪判断
    if sentiment_score >= 50:
        sentiment = "bullish"  # 看多
    elif sentiment_score <= -50:
        sentiment = "bearish"  # 看空
    else:
        sentiment = "neutral"  # 中性

    return OrderBookAnalysis(
        symbol=ob.symbol,
        name=ob.name,
        total_bid_volume=total_bid,
        total_ask_volume=total_ask,
        bid_ask_ratio=round(bid_ask_ratio, 2),
        bid_price_range=round(bid_price_range, 3),
        ask_price_range=round(ask_price_range, 3),
        price_spread=round(price_spread, 3),
        price_spread_pct=round(price_spread_pct, 3),
        sentiment=sentiment,
        sentiment_score=round(sentiment_score, 1)
    )


def format_orderbook(ob: OrderBook) -> str:
    """格式化输出盘口数据"""
    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"  {ob.name} ({ob.symbol}) - 盘口数据")
    lines.append(f"{'='*60}")
    lines.append(f"  当前价: ¥{ob.price:.2f}    昨收: ¥{ob.prev_close:.2f}")
    lines.append(f"  今开: ¥{ob.open:.2f}    最高: ¥{ob.high:.2f}    最低: ¥{ob.low:.2f}")
    lines.append(f"  成交量: {ob.volume:,}股    成交额: {ob.amount:,.0f}元")
    lines.append(f"  时间: {ob.timestamp}")
    lines.append(f"{'─'*60}")
    
    # 卖盘 (从高到低)
    lines.append(f"  {'卖盘':^20}      {'买盘':^20}")
    lines.append(f"  {'─'*20}      {'─'*20}")
    lines.append(f"  卖五: {ob.ask5.volume:>8,}股 @ ¥{ob.ask5.price:.2f}      买五: {ob.bid5.volume:>8,}股 @ ¥{ob.bid5.price:.2f}")
    lines.append(f"  卖四: {ob.ask4.volume:>8,}股 @ ¥{ob.ask4.price:.2f}      买四: {ob.bid4.volume:>8,}股 @ ¥{ob.bid4.price:.2f}")
    lines.append(f"  卖三: {ob.ask3.volume:>8,}股 @ ¥{ob.ask3.price:.2f}      买三: {ob.bid3.volume:>8,}股 @ ¥{ob.bid3.price:.2f}")
    lines.append(f"  卖二: {ob.ask2.volume:>8,}股 @ ¥{ob.ask2.price:.2f}      买二: {ob.bid2.volume:>8,}股 @ ¥{ob.bid2.price:.2f}")
    lines.append(f"  卖一: {ob.ask1.volume:>8,}股 @ ¥{ob.ask1.price:.2f} ◄──► 买一: {ob.bid1.volume:>8,}股 @ ¥{ob.bid1.price:.2f}")
    lines.append(f"{'='*60}\n")
    
    return "\n".join(lines)


def format_analysis(analysis: OrderBookAnalysis, ob: OrderBook) -> str:
    """格式化输出盘口分析"""
    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"  {analysis.name} ({analysis.symbol}) - 盘口分析")
    lines.append(f"{'='*60}")
    
    # 买卖力量
    lines.append(f"  【买卖力量】")
    lines.append(f"  买盘总挂单: {analysis.total_bid_volume:,} 股")
    lines.append(f"  卖盘总挂单: {analysis.total_ask_volume:,} 股")
    lines.append(f"  买卖比: {analysis.bid_ask_ratio:.2f}")
    
    # 价格位置
    lines.append(f"\n  【价格位置】")
    lines.append(f"  买盘价格区间: ¥{ob.bid5.price:.2f} ~ ¥{ob.bid1.price:.2f} ({analysis.bid_price_range:.3f}元)")
    lines.append(f"  卖盘价格区间: ¥{ob.ask1.price:.2f} ~ ¥{ob.ask5.price:.2f} ({analysis.ask_price_range:.3f}元)")
    lines.append(f"  买卖价差: ¥{analysis.price_spread:.3f} ({analysis.price_spread_pct:.3f}%)")
    
    
    # 市场情绪
    lines.append(f"\n  【市场情绪】")
    sentiment_emoji = {
        "bullish": "📈 看多",
        "bearish": "📉 看空",
        "neutral": "➖ 中性"
    }
    lines.append(f"  情绪判断: {sentiment_emoji.get(analysis.sentiment, analysis.sentiment)}")
    lines.append(f"  情绪分数: {analysis.sentiment_score:+.1f} (范围: -100 到 +100)")
    
    lines.append(f"{'='*60}\n")
    
    return "\n".join(lines)


def format_depth(ob: OrderBook) -> str:
    """格式化输出盘口深度"""
    lines = []
    lines.append(f"\n{'='*70}")
    lines.append(f"  {ob.name} ({ob.symbol}) - 盘口深度")
    lines.append(f"{'='*70}")
    
    # 卖盘深度图 (从上到下)
    total_ask = ob.ask1.volume + ob.ask2.volume + ob.ask3.volume + ob.ask4.volume + ob.ask5.volume
    total_bid = ob.bid1.volume + ob.bid2.volume + ob.bid3.volume + ob.bid4.volume + ob.bid5.volume
    max_vol = max(total_ask, total_bid)
    
    lines.append(f"\n  {'卖盘深度':^35}")
    lines.append(f"  {'─'*35}")
    
    for i, (level, name) in enumerate([
        (ob.ask5, "卖五"), (ob.ask4, "卖四"), (ob.ask3, "卖三"), 
        (ob.ask2, "卖二"), (ob.ask1, "卖一")
    ]):
        bar_len = int(level.volume / max_vol * 30) if max_vol > 0 else 0
        bar = "█" * bar_len
        lines.append(f"  {name}: {level.volume:>8,}股 @ ¥{level.price:.2f} {bar}")
    
    lines.append(f"\n  {'─'*35} ◄ 当前价 ¥{ob.price:.2f} ► {'─'*35}\n")
    
    # 买盘深度图 (从下到上)
    lines.append(f"  {'买盘深度':^35}")
    lines.append(f"  {'─'*35}")
    
    for i, (level, name) in enumerate([
        (ob.bid1, "买一"), (ob.bid2, "买二"), (ob.bid3, "买三"), 
        (ob.bid4, "买四"), (ob.bid5, "买五")
    ]):
        bar_len = int(level.volume / max_vol * 30) if max_vol > 0 else 0
        bar = "█" * bar_len
        lines.append(f"  {name}: {level.volume:>8,}股 @ ¥{level.price:.2f} {bar}")
    
    lines.append(f"\n  买盘总量: {total_bid:,}股    卖盘总量: {total_ask:,}股    比值: {total_bid/total_ask:.2f}" if total_ask > 0 else "")
    lines.append(f"{'='*70}\n")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="A股盘口数据接口")
    parser.add_argument("symbol", help="股票代码 (如 600309)")
    parser.add_argument("--analyze", "-a", action="store_true", help="显示盘口分析")
    parser.add_argument("--depth", "-d", action="store_true", help="显示盘口深度图")
    parser.add_argument("--json", "-j", action="store_true", help="JSON格式输出")
    
    args = parser.parse_args()
    
    # 获取盘口数据
    ob = get_orderbook(args.symbol)
    
    if not ob:
        print(f"错误: 无法获取 {args.symbol} 的盘口数据")
        sys.exit(1)
    
    if args.json:
        # JSON 输出
        output = asdict(ob)
        output['bid1'] = asdict(ob.bid1)
        output['bid2'] = asdict(ob.bid2)
        output['bid3'] = asdict(ob.bid3)
        output['bid4'] = asdict(ob.bid4)
        output['bid5'] = asdict(ob.bid5)
        output['ask1'] = asdict(ob.ask1)
        output['ask2'] = asdict(ob.ask2)
        output['ask3'] = asdict(ob.ask3)
        output['ask4'] = asdict(ob.ask4)
        output['ask5'] = asdict(ob.ask5)
        
        if args.analyze:
            analysis = analyze_orderbook(ob)
            output['analysis'] = asdict(analysis)
        
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        # 表格输出
        print(format_orderbook(ob))
        
        if args.analyze:
            analysis = analyze_orderbook(ob)
            print(format_analysis(analysis, ob))
        
        if args.depth:
            print(format_depth(ob))


if __name__ == "__main__":
    main()