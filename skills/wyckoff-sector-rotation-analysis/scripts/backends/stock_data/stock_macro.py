#!/usr/bin/env python3
"""
A 股数据接口 - 宏观数据模块
==========================
获取中国宏观经济数据：LPR、CPI/PPI、PMI、社融、M2 等。

数据源：AKShare (无需 API key)

Usage:
    python stock_macro.py --dashboard                  # 完整仪表板
    python stock_macro.py --rates                      # 利率数据 (LPR, Shibor)
    python stock_macro.py --inflation                  # CPI/PPI 数据
    python stock_macro.py --pmi                        # PMI 数据
    python stock_macro.py --social-financing           # 社融 + M2
    python stock_macro.py --cycle                      # 经济周期评估
    python stock_macro.py --json                       # JSON 格式输出
"""
import argparse
import sys
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional, List, Dict


# ==================== 辅助函数 ====================

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


def safe_str(val, default="") -> str:
    """安全转换为字符串"""
    if val is None or val == "null" or val == "-":
        return default
    return str(val)


def _direction(values: list, lookback: int = 6) -> str:
    """判断数据趋势方向"""
    valid = [v for v in values if v is not None]
    if len(valid) < 2:
        return "数据不足"
    recent = valid[-lookback:]
    if len(recent) < 2:
        return "数据不足"
    change = (recent[-1] - recent[0]) / abs(recent[0]) if recent[0] != 0 else 0
    if change > 0.03:
        return "上升"
    elif change < -0.03:
        return "下降"
    return "平稳"


# ==================== 利率数据 ====================

def fetch_rates() -> dict:
    """获取中国利率数据 (LPR, Shibor 等)"""
    import akshare as ak

    result = {}

    # LPR (贷款市场报价利率)
    try:
        df = ak.macro_china_lpr()
        if df is not None and not df.empty:
            recent = df.tail(12)
            lpr_1y = []
            lpr_5y = []
            for _, row in recent.iterrows():
                lpr_1y.append({
                    "date": str(row.get("TRADE_DATE", "")),
                    "value": safe_float(row.get("LPR1Y")),
                })
                lpr_5y.append({
                    "date": str(row.get("TRADE_DATE", "")),
                    "value": safe_float(row.get("LPR5Y")),
                })
            result["lpr_1y"] = {
                "latest": lpr_1y[-1]["value"] if lpr_1y else None,
                "direction": _direction([e["value"] for e in lpr_1y]),
                "series": lpr_1y,
            }
            result["lpr_5y"] = {
                "latest": lpr_5y[-1]["value"] if lpr_5y else None,
                "direction": _direction([e["value"] for e in lpr_5y]),
                "series": lpr_5y,
            }
    except Exception as e:
        result["lpr"] = {"error": str(e)}

    # Shibor (上海银行间同业拆放利率)
    try:
        df = ak.macro_china_shibor_all()
        if df is not None and not df.empty:
            recent = df.head(30)
            records = []
            for _, row in recent.iterrows():
                records.append({
                    "date": str(row.iloc[0]) if len(row) > 0 else "",
                    "overnight": safe_float(row.iloc[1]) if len(row) > 1 else None,
                    "1w": safe_float(row.iloc[3]) if len(row) > 3 else None,
                })
            result["shibor"] = {
                "latest_overnight": records[0]["overnight"] if records else None,
                "latest_1w": records[0]["1w"] if records else None,
                "series": records[:10],
            }
    except Exception as e:
        result["shibor"] = {"error": str(e)}

    return result


# ==================== 通胀数据 ====================

def fetch_inflation() -> dict:
    """获取中国 CPI 和 PPI 数据"""
    import akshare as ak

    result = {}

    # CPI (消费者物价指数)
    try:
        df = ak.macro_china_cpi_monthly()
        if df is not None and not df.empty:
            recent = df.tail(12)
            records = []
            for _, row in recent.iterrows():
                records.append({
                    "date": str(row.iloc[0]),
                    "cpi_yoy": safe_float(row.iloc[1]) if len(row) > 1 else None,
                })
            result["cpi"] = {
                "latest": records[-1]["cpi_yoy"] if records else None,
                "direction": _direction([e["cpi_yoy"] for e in records]),
                "series": records,
            }
    except Exception as e:
        result["cpi"] = {"error": str(e)}

    # PPI (生产者物价指数)
    try:
        df = ak.macro_china_ppi_monthly()
        if df is not None and not df.empty:
            recent = df.tail(12)
            records = []
            for _, row in recent.iterrows():
                records.append({
                    "date": str(row.iloc[0]),
                    "ppi_yoy": safe_float(row.iloc[1]) if len(row) > 1 else None,
                })
            result["ppi"] = {
                "latest": records[-1]["ppi_yoy"] if records else None,
                "direction": _direction([e["ppi_yoy"] for e in records]),
                "series": records,
            }
    except Exception as e:
        result["ppi"] = {"error": str(e)}

    return result


# ==================== PMI 数据 ====================

def fetch_pmi() -> dict:
    """获取中国 PMI 数据"""
    import akshare as ak

    result = {}

    try:
        df = ak.macro_china_pmi()
        if df is not None and not df.empty:
            recent = df.tail(12)
            records = []
            for _, row in recent.iterrows():
                records.append({
                    "date": str(row.iloc[0]),
                    "manufacturing_pmi": safe_float(row.iloc[1]) if len(row) > 1 else None,
                    "non_manufacturing_pmi": safe_float(row.iloc[2]) if len(row) > 2 else None,
                })
            mfg_values = [e["manufacturing_pmi"] for e in records]
            latest_val = records[-1]["manufacturing_pmi"] if records else None
            result["manufacturing_pmi"] = {
                "latest": latest_val,
                "direction": _direction(mfg_values),
                "above_50": (latest_val or 0) > 50 if latest_val else None,
                "interpretation": (
                    "高于 50 — 制造业扩张"
                    if latest_val and latest_val > 50
                    else "低于 50 — 制造业收缩"
                ),
                "series": records,
            }
    except Exception as e:
        result["manufacturing_pmi"] = {"error": str(e)}

    return result


# ==================== 社融与 M2 ====================

def fetch_social_financing() -> dict:
    """获取中国社会融资规模和 M2 数据"""
    import akshare as ak

    result = {}

    # 社会融资规模
    try:
        df = ak.macro_china_shrzgm()
        if df is not None and not df.empty:
            recent = df.tail(12)
            records = []
            for _, row in recent.iterrows():
                records.append({
                    "date": str(row.iloc[0]),
                    "value": safe_float(row.iloc[1]) if len(row) > 1 else None,
                })
            result["social_financing"] = {
                "latest": records[-1]["value"] if records else None,
                "direction": _direction([e["value"] for e in records]),
                "series": records,
                "interpretation": "社融增长表示信用扩张/收缩",
            }
    except Exception as e:
        result["social_financing"] = {"error": str(e)}

    # M2 货币供应量
    try:
        df = ak.macro_china_m2_monthly()
        if df is not None and not df.empty:
            recent = df.tail(12)
            records = []
            for _, row in recent.iterrows():
                records.append({
                    "date": str(row.iloc[0]),
                    "m2_yoy": safe_float(row.iloc[1]) if len(row) > 1 else None,
                })
            result["m2_growth"] = {
                "latest": records[-1]["m2_yoy"] if records else None,
                "direction": _direction([e["m2_yoy"] for e in records]),
                "series": records,
            }
    except Exception as e:
        result["m2_growth"] = {"error": str(e)}

    return result


# ==================== 经济周期评估 ====================

def assess_business_cycle() -> dict:
    """
    判断中国经济周期阶段
    使用 PMI、CPI、PPI、信用数据等指标
    """
    inflation = fetch_inflation()
    pmi_data = fetch_pmi()
    financing = fetch_social_financing()

    signals = {}

    # PMI 信号
    mfg_pmi = pmi_data.get("manufacturing_pmi", {})
    pmi_latest = mfg_pmi.get("latest")
    pmi_dir = mfg_pmi.get("direction", "平稳")
    signals["pmi"] = {
        "value": pmi_latest,
        "direction": pmi_dir,
        "expanding": pmi_latest > 50 if pmi_latest else None,
    }

    # 通胀信号
    cpi_latest = inflation.get("cpi", {}).get("latest")
    ppi_latest = inflation.get("ppi", {}).get("latest")
    signals["cpi"] = {"value": cpi_latest, "direction": inflation.get("cpi", {}).get("direction")}
    signals["ppi"] = {"value": ppi_latest, "direction": inflation.get("ppi", {}).get("direction")}

    # 信用信号
    sf = financing.get("social_financing", {})
    sf_dir = sf.get("direction", "平稳")
    m2 = financing.get("m2_growth", {})
    m2_dir = m2.get("direction", "平稳")
    signals["credit"] = {"social_financing_direction": sf_dir, "m2_direction": m2_dir}

    # 周期阶段判断
    pmi_expanding = pmi_latest and pmi_latest > 50
    pmi_rising = pmi_dir == "上升"
    credit_expanding = sf_dir == "上升" or m2_dir == "上升"

    if pmi_expanding and pmi_rising and credit_expanding:
        phase = "复苏期"
        description = "经济复苏期：PMI 回升，信用扩张，政策宽松"
        favored = ["消费", "科技", "金融"]
        disfavored = ["公用事业"]
    elif pmi_expanding and not pmi_rising:
        phase = "扩张期"
        description = "经济扩张期：PMI 维持高位，增长稳定"
        favored = ["制造业", "周期股", "金融"]
        disfavored = ["防御板块"]
    elif not pmi_expanding and ppi_latest and ppi_latest < 0:
        phase = "收缩期"
        description = "经济收缩期：PMI 低于 50，PPI 通缩"
        favored = ["消费防御", "公用事业", "高股息"]
        disfavored = ["周期股", "地产"]
    else:
        phase = "过渡期"
        description = "过渡期：经济信号混合"
        favored = ["均衡配置"]
        disfavored = []

    return {
        "phase": phase,
        "description": description,
        "signals": signals,
        "sector_implications": {
            "favored": favored,
            "disfavored": disfavored,
        },
        "factor_implications": {
            "复苏期": "小盘、动量因子占优",
            "扩张期": "质量、成长因子占优",
            "收缩期": "低波动、红利因子占优",
            "过渡期": "均衡配置各因子",
        }.get(phase, ""),
    }


# ==================== 仪表板 ====================

def macro_dashboard() -> dict:
    """综合宏观数据仪表板"""
    return {
        "timestamp": datetime.now().isoformat(),
        "rates": fetch_rates(),
        "inflation": fetch_inflation(),
        "pmi": fetch_pmi(),
        "social_financing": fetch_social_financing(),
        "business_cycle": assess_business_cycle(),
    }


# ==================== 格式化输出 ====================

def format_table(data: dict, title: str) -> str:
    """格式化数据为表格输出"""
    lines = []
    lines.append(f"\n{'=' * 60}")
    lines.append(f"  {title}")
    lines.append(f"{'=' * 60}")

    def format_value(v):
        if v is None:
            return "--"
        if isinstance(v, float):
            return f"{v:.2f}"
        return str(v)[:20]

    def format_dict(d: dict, indent: int = 0):
        for k, v in d.items():
            prefix = "  " * indent
            if isinstance(v, dict):
                if "series" in v or "data" in v:
                    lines.append(f"{prefix}{k}: {v.get('latest', v.get('direction', '...'))}")
                else:
                    lines.append(f"{prefix}{k}:")
                    format_dict(v, indent + 1)
            elif isinstance(v, list):
                lines.append(f"{prefix}{k}: [{len(v)} 条]")
            else:
                lines.append(f"{prefix}{k}: {format_value(v)}")

    format_dict(data)
    lines.append(f"{'=' * 60}\n")
    return "\n".join(lines)


def output_json(data):
    """JSON 格式输出"""
    import json
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def error_exit(msg, code=1):
    """错误退出"""
    print(f"Error: {msg}", file=sys.stderr)
    sys.exit(code)


# ==================== CLI ====================

def main():
    parser = argparse.ArgumentParser(
        description="中国宏观数据获取工具 (AKShare, 无需 API key)"
    )
    parser.add_argument("--dashboard", action="store_true", help="完整仪表板")
    parser.add_argument("--rates", action="store_true", help="利率数据 (LPR, Shibor)")
    parser.add_argument("--inflation", action="store_true", help="CPI/PPI")
    parser.add_argument("--pmi", action="store_true", help="PMI 数据")
    parser.add_argument("--social-financing", action="store_true",
                        help="社会融资规模 + M2")
    parser.add_argument("--cycle", action="store_true",
                        help="经济周期评估")
    parser.add_argument("--json", "-j", action="store_true",
                        help="JSON 格式输出")
    args = parser.parse_args()

    try:
        if args.rates:
            data = fetch_rates()
        elif args.inflation:
            data = fetch_inflation()
        elif args.pmi:
            data = fetch_pmi()
        elif args.social_financing:
            data = fetch_social_financing()
        elif args.cycle:
            data = assess_business_cycle()
        else:
            data = macro_dashboard()

        if args.json:
            output_json(data)
        else:
            print(format_table(data, "中国宏观数据"))

    except ImportError:
        error_exit("需要安装 akshare: pip install akshare")
    except Exception as e:
        error_exit(f"错误：{e}")


if __name__ == "__main__":
    main()
