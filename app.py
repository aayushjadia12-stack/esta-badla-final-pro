import os, re, uuid, math, json, zipfile
from io import BytesIO
from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Optional, Tuple

import pandas as pd
import requests
import streamlit as st

try:
    import yfinance as yf
except Exception:
    yf = None

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:
    st_autorefresh = None

try:
    from supabase import create_client
except Exception:
    create_client = None

st.set_page_config(page_title="ESTA Badla Signal Pro", page_icon="📈", layout="wide")

CSS = """
<style>
.block-container{padding-top:1rem;padding-bottom:2rem;max-width:1200px;}
.metric-card{border:1px solid rgba(128,128,128,.25);border-radius:14px;padding:12px;margin:6px 0;background:rgba(128,128,128,.04)}
.good{color:#159447;font-weight:700}.bad{color:#d13b3b;font-weight:700}.warn{color:#c27c00;font-weight:700}.muted{color:#777;font-size:.9rem}.big{font-size:1.25rem;font-weight:700}.small{font-size:.85rem}.pill{display:inline-block;padding:3px 9px;border-radius:999px;border:1px solid rgba(128,128,128,.3);font-size:.85rem;margin:2px}.section-title{font-size:1.2rem;font-weight:800;margin-top:1rem}.stButton>button{border-radius:10px;font-weight:700}.danger-box{border:1px solid #d13b3b;border-radius:12px;padding:10px;background:rgba(209,59,59,.05)}
@media(max-width:700px){.block-container{padding-left:.65rem;padding-right:.65rem}.big{font-size:1.05rem}.metric-card{padding:10px}}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

COMMODITIES = {
    "Silver": {
        "symbol": "SI=F", "unit": "₹/kg", "intl_unit": "$/oz", "conversion": "oz_to_kg", "default_duty": 15.5,
        "comex_contract_qty": 155.517, "mcx_lot_qty": 30.0, "suggested_comex_lots": 1.0,
        "normal_low": 5000, "normal_high": 10000, "high": 15000, "extreme": 20000,
        "charges": 250, "slippage": 150, "currency_exposure_factor": 2000000,
    },
    "Gold": {
        "symbol": "GC=F", "unit": "₹/kg", "intl_unit": "$/oz", "conversion": "oz_to_kg", "default_duty": 15.5,
        "comex_contract_qty": 3.11035, "mcx_lot_qty": 1.0, "suggested_comex_lots": 1.0,
        "normal_low": 20000, "normal_high": 60000, "high": 100000, "extreme": 150000,
        "charges": 2000, "slippage": 1500, "currency_exposure_factor": 2000000,
    },
    "Copper": {
        "symbol": "HG=F", "unit": "₹/kg", "intl_unit": "$/lb", "conversion": "lb_to_kg", "default_duty": 5.0,
        "comex_contract_qty": 11339.8, "mcx_lot_qty": 2500.0, "suggested_comex_lots": 1.0,
        "normal_low": 5, "normal_high": 15, "high": 30, "extreme": 45,
        "charges": 0.8, "slippage": 0.7, "currency_exposure_factor": 2000000,
    },
    "Zinc": {
        "symbol": None, "unit": "₹/kg", "intl_unit": "$/MT", "conversion": "mt_to_kg", "default_duty": 5.0,
        "comex_contract_qty": 5000.0, "mcx_lot_qty": 5000.0, "suggested_comex_lots": 1.0,
        "normal_low": 3, "normal_high": 8, "high": 15, "extreme": 22,
        "charges": 0.6, "slippage": 0.5, "currency_exposure_factor": 2000000,
    },
    "Aluminium": {
        "symbol": None, "unit": "₹/kg", "intl_unit": "$/MT", "conversion": "mt_to_kg", "default_duty": 5.0,
        "comex_contract_qty": 5000.0, "mcx_lot_qty": 5000.0, "suggested_comex_lots": 1.0,
        "normal_low": 2, "normal_high": 6, "high": 12, "extreme": 18,
        "charges": 0.5, "slippage": 0.5, "currency_exposure_factor": 2000000,
    },
}

LOCAL_FILES = {
    "cash_ledger": "cash_ledger.csv",
    "paper_trades": "paper_trades.csv",
    "rate_history": "rate_history.csv",
}

TRADE_COLS = ["trade_id","opened_at","closed_at","status","commodity","direction","matched_qty_kg","comex_lots","mcx_lots","dgcx_lots","entry_comex","entry_mcx","entry_dgcx","entry_usdinr","entry_landed","entry_gross_spread","entry_cost_per_kg","entry_net_spread","exit_comex","exit_mcx","exit_dgcx","exit_usdinr","exit_landed","exit_gross_spread","exit_cost_per_kg","exit_net_spread","realized_pnl","mae","mfe","notes"]
CASH_COLS = ["id","ts","type","amount","note"]
RATE_COLS = ["id","ts","commodity","comex","mcx","dgcx","usdinr","landed","gross_spread","cost_per_kg","net_spread","source_summary"]

# ---------- Utilities ----------
def now_iso():
    return datetime.now(timezone.utc).isoformat()

def fmt_inr(x, decimals=0):
    try:
        if x is None or (isinstance(x,float) and math.isnan(x)): return "—"
        return f"₹{x:,.{decimals}f}"
    except Exception: return "—"

def fmt_num(x, decimals=2):
    try:
        if x is None or (isinstance(x,float) and math.isnan(x)): return "—"
        return f"{x:,.{decimals}f}"
    except Exception: return "—"

def to_float(x, default=None):
    try:
        if x is None: return default
        if isinstance(x, str): x=x.replace(",","").strip()
        return float(x)
    except Exception:
        return default

def parse_ts(s):
    try:
        return pd.to_datetime(s, utc=True)
    except Exception:
        return pd.NaT

# ---------- Storage ----------
class Store:
    def __init__(self):
        self.mode = "local"
        self.client = None
        url = ""
        key = ""
        try:
            url = st.secrets.get("SUPABASE_URL", "")
            key = st.secrets.get("SUPABASE_KEY", "")
        except Exception:
            url = os.getenv("SUPABASE_URL", "")
            key = os.getenv("SUPABASE_KEY", "")
        if url and key and create_client is not None:
            try:
                self.client = create_client(url, key)
                # quick test; no failure if tables not present yet
                self.mode = "supabase"
            except Exception as e:
                st.warning(f"Supabase connection failed. Using local CSV. Error: {e}")

    def read(self, table, cols):
        if self.mode == "supabase" and self.client:
            try:
                res = self.client.table(table).select("*").execute()
                data = res.data or []
                return pd.DataFrame(data, columns=cols) if not data else pd.DataFrame(data)
            except Exception as e:
                st.error(f"Supabase read error for {table}: {e}")
                return pd.DataFrame(columns=cols)
        path = LOCAL_FILES.get(table, f"{table}.csv")
        if os.path.exists(path):
            try: return pd.read_csv(path)
            except Exception: pass
        return pd.DataFrame(columns=cols)

    def insert(self, table, row):
        row = {k: (None if isinstance(v,float) and math.isnan(v) else v) for k,v in row.items()}
        if self.mode == "supabase" and self.client:
            return self.client.table(table).insert(row).execute()
        df = self.read(table, list(row.keys()))
        df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df.to_csv(LOCAL_FILES.get(table, f"{table}.csv"), index=False)

    def update(self, table, key_col, key_val, updates):
        if self.mode == "supabase" and self.client:
            return self.client.table(table).update(updates).eq(key_col, key_val).execute()
        path = LOCAL_FILES.get(table, f"{table}.csv")
        df = self.read(table, TRADE_COLS if table=="paper_trades" else CASH_COLS)
        if key_col in df.columns:
            for k,v in updates.items():
                df.loc[df[key_col].astype(str)==str(key_val), k]=v
            df.to_csv(path, index=False)

store = Store()

# ---------- Fetching ----------
@st.cache_data(ttl=12, show_spinner=False)
def yf_last(symbol: str) -> Tuple[Optional[float], str]:
    if not symbol or yf is None:
        return None, "manual only"
    try:
        t = yf.Ticker(symbol)
        hist = t.history(period="1d", interval="1m")
        if len(hist) > 0:
            return float(hist["Close"].dropna().iloc[-1]), f"Yahoo {symbol}"
        info = getattr(t, "fast_info", {})
        val = info.get("last_price") if hasattr(info, "get") else None
        if val:
            return float(val), f"Yahoo {symbol}"
    except Exception as e:
        return None, f"Yahoo failed: {str(e)[:55]}"
    return None, f"Yahoo {symbol} no data"

@st.cache_data(ttl=20, show_spinner=False)
def fetch_usdinr() -> Tuple[Optional[float], str]:
    val, src = yf_last("INR=X")
    if val: return val, src
    return None, src

@st.cache_data(ttl=30, show_spinner=False)
def fetch_mcx_sources(commodity: str) -> List[Dict[str, Any]]:
    """Fetch MCX prices from multiple public/demo pages.

    Important: this does NOT hard-reject values using fixed price ranges. It returns
    every extracted source value and later marks values as Good / Suspicious by
    comparing sources with each other. Manual override is always available.
    """
    url_map = {
        "Silver": {
            "Moneycontrol": "https://www.moneycontrol.com/commodity/mcx-silver-price/",
            "Groww": "https://groww.in/commodities/futures/mcx_silver",
            "Economic Times": "https://economictimes.indiatimes.com/commoditysummary/symbol-SILVER.cms",
        },
        "Gold": {
            "Moneycontrol": "https://www.moneycontrol.com/commodity/mcx-gold-price/",
            "Groww": "https://groww.in/commodities/futures/mcx_gold",
            "Economic Times": "https://economictimes.indiatimes.com/commoditysummary/symbol-GOLD.cms",
        },
        "Copper": {
            "Moneycontrol": "https://www.moneycontrol.com/commodity/mcx-copper-price/",
            "Groww": "https://groww.in/commodities/futures/mcx_copper",
        },
        "Zinc": {
            "Moneycontrol": "https://www.moneycontrol.com/commodity/mcx-zinc-price/",
            "Groww": "https://groww.in/commodities/futures/mcx_zinc",
        },
        "Aluminium": {
            "Moneycontrol": "https://www.moneycontrol.com/commodity/mcx-aluminium-price/",
            "Groww": "https://groww.in/commodities/futures/mcx_aluminium",
        },
    }
    headers = {"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    out = []

    def extract_price(text: str, commodity: str) -> Tuple[Optional[float], str]:
        # 1) Prefer explicit labels/JSON fields if present.
        patterns = [
            r'"(?:lastPrice|last_price|ltp|last|currentPrice|price)"\s*:\s*"?([0-9,]+(?:\.[0-9]+)?)"?',
            r'(?:Last Price|LTP|Last Traded Price|Current Price|Price)\s*[:\-]?\s*₹?\s*([0-9,]+(?:\.[0-9]+)?)',
            r'₹\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?)',
        ]
        for pat in patterns:
            m = re.search(pat, text, flags=re.I)
            if m:
                return to_float(m.group(1)), "explicit pattern"

        # 2) Fallback: collect many numbers and choose a rate-like candidate.
        nums = re.findall(r'₹?\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?|[0-9]{3,9}(?:\.[0-9]+)?)', text)
        vals = []
        for n in nums[:500]:
            v = to_float(n)
            if v is None or v <= 0:
                continue
            # No hard accept/reject range. These are only extraction heuristics to avoid dates/percentages.
            if commodity in ["Copper", "Zinc", "Aluminium"]:
                if 20 <= v <= 5000:
                    vals.append(v)
            else:
                if v >= 1000:
                    vals.append(v)
        if not vals:
            return None, "no numeric candidate"
        # Use the median-like candidate from first few candidates to avoid taking 52-week high/low if possible.
        first = vals[:20]
        return float(pd.Series(first).median()), "numeric fallback median"

    for src, url in url_map.get(commodity, {}).items():
        try:
            r = requests.get(url, headers=headers, timeout=8)
            val, note = extract_price(r.text, commodity)
            out.append({"source": src, "price": val, "status": "Fetched" if val else "No price", "note": note, "url": url})
        except Exception as e:
            out.append({"source": src, "price": None, "status": "Failed", "note": str(e)[:80], "url": url})

    # Compare sources and mark suspicious values, but do not delete them.
    vals = [x["price"] for x in out if x.get("price") is not None]
    if vals:
        med = float(pd.Series(vals).median())
        for x in out:
            if x.get("price") is None:
                continue
            diff_pct = abs(x["price"] - med) / abs(med) if med else 0
            x["median"] = med
            x["diff_pct"] = diff_pct
            if len(vals) >= 2 and diff_pct > 0.08:
                x["status"] = "Suspicious"
                x["note"] = f"Differs {diff_pct:.1%} from source median. Shown, not blocked."
            elif len(vals) >= 2:
                x["status"] = "Good"
                x["note"] = f"Near source median. {x.get('note','')}"
            else:
                x["status"] = "Single source"
                x["note"] = "Only one public source returned a value."
    return out

def choose_mcx_rate(sources: List[Dict[str, Any]], preference: str, manual_mcx: Optional[float]) -> Tuple[Optional[float], str]:
    if preference == "Manual MCX":
        return manual_mcx, "Manual MCX"
    valid = [x for x in sources if x.get("price") is not None]
    if not valid:
        return None, "No public MCX source"
    if preference and preference not in ["Auto best source", "Manual MCX"]:
        for x in valid:
            if x.get("source") == preference:
                return float(x["price"]), f"{preference} ({x.get('status')})"
        return float(valid[0]["price"]), f"{valid[0].get('source')} fallback"
    # Auto best: choose value closest to median across sources. Suspicious values remain visible but are less likely to be picked.
    vals = [x["price"] for x in valid]
    med = float(pd.Series(vals).median())
    best = min(valid, key=lambda x: abs(x["price"] - med))
    return float(best["price"]), f"Auto best: {best.get('source')} ({best.get('status')})"

@st.cache_data(ttl=30, show_spinner=False)
def fetch_mcx_from_public(commodity: str) -> Tuple[Optional[float], str]:
    sources = fetch_mcx_sources(commodity)
    return choose_mcx_rate(sources, "Auto best source", None)

@st.cache_data(ttl=30, show_spinner=False)
def fetch_dgcx() -> Tuple[Optional[float], str]:
    # Attempt from public pages; manual fallback remains important.
    urls = [
        "https://www.barchart.com/futures/quotes/INR*0/futures-prices",
        "https://www.dgcx.ae/products/inr-usd-futures",
    ]
    headers = {"User-Agent":"Mozilla/5.0"}
    for u in urls:
        try:
            r = requests.get(u, headers=headers, timeout=7)
            text = r.text
            # DGCX DINR often around 100-110 cents per INR 100
            nums = re.findall(r'(?<![0-9])([0-9]{3}\.[0-9]{1,4}|[0-9]{3})(?![0-9])', text)
            vals = [to_float(n) for n in nums]
            vals = [v for v in vals if v and 90 <= v <= 115]
            if vals:
                return vals[0], "DGCX/Barchart scrape"
        except Exception:
            continue
    return None, "DGCX fetch failed"

def dgcx_to_usdinr(dgcx_quote: Optional[float]) -> Optional[float]:
    if not dgcx_quote: return None
    # DINR is US cents per ₹100. USDINR = 100 / (quote/100)
    return 100.0 / (dgcx_quote / 100.0)

def calc_landed(commodity: str, intl_price, usdinr, duty):
    cfg = COMMODITIES[commodity]
    if intl_price is None or usdinr is None: return None
    if cfg["conversion"] == "oz_to_kg":
        base = intl_price * usdinr * 32.1507466
    elif cfg["conversion"] == "lb_to_kg":
        base = intl_price * usdinr * 2.2046226218
    elif cfg["conversion"] == "mt_to_kg":
        base = intl_price * usdinr / 1000.0
    else:
        base = intl_price * usdinr
    return base * (1 + duty/100.0)

def calc_snapshot(commodity, intl_price, mcx_price, dgcx_quote, manual_usdinr, duty, charges, slippage):
    implied = dgcx_to_usdinr(dgcx_quote)
    usdinr = implied or manual_usdinr
    landed = calc_landed(commodity, intl_price, usdinr, duty)
    gross = None if landed is None or mcx_price is None else mcx_price - landed
    cost = (charges or 0) + (slippage or 0)
    net = None if gross is None else gross - cost
    return {"usdinr": usdinr, "landed": landed, "gross_spread": gross, "cost_per_kg": cost, "net_spread": net}

def get_rate_inputs(commodity: str, mcx_preference: str = "Auto best source", manual_mcx: Optional[float] = None) -> Dict[str, Any]:
    cfg = COMMODITIES[commodity]
    auto_intl, src_intl = yf_last(cfg.get("symbol")) if cfg.get("symbol") else (None, "manual/global only")
    mcx_sources = fetch_mcx_sources(commodity)
    auto_mcx, src_mcx = choose_mcx_rate(mcx_sources, mcx_preference, manual_mcx)
    auto_dgcx, src_dgcx = fetch_dgcx()
    auto_usdinr, src_usd = fetch_usdinr()
    return {"auto_intl":auto_intl,"src_intl":src_intl,"auto_mcx":auto_mcx,"src_mcx":src_mcx,"mcx_sources":mcx_sources,"auto_dgcx":auto_dgcx,"src_dgcx":src_dgcx,"auto_usdinr":auto_usdinr,"src_usd":src_usd}

# ---------- Trade logic ----------
MARGIN_DEFAULTS = {
    "Silver": {"comex_pct": 0.18, "mcx_pct": 0.08, "dgcx_pct": 0.025},
    "Gold": {"comex_pct": 0.06, "mcx_pct": 0.06, "dgcx_pct": 0.025},
    "Copper": {"comex_pct": 0.08, "mcx_pct": 0.08, "dgcx_pct": 0.025},
    "Zinc": {"comex_pct": 0.08, "mcx_pct": 0.08, "dgcx_pct": 0.025},
    "Aluminium": {"comex_pct": 0.08, "mcx_pct": 0.08, "dgcx_pct": 0.025},
}

def leg_actions(direction: str) -> Tuple[str, str, str]:
    d = str(direction or "")
    if "MCX_LOW" in d or "BUY_MCX" in d:
        return "SELL", "BUY", "BUY"
    return "BUY", "SELL", "SELL"

def intl_notional_inr(commodity: str, intl_price, lots, usdinr) -> float:
    cfg = COMMODITIES[commodity]
    price = to_float(intl_price, 0) or 0
    lots = to_float(lots, 0) or 0
    usd = to_float(usdinr, 0) or 0
    qty = lots * cfg["comex_contract_qty"]  # stored in kg-equivalent for all tracked commodities
    if cfg["conversion"] == "oz_to_kg":
        usd_notional = price * qty * 32.1507466
    elif cfg["conversion"] == "lb_to_kg":
        usd_notional = price * qty * 2.2046226218
    elif cfg["conversion"] == "mt_to_kg":
        usd_notional = price * qty / 1000.0
    else:
        usd_notional = price * qty
    return usd_notional * usd

def estimate_margins(commodity: str, intl_price, mcx_price, dgcx_quote, usdinr, comex_lots, mcx_lots, dgcx_lots, direction="MCX_HIGH") -> Dict[str, Any]:
    cfg = COMMODITIES[commodity]
    md = MARGIN_DEFAULTS.get(commodity, MARGIN_DEFAULTS["Silver"])
    usd = to_float(usdinr, None) or dgcx_to_usdinr(to_float(dgcx_quote, None)) or 0
    comex_notional = intl_notional_inr(commodity, intl_price, comex_lots, usd)
    mcx_notional = (to_float(mcx_price, 0) or 0) * (to_float(mcx_lots, 0) or 0) * cfg["mcx_lot_qty"]
    dgcx_notional = (to_float(dgcx_lots, 0) or 0) * cfg.get("currency_exposure_factor", 2000000)
    ia, ma, da = leg_actions(direction)
    out = {
        "intl_action": ia, "mcx_action": ma, "dgcx_action": da,
        "comex_notional": abs(comex_notional), "mcx_notional": abs(mcx_notional), "dgcx_notional": abs(dgcx_notional),
        "comex_margin": abs(comex_notional) * md["comex_pct"],
        "mcx_margin": abs(mcx_notional) * md["mcx_pct"],
        "dgcx_margin": abs(dgcx_notional) * md["dgcx_pct"],
        "comex_pct": md["comex_pct"], "mcx_pct": md["mcx_pct"], "dgcx_pct": md["dgcx_pct"],
    }
    out["total_margin"] = out["comex_margin"] + out["mcx_margin"] + out["dgcx_margin"]
    return out

def margin_card(marg: Dict[str, Any], title: str = "Estimated margin used"):
    st.markdown(f"**{title}**")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"COMEX/Intl {marg.get('intl_action','')}", fmt_inr(marg.get("comex_margin"),0), f"{marg.get('comex_pct',0)*100:.1f}%")
    c2.metric(f"MCX {marg.get('mcx_action','')}", fmt_inr(marg.get("mcx_margin"),0), f"{marg.get('mcx_pct',0)*100:.1f}%")
    c3.metric(f"DGCX {marg.get('dgcx_action','')}", fmt_inr(marg.get("dgcx_margin"),0), f"{marg.get('dgcx_pct',0)*100:.1f}%")
    c4.metric("Total margin", fmt_inr(marg.get("total_margin"),0))
    st.caption("Margin is estimated for paper trading only. Broker/exchange SPAN, exposure, currency conversion, calendar spreads and intraday rules can be different.")

def suggested_lots(commodity, comex_lots, intl_price=None, usdinr=None):
    cfg=COMMODITIES[commodity]
    qty = comex_lots * cfg["comex_contract_qty"]
    mcx_lots = round(qty / cfg["mcx_lot_qty"])
    matched = mcx_lots * cfg["mcx_lot_qty"]
    # DGCX hedge: approx INR value / 20L
    if intl_price and usdinr:
        notional = qty * (calc_landed(commodity, intl_price, usdinr, 0) or 0)
    else:
        notional = matched * 200000  # rough placeholder
    dgcx_lots = max(0, round(notional / cfg["currency_exposure_factor"]))
    return mcx_lots, dgcx_lots, matched

def direction_label(direction):
    if direction == "MCX_HIGH": return "Buy International + Sell MCX + Sell DGCX"
    if direction == "MCX_LOW": return "Sell International + Buy MCX + Buy DGCX"
    return direction

def pnl_for_trade(row, current_net_spread):
    entry = to_float(row.get("entry_net_spread"), 0)
    qty = to_float(row.get("matched_qty_kg"), 0)
    if current_net_spread is None: return 0
    direction = str(row.get("direction", "MCX_HIGH"))
    if "MCX_LOW" in direction or "BUY_MCX" in direction:
        return (current_net_spread - entry) * qty
    return (entry - current_net_spread) * qty

def zone_and_signal(commodity, net_spread, hist_df=None):
    cfg = COMMODITIES[commodity]
    if net_spread is None:
        return {"zone":"No data","signal":"Avoid","score":0,"trade":"No trade","reason":"Missing rate data","target":None,"stop":None}
    # Prefer history averages if available
    avg7 = avg30 = None
    if hist_df is not None and not hist_df.empty:
        df = hist_df.copy()
        df["ts_dt"] = pd.to_datetime(df.get("ts"), errors="coerce", utc=True)
        df = df[df.get("commodity","")==commodity] if "commodity" in df.columns else df
        df["net_spread"] = pd.to_numeric(df.get("net_spread"), errors="coerce")
        now = pd.Timestamp.now(tz="UTC")
        avg7 = df[df["ts_dt"] >= now - pd.Timedelta(days=7)]["net_spread"].mean()
        avg30 = df[df["ts_dt"] >= now - pd.Timedelta(days=30)]["net_spread"].mean()
    base_avg = avg30 if pd.notna(avg30) else (avg7 if pd.notna(avg7) else (cfg["normal_low"]+cfg["normal_high"])/2)
    if abs(base_avg) < 1e-9: base_avg = (cfg["normal_low"]+cfg["normal_high"])/2
    distance = (net_spread - base_avg)
    pct = distance / abs(base_avg) if base_avg else 0
    score = 50 + min(35, abs(pct)*50)
    zone = "Normal"
    signal = "Wait"
    trade = "No fresh trade"
    reason = f"Net badla is near average {fmt_inr(base_avg,0)}"
    target = base_avg
    stop = None
    if net_spread >= cfg["extreme"] or pct > 0.45:
        zone="Very high"; signal="Entry allowed"; trade="Buy International + Sell MCX + Sell DGCX"; reason="MCX premium is very high vs average"; score += 15; stop = net_spread + abs(net_spread-base_avg)*0.6
    elif net_spread >= cfg["high"] or pct > 0.25:
        zone="High"; signal="Entry allowed"; trade="Buy International + Sell MCX + Sell DGCX"; reason="MCX premium is high vs average"; score += 8; stop = net_spread + abs(net_spread-base_avg)*0.8
    elif net_spread <= -cfg["normal_high"] or pct < -0.25:
        zone="Low/negative"; signal="Entry allowed"; trade="Sell International + Buy MCX + Buy DGCX"; reason="MCX is cheap vs international landed"; score += 8; stop = net_spread - abs(net_spread-base_avg)*0.8
    elif cfg["normal_low"] <= net_spread <= cfg["normal_high"]:
        zone="Normal"; score = min(score, 55)
    else:
        zone="Watch"; score=min(score,65); signal="Watch"
    return {"zone":zone,"signal":signal,"score":int(max(0,min(100,score))),"trade":trade,"reason":reason,"target":target,"stop":stop,"avg7":avg7,"avg30":avg30}

def save_rate_snapshot(commodity, rates, snap, source_summary):
    row = {
        "id": str(uuid.uuid4()), "ts": now_iso(), "commodity": commodity,
        "comex": rates.get("intl"), "mcx": rates.get("mcx"), "dgcx": rates.get("dgcx"), "usdinr": snap.get("usdinr"),
        "landed": snap.get("landed"), "gross_spread": snap.get("gross_spread"), "cost_per_kg": snap.get("cost_per_kg"),
        "net_spread": snap.get("net_spread"), "source_summary": source_summary
    }
    try: store.insert("rate_history", row)
    except Exception as e: st.caption(f"Rate save skipped: {e}")

# ---------- Sidebar ----------
st.sidebar.title("ESTA Badla Pro")
refresh = st.sidebar.selectbox("Auto refresh", ["Off", "10 sec", "15 sec", "20 sec", "30 sec", "60 sec"], index=2)
if refresh != "Off" and st_autorefresh:
    secs = int(refresh.split()[0])
    st_autorefresh(interval=secs*1000, key="auto_refresh")
elif refresh != "Off":
    st.sidebar.caption("Install streamlit-autorefresh for automatic refresh.")

store_badla = st.sidebar.checkbox("Save rate snapshot on refresh", value=True, help="Saves current selected commodity spread history to Supabase/local CSV.")
st.sidebar.caption(f"Storage: {'Supabase online' if store.mode=='supabase' else 'Local CSV'}")

cash_df = store.read("cash_ledger", CASH_COLS)
trades_df = store.read("paper_trades", TRADE_COLS)
hist_df = store.read("rate_history", RATE_COLS)

# ---------- Cash calculations ----------
def total_cash():
    if cash_df.empty: return 0.0
    return pd.to_numeric(cash_df.get("amount"), errors="coerce").fillna(0).sum()

def realized_pnl_total():
    if trades_df.empty or "realized_pnl" not in trades_df.columns: return 0.0
    closed = trades_df[trades_df.get("status") == "CLOSED"]
    return pd.to_numeric(closed.get("realized_pnl"), errors="coerce").fillna(0).sum()

# ---------- Header ----------
st.title("📈 ESTA Badla Signal Pro")
st.caption("Paper/demo badla scanner and exact ticket tracker. Public data can be delayed; use broker feeds before live trading.")

menu = st.tabs(["Dashboard", "Badla Scanner", "Open Trade", "Manual Ticket", "Past Price Simulator", "Trade Management", "History", "Settings"])

# Shared selected rates controls in sidebar
st.sidebar.markdown("---")
sel_commodity = st.sidebar.selectbox("Selected commodity", list(COMMODITIES.keys()), index=0)
cfg = COMMODITIES[sel_commodity]
rate_auto_initial = get_rate_inputs(sel_commodity)

use_manual = st.sidebar.checkbox("Use manual prices for ALL selected commodity rates", value=False)
manual_intl = st.sidebar.number_input(f"Manual international ({cfg['intl_unit']})", value=float(rate_auto_initial["auto_intl"] or 0), step=0.01, format="%.4f")
manual_mcx = st.sidebar.number_input(f"Manual MCX ({cfg['unit']})", value=float(rate_auto_initial["auto_mcx"] or 0), step=1.0, format="%.4f")
manual_dgcx = st.sidebar.number_input("Manual DGCX DINR", value=float(rate_auto_initial["auto_dgcx"] or 104.80), step=0.01, format="%.4f")
manual_usdinr = st.sidebar.number_input("Manual USDINR fallback", value=float(rate_auto_initial["auto_usdinr"] or 95.0), step=0.01, format="%.4f")

mcx_source_options = ["Auto best source", "Manual MCX", "Moneycontrol", "Groww", "Economic Times"]
mcx_source_choice = st.sidebar.selectbox("MCX rate source", mcx_source_options, index=0, help="No hard price range block. All source prices are shown; suspicious differences are only warned.")
rate_auto = get_rate_inputs(sel_commodity, mcx_source_choice, manual_mcx)

duty = st.sidebar.number_input("Duty %", value=float(cfg["default_duty"]), step=0.1)
charges = st.sidebar.number_input(f"Charges buffer ({cfg['unit']})", value=float(cfg["charges"]), step=1.0 if sel_commodity in ["Silver","Gold"] else 0.1)
slippage = st.sidebar.number_input(f"Slippage buffer ({cfg['unit']})", value=float(cfg["slippage"]), step=1.0 if sel_commodity in ["Silver","Gold"] else 0.1)

intl = manual_intl if use_manual else rate_auto["auto_intl"]
mcx = manual_mcx if use_manual else rate_auto["auto_mcx"]
dgcx = manual_dgcx if use_manual else rate_auto["auto_dgcx"]
rates = {"intl": intl, "mcx": mcx, "dgcx": dgcx}
snap = calc_snapshot(sel_commodity, intl, mcx, dgcx, manual_usdinr, duty, charges, slippage)
source_summary = f"Intl: {rate_auto['src_intl']} | MCX: {rate_auto['src_mcx']} | DGCX: {rate_auto['src_dgcx']} | USDINR: {rate_auto['src_usd']} | Manual mode: {use_manual}"
if store_badla and intl and mcx and snap.get("net_spread") is not None:
    # avoid too many duplicate rows by saving at most one per minute per selected commodity
    try:
        last = hist_df[hist_df.get("commodity")==sel_commodity].copy() if not hist_df.empty else pd.DataFrame()
        allow = True
        if not last.empty:
            last_ts = pd.to_datetime(last["ts"], utc=True, errors="coerce").max()
            allow = pd.isna(last_ts) or (pd.Timestamp.now(tz="UTC") - last_ts).total_seconds() > 55
        if allow:
            save_rate_snapshot(sel_commodity, rates, snap, source_summary)
    except Exception:
        pass

# ---------- Dashboard ----------
with menu[0]:
    col1,col2,col3 = st.columns(3)
    cash = total_cash()
    realized = realized_pnl_total()
    # open pnl for selected commodity rates only; rough, for all open trades if current commodity matches
    open_pnl=0.0
    if not trades_df.empty:
        open_trades = trades_df[trades_df.get("status") == "OPEN"].copy()
        for _,r in open_trades.iterrows():
            if r.get("commodity") == sel_commodity:
                open_pnl += pnl_for_trade(r, snap.get("net_spread"))
    with col1: st.metric("Paper cash", fmt_inr(cash,0))
    with col2: st.metric("Open P&L", fmt_inr(open_pnl,0))
    with col3: st.metric("Paper equity", fmt_inr(cash+open_pnl,0))

    st.markdown("### Selected commodity live view")
    c1,c2,c3,c4 = st.columns(4)
    c1.metric(f"{sel_commodity} international", fmt_num(intl,4), cfg['intl_unit'])
    c2.metric("MCX", fmt_inr(mcx,2) if sel_commodity in ["Silver","Gold"] else fmt_num(mcx,2), cfg['unit'])
    c3.metric("DGCX DINR", fmt_num(dgcx,4))
    c4.metric("Implied USDINR", fmt_num(snap.get("usdinr"),4))
    c5,c6,c7 = st.columns(3)
    c5.metric("Landed price", fmt_inr(snap.get("landed"),2) if sel_commodity in ["Silver","Gold"] else fmt_num(snap.get("landed"),2), cfg['unit'])
    c6.metric("Gross badla", fmt_inr(snap.get("gross_spread"),2) if sel_commodity in ["Silver","Gold"] else fmt_num(snap.get("gross_spread"),2), cfg['unit'])
    c7.metric("Net badla", fmt_inr(snap.get("net_spread"),2) if sel_commodity in ["Silver","Gold"] else fmt_num(snap.get("net_spread"),2), cfg['unit'])
    sig = zone_and_signal(sel_commodity, snap.get("net_spread"), hist_df)
    st.info(f"Signal: **{sig['signal']}** | Zone: **{sig['zone']}** | Quality: **{sig['score']}/100** | {sig['trade']} | {sig['reason']}")
    with st.expander("Source health / MCX source comparison"):
        st.write(source_summary)
        src_df = pd.DataFrame(rate_auto.get("mcx_sources", []))
        if not src_df.empty:
            show_cols = [c for c in ["source","price","status","note","diff_pct","url"] if c in src_df.columns]
            st.dataframe(src_df[show_cols], use_container_width=True, hide_index=True)
        st.caption("No hard price-range filter is used. Suspicious means the source differs strongly from other public sources; you can still select it from MCX rate source.")

# ---------- Scanner ----------
with menu[1]:
    st.subheader("Badla Scanner / Opportunity Dashboard")
    rows=[]
    for com, ccfg in COMMODITIES.items():
        ri = get_rate_inputs(com)
        intl_i = ri["auto_intl"]
        mcx_i = ri["auto_mcx"]
        dgcx_i = ri["auto_dgcx"] or manual_dgcx
        usd_i = ri["auto_usdinr"] or manual_usdinr
        sp = calc_snapshot(com, intl_i, mcx_i, dgcx_i, usd_i, ccfg["default_duty"], ccfg["charges"], ccfg["slippage"])
        sg = zone_and_signal(com, sp.get("net_spread"), hist_df)
        rows.append({
            "Commodity":com,"Intl":intl_i,"MCX":mcx_i,"DGCX":dgcx_i,"USDINR":sp.get("usdinr"),"Landed":sp.get("landed"),
            "Gross badla":sp.get("gross_spread"),"Net badla":sp.get("net_spread"),"Zone":sg["zone"],"Signal":sg["signal"],"Quality":sg["score"],"Trade":sg["trade"],"Reason":sg["reason"]
        })
    scan_df=pd.DataFrame(rows).sort_values("Quality", ascending=False)
    st.dataframe(scan_df, use_container_width=True, hide_index=True)
    best = scan_df.iloc[0]
    st.success(f"Best current opportunity: {best['Commodity']} | {best['Signal']} | Quality {best['Quality']}/100 | {best['Trade']}")

# ---------- Open Trade Auto ----------
with menu[2]:
    st.subheader("Open Trade — Live/Selected Rate Ticket")
    st.caption("Use current selected rates, but control COMEX/International, MCX and DGCX lots yourself. Suggested lots are shown only as guidance.")
    colA,colB=st.columns(2)
    with colA:
        direction = st.radio("Trade direction", ["MCX_HIGH", "MCX_LOW"], format_func=direction_label, horizontal=False)
        comex_lots = st.number_input("International/COMEX lots", min_value=0.0, value=float(cfg["suggested_comex_lots"]), step=1.0)
        suggested_mcx, suggested_dgcx, suggested_matched = suggested_lots(sel_commodity, comex_lots, intl, snap.get("usdinr"))
        mcx_lots_live = st.number_input("MCX lots", min_value=0.0, value=float(suggested_mcx), step=1.0, help="You can change this manually. It will not be forced to the suggestion.")
        dgcx_lots_live = st.number_input("DGCX lots", min_value=0.0, value=float(suggested_dgcx), step=1.0, help="You can change this manually. It will not be forced to the suggestion.")
        matched_qty = mcx_lots_live * cfg["mcx_lot_qty"]
    with colB:
        st.metric("Suggested MCX lots", suggested_mcx)
        st.metric("Suggested DGCX lots", suggested_dgcx)
        st.metric("Your matched quantity", f"{matched_qty:,.2f} kg")
        st.caption(f"Suggested matched qty: {suggested_matched:,.2f} kg")
    marg = estimate_margins(sel_commodity, intl, mcx, dgcx, snap.get("usdinr"), comex_lots, mcx_lots_live, dgcx_lots_live, direction)
    margin_card(marg)
    notes = st.text_area("Trade notes", value=f"Live/selected rate ticket. MCX source: {rate_auto.get('src_mcx')}. {source_summary}")
    if st.button("Open paper trade using current selected rates", type="primary"):
        if snap.get("net_spread") is None or matched_qty <= 0:
            st.error("Missing rates or matched quantity. Use manual prices or wait for data.")
        else:
            row={"trade_id":str(uuid.uuid4()),"opened_at":now_iso(),"closed_at":None,"status":"OPEN","commodity":sel_commodity,"direction":direction,"matched_qty_kg":matched_qty,"comex_lots":comex_lots,"mcx_lots":mcx_lots_live,"dgcx_lots":dgcx_lots_live,"entry_comex":intl,"entry_mcx":mcx,"entry_dgcx":dgcx,"entry_usdinr":snap.get("usdinr"),"entry_landed":snap.get("landed"),"entry_gross_spread":snap.get("gross_spread"),"entry_cost_per_kg":snap.get("cost_per_kg"),"entry_net_spread":snap.get("net_spread"),"exit_comex":None,"exit_mcx":None,"exit_dgcx":None,"exit_usdinr":None,"exit_landed":None,"exit_gross_spread":None,"exit_cost_per_kg":None,"exit_net_spread":None,"realized_pnl":None,"mae":0,"mfe":0,"notes":notes}
            store.insert("paper_trades", row)
            st.success("Paper trade opened with your lot sizes.")
            st.rerun()

# ---------- Manual Ticket ----------
with menu[3]:
    st.subheader("Manual Exact Trade Ticket")
    st.caption("Use this to enter the exact prices/lots from any badla screen. The app will not auto-change your lots.")
    c1,c2,c3=st.columns(3)
    with c1:
        man_com = st.selectbox("Commodity", list(COMMODITIES.keys()), key="manual_com")
        man_dir = st.radio("Spread type", ["MCX_HIGH", "MCX_LOW"], format_func=direction_label, key="manual_dir")
        man_duty = st.number_input("Duty %", value=float(COMMODITIES[man_com]["default_duty"]), key="manual_duty")
    with c2:
        man_intl_price = st.number_input(f"International price ({COMMODITIES[man_com]['intl_unit']})", value=0.0, step=0.01, key="manual_intl")
        man_intl_lots = st.number_input("International lots", value=1.0, step=1.0, key="manual_intl_lots")
        man_intl_action = st.selectbox("International action", ["BUY","SELL"], index=0 if man_dir=="MCX_HIGH" else 1)
    with c3:
        man_mcx_price = st.number_input(f"MCX price ({COMMODITIES[man_com]['unit']})", value=0.0, step=1.0, key="manual_mcx")
        man_mcx_lots = st.number_input("MCX lots", value=5.0 if man_com=="Silver" else 1.0, step=1.0, key="manual_mcx_lots")
        man_mcx_action = st.selectbox("MCX action", ["SELL","BUY"], index=0 if man_dir=="MCX_HIGH" else 1)
    d1,d2,d3=st.columns(3)
    with d1:
        man_dgcx_price = st.number_input("DGCX DINR price", value=104.81, step=0.01, key="manual_dgcx")
    with d2:
        man_dgcx_lots = st.number_input("DGCX lots", value=15.0 if man_com=="Silver" else 1.0, step=1.0, key="manual_dgcx_lots")
    with d3:
        man_dgcx_action = st.selectbox("DGCX action", ["SELL","BUY"], index=0 if man_dir=="MCX_HIGH" else 1)
    man_usd = dgcx_to_usdinr(man_dgcx_price)
    man_charges = st.number_input("Cost buffer per kg", value=float(COMMODITIES[man_com]["charges"]+COMMODITIES[man_com]["slippage"]), step=1.0 if man_com in ["Silver","Gold"] else 0.1)
    man_snap = calc_snapshot(man_com, man_intl_price or None, man_mcx_price or None, man_dgcx_price or None, man_usd, man_duty, man_charges, 0)
    matched_manual = man_mcx_lots * COMMODITIES[man_com]["mcx_lot_qty"]
    st.markdown(f"**Entry net badla:** {fmt_inr(man_snap.get('net_spread'),2) if man_com in ['Silver','Gold'] else fmt_num(man_snap.get('net_spread'),2)} | **Matched qty:** {matched_manual:,.2f} kg | **USDINR:** {fmt_num(man_snap.get('usdinr'),4)}")
    manual_marg = estimate_margins(man_com, man_intl_price, man_mcx_price, man_dgcx_price, man_snap.get('usdinr'), man_intl_lots, man_mcx_lots, man_dgcx_lots, man_dir)
    margin_card(manual_marg)
    man_notes = st.text_area("Manual ticket notes", value=f"Exact manual ticket: {man_intl_action} Intl {man_intl_lots} @ {man_intl_price}, {man_mcx_action} MCX {man_mcx_lots} @ {man_mcx_price}, {man_dgcx_action} DGCX {man_dgcx_lots} @ {man_dgcx_price}")
    if st.button("Open exact manual paper trade", type="primary"):
        if man_snap.get("net_spread") is None or matched_manual <= 0:
            st.error("Enter valid prices and lots first.")
        else:
            row={"trade_id":str(uuid.uuid4()),"opened_at":now_iso(),"closed_at":None,"status":"OPEN","commodity":man_com,"direction":man_dir+f" | {man_intl_action}_INTL/{man_mcx_action}_MCX/{man_dgcx_action}_DGCX","matched_qty_kg":matched_manual,"comex_lots":man_intl_lots,"mcx_lots":man_mcx_lots,"dgcx_lots":man_dgcx_lots,"entry_comex":man_intl_price,"entry_mcx":man_mcx_price,"entry_dgcx":man_dgcx_price,"entry_usdinr":man_snap.get("usdinr"),"entry_landed":man_snap.get("landed"),"entry_gross_spread":man_snap.get("gross_spread"),"entry_cost_per_kg":man_snap.get("cost_per_kg"),"entry_net_spread":man_snap.get("net_spread"),"exit_comex":None,"exit_mcx":None,"exit_dgcx":None,"exit_usdinr":None,"exit_landed":None,"exit_gross_spread":None,"exit_cost_per_kg":None,"exit_net_spread":None,"realized_pnl":None,"mae":0,"mfe":0,"notes":man_notes}
            store.insert("paper_trades", row)
            st.success("Exact manual paper trade opened.")
            st.rerun()

# ---------- Past Price Simulator ----------
with menu[4]:
    st.subheader("Past Price Trade Simulator")
    st.caption("Enter a past entry price and compare it with current live/manual selected rates. You can simulate only or add it as an open paper trade.")
    pcom = st.selectbox("Commodity", list(COMMODITIES.keys()), key="past_com")
    pcfg = COMMODITIES[pcom]
    pc1,pc2,pc3=st.columns(3)
    with pc1:
        past_dir = st.radio("Direction", ["MCX_HIGH","MCX_LOW"], format_func=direction_label, key="past_dir")
        past_date = st.text_input("Entry date/time note", value=datetime.now().strftime("%Y-%m-%d %H:%M"))
    with pc2:
        p_intl = st.number_input(f"Past international price ({pcfg['intl_unit']})", value=0.0, step=0.01, key="p_intl")
        p_mcx = st.number_input(f"Past MCX price ({pcfg['unit']})", value=0.0, step=1.0, key="p_mcx")
    with pc3:
        p_dgcx = st.number_input("Past DGCX DINR", value=104.81, step=0.01, key="p_dgcx")
        p_cost = st.number_input("Past cost buffer/kg", value=float(pcfg["charges"]+pcfg["slippage"]), step=1.0 if pcom in ["Silver","Gold"] else 0.1, key="p_cost")
    pl1,pl2,pl3=st.columns(3)
    with pl1: p_intl_lots=st.number_input("International lots", value=1.0, step=1.0, key="p_intl_lots")
    with pl2: p_mcx_lots=st.number_input("MCX lots", value=5.0 if pcom=="Silver" else 1.0, step=1.0, key="p_mcx_lots")
    with pl3: p_dgcx_lots=st.number_input("DGCX lots", value=15.0 if pcom=="Silver" else 1.0, step=1.0, key="p_dgcx_lots")
    p_entry = calc_snapshot(pcom, p_intl or None, p_mcx or None, p_dgcx or None, dgcx_to_usdinr(p_dgcx), pcfg["default_duty"], p_cost, 0)
    cur_rates = get_rate_inputs(pcom)
    cur_intl = cur_rates["auto_intl"] if cur_rates["auto_intl"] is not None else p_intl
    cur_mcx = cur_rates["auto_mcx"] if cur_rates["auto_mcx"] is not None else p_mcx
    cur_dgcx = cur_rates["auto_dgcx"] if cur_rates["auto_dgcx"] is not None else p_dgcx
    p_current = calc_snapshot(pcom, cur_intl, cur_mcx, cur_dgcx, cur_rates["auto_usdinr"] or dgcx_to_usdinr(cur_dgcx), pcfg["default_duty"], p_cost, 0)
    p_qty = p_mcx_lots * pcfg["mcx_lot_qty"]
    temp_row = {"direction": past_dir, "entry_net_spread": p_entry.get("net_spread"), "matched_qty_kg": p_qty}
    sim_pnl = pnl_for_trade(temp_row, p_current.get("net_spread")) if p_entry.get("net_spread") is not None else None
    a,b,c=st.columns(3)
    a.metric("Past entry net badla", fmt_inr(p_entry.get("net_spread"),2) if pcom in ["Silver","Gold"] else fmt_num(p_entry.get("net_spread"),2))
    b.metric("Current net badla", fmt_inr(p_current.get("net_spread"),2) if pcom in ["Silver","Gold"] else fmt_num(p_current.get("net_spread"),2))
    c.metric("Profit/Loss if opened then", fmt_inr(sim_pnl,0))
    past_marg = estimate_margins(pcom, p_intl, p_mcx, p_dgcx, p_entry.get('usdinr'), p_intl_lots, p_mcx_lots, p_dgcx_lots, past_dir)
    margin_card(past_marg, "Estimated margin if this past trade was opened")
    if st.button("Add this past price as open paper trade"):
        if p_entry.get("net_spread") is None:
            st.error("Past entry prices are incomplete.")
        else:
            row={"trade_id":str(uuid.uuid4()),"opened_at":now_iso(),"closed_at":None,"status":"OPEN","commodity":pcom,"direction":past_dir+" | BACKDATED_SIM","matched_qty_kg":p_qty,"comex_lots":p_intl_lots,"mcx_lots":p_mcx_lots,"dgcx_lots":p_dgcx_lots,"entry_comex":p_intl,"entry_mcx":p_mcx,"entry_dgcx":p_dgcx,"entry_usdinr":p_entry.get("usdinr"),"entry_landed":p_entry.get("landed"),"entry_gross_spread":p_entry.get("gross_spread"),"entry_cost_per_kg":p_entry.get("cost_per_kg"),"entry_net_spread":p_entry.get("net_spread"),"exit_comex":None,"exit_mcx":None,"exit_dgcx":None,"exit_usdinr":None,"exit_landed":None,"exit_gross_spread":None,"exit_cost_per_kg":None,"exit_net_spread":None,"realized_pnl":None,"mae":0,"mfe":0,"notes":f"Backdated paper trade. Past entry note: {past_date}. Sim P&L at creation: {sim_pnl}"}
            store.insert("paper_trades", row); st.success("Backdated paper trade added as open trade."); st.rerun()

# ---------- Trade Management ----------
with menu[5]:
    st.subheader("Trade Management")
    trades_df = store.read("paper_trades", TRADE_COLS)
    open_df = trades_df[trades_df.get("status") == "OPEN"].copy() if not trades_df.empty else pd.DataFrame(columns=TRADE_COLS)
    if open_df.empty:
        st.info("No open paper trades.")
    else:
        for _,r in open_df.iterrows():
            com = r.get("commodity")
            rr = get_rate_inputs(com)
            cur_intl = rr["auto_intl"] or r.get("entry_comex")
            cur_mcx = rr["auto_mcx"] or r.get("entry_mcx")
            cur_dgcx = rr["auto_dgcx"] or r.get("entry_dgcx")
            ccfg=COMMODITIES[com]
            cur_snap=calc_snapshot(com, cur_intl, cur_mcx, cur_dgcx, rr["auto_usdinr"] or dgcx_to_usdinr(cur_dgcx), ccfg["default_duty"], r.get("entry_cost_per_kg") or ccfg["charges"], 0)
            pnl=pnl_for_trade(r, cur_snap.get("net_spread"))
            with st.expander(f"{com} | {r.get('direction')} | Entry {fmt_inr(r.get('entry_net_spread'),2)} | Open P&L {fmt_inr(pnl,0)}", expanded=False):
                st.write(f"Trade ID: `{r.get('trade_id')}`")
                c1,c2,c3=st.columns(3)
                c1.metric("Entry net badla", fmt_inr(r.get("entry_net_spread"),2) if com in ["Silver","Gold"] else fmt_num(r.get("entry_net_spread"),2))
                c2.metric("Current net badla", fmt_inr(cur_snap.get("net_spread"),2) if com in ["Silver","Gold"] else fmt_num(cur_snap.get("net_spread"),2))
                c3.metric("Open P&L", fmt_inr(pnl,0))
                trade_marg = estimate_margins(com, cur_intl, cur_mcx, cur_dgcx, cur_snap.get('usdinr'), r.get('comex_lots'), r.get('mcx_lots'), r.get('dgcx_lots'), r.get('direction'))
                margin_card(trade_marg, "Estimated margin currently used by this paper trade")
                st.caption(str(r.get("notes")))
                st.markdown("**What-if exit / manual close**")
                wc1,wc2,wc3=st.columns(3)
                with wc1: ex_intl=st.number_input("Exit international", value=float(cur_intl or 0), key=f"ex_i_{r.get('trade_id')}")
                with wc2: ex_mcx=st.number_input("Exit MCX", value=float(cur_mcx or 0), key=f"ex_m_{r.get('trade_id')}")
                with wc3: ex_dgcx=st.number_input("Exit DGCX", value=float(cur_dgcx or 0), key=f"ex_d_{r.get('trade_id')}")
                ex_snap=calc_snapshot(com, ex_intl, ex_mcx, ex_dgcx, dgcx_to_usdinr(ex_dgcx), ccfg["default_duty"], r.get("entry_cost_per_kg") or 0, 0)
                ex_pnl=pnl_for_trade(r, ex_snap.get("net_spread"))
                st.metric("What-if exit P&L", fmt_inr(ex_pnl,0))
                close_col1,close_col2=st.columns(2)
                if close_col1.button("Close at live/current prices", key=f"close_live_{r.get('trade_id')}"):
                    updates={"closed_at":now_iso(),"status":"CLOSED","exit_comex":cur_intl,"exit_mcx":cur_mcx,"exit_dgcx":cur_dgcx,"exit_usdinr":cur_snap.get("usdinr"),"exit_landed":cur_snap.get("landed"),"exit_gross_spread":cur_snap.get("gross_spread"),"exit_cost_per_kg":cur_snap.get("cost_per_kg"),"exit_net_spread":cur_snap.get("net_spread"),"realized_pnl":pnl}
                    store.update("paper_trades","trade_id",r.get("trade_id"),updates)
                    store.insert("cash_ledger", {"id":str(uuid.uuid4()),"ts":now_iso(),"type":"REALIZED_PNL","amount":pnl,"note":f"Closed trade {r.get('trade_id')}"})
                    st.success("Closed at live/current prices."); st.rerun()
                if close_col2.button("Close at manual what-if prices", key=f"close_manual_{r.get('trade_id')}"):
                    updates={"closed_at":now_iso(),"status":"CLOSED","exit_comex":ex_intl,"exit_mcx":ex_mcx,"exit_dgcx":ex_dgcx,"exit_usdinr":ex_snap.get("usdinr"),"exit_landed":ex_snap.get("landed"),"exit_gross_spread":ex_snap.get("gross_spread"),"exit_cost_per_kg":ex_snap.get("cost_per_kg"),"exit_net_spread":ex_snap.get("net_spread"),"realized_pnl":ex_pnl}
                    store.update("paper_trades","trade_id",r.get("trade_id"),updates)
                    store.insert("cash_ledger", {"id":str(uuid.uuid4()),"ts":now_iso(),"type":"REALIZED_PNL","amount":ex_pnl,"note":f"Manual close trade {r.get('trade_id')}"})
                    st.success("Closed at manual prices."); st.rerun()

# ---------- History ----------
with menu[6]:
    st.subheader("History")
    if st.button("Refresh all history from storage"):
        st.cache_data.clear()
        st.rerun()
    htab1,htab2,htab3=st.tabs(["Trades","Rate history","Cash ledger"])
    with htab1:
        df=store.read("paper_trades", TRADE_COLS)
        st.dataframe(df.sort_values("opened_at", ascending=False) if not df.empty and "opened_at" in df else df, use_container_width=True)
        st.download_button("Download trades CSV", df.to_csv(index=False).encode(), "paper_trades.csv", "text/csv")
    with htab2:
        df=store.read("rate_history", RATE_COLS)
        if not df.empty:
            df2=df.copy(); df2["ts"]=pd.to_datetime(df2["ts"], errors="coerce")
            st.line_chart(df2.dropna(subset=["ts"]).set_index("ts")["net_spread"] if "net_spread" in df2 else pd.DataFrame())
        st.dataframe(df.sort_values("ts", ascending=False) if not df.empty and "ts" in df else df, use_container_width=True)
        st.download_button("Download rate history CSV", df.to_csv(index=False).encode(), "rate_history.csv", "text/csv")
    with htab3:
        df=store.read("cash_ledger", CASH_COLS)
        st.dataframe(df.sort_values("ts", ascending=False) if not df.empty and "ts" in df else df, use_container_width=True)
        st.download_button("Download cash ledger CSV", df.to_csv(index=False).encode(), "cash_ledger.csv", "text/csv")
    # backup zip
    mem=BytesIO()
    with zipfile.ZipFile(mem, "w") as z:
        z.writestr("paper_trades.csv", store.read("paper_trades", TRADE_COLS).to_csv(index=False))
        z.writestr("rate_history.csv", store.read("rate_history", RATE_COLS).to_csv(index=False))
        z.writestr("cash_ledger.csv", store.read("cash_ledger", CASH_COLS).to_csv(index=False))
    st.download_button("Download full backup ZIP", mem.getvalue(), f"esta_badla_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.zip", "application/zip")

# ---------- Settings ----------
with menu[7]:
    st.subheader("Settings / Paper Money")
    st.write(f"Storage mode: **{'Supabase online' if store.mode=='supabase' else 'Local CSV'}**")
    add_amt=st.number_input("Add paper money", min_value=0.0, value=0.0, step=10000.0)
    add_note=st.text_input("Note", value="Paper money added")
    if st.button("Add paper money") and add_amt>0:
        store.insert("cash_ledger", {"id":str(uuid.uuid4()),"ts":now_iso(),"type":"DEPOSIT","amount":add_amt,"note":add_note})
        st.success("Paper money added."); st.rerun()

    st.markdown("### Remove paper money")
    rem_amt=st.number_input("Remove paper money", min_value=0.0, value=0.0, step=10000.0)
    rem_note=st.text_input("Remove note", value="Paper money removed")
    if st.button("Remove paper money") and rem_amt>0:
        store.insert("cash_ledger", {"id":str(uuid.uuid4()),"ts":now_iso(),"type":"WITHDRAWAL","amount":-rem_amt,"note":rem_note})
        st.success("Paper money removed."); st.rerun()

    st.markdown("### Current rules")
    st.write("High badla = MCX expensive → Buy international/COMEX, Sell MCX, Sell DGCX hedge.")
    st.write("Low/negative badla = MCX cheap → Sell international/COMEX, Buy MCX, Buy/reverse DGCX hedge.")
    st.warning("This is a paper/demo terminal. Public data can be delayed or wrong. Do not use as live execution advice without broker-grade data and risk controls.")
