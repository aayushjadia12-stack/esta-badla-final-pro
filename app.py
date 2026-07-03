import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple, Any, List

import numpy as np
import pandas as pd
import plotly.express as px
import requests
import streamlit as st
import yfinance as yf
from bs4 import BeautifulSoup
from streamlit_autorefresh import st_autorefresh

try:
    from supabase import create_client, Client
except Exception:
    create_client = None
    Client = None

APP_TITLE = "ESTA Badla Final Pro"
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)
TRADES_CSV = DATA_DIR / "paper_trades.csv"
CASH_CSV = DATA_DIR / "cash_ledger.csv"
RATES_CSV = DATA_DIR / "rate_history.csv"

OZ_PER_KG = 32.15074656862798
DEFAULT_TIMEOUT = 8
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
}

COMMODITIES = {
    "Silver": {
        "yahoo": "SI=F",
        "mcx_manual": 234000.0,
        "comex_manual": 63.0,
        "duty": 15.5,
        "mcx_lot_kg": 30.0,
        "mcx_mini_lot_kg": 5.0,
        "comex_lot_oz": 5000.0,
        "dgcx_lot_inr": 2_000_000.0,
        "charges": 250.0,
        "slippage": 150.0,
        "target_profit_per_kg": 1500.0,
        "stop_loss_per_kg": 2500.0,
        "margin_note": "1 COMEX SI is large. Treat ₹75 lakh–₹1 crore+ as safer learning capital for full-size live structure.",
    },
    "Gold": {
        "yahoo": "GC=F",
        "mcx_manual": 128000.0,   # per 10g approx/manual
        "comex_manual": 4200.0,   # $/oz
        "duty": 6.0,
        "mcx_lot_kg": 1.0,
        "mcx_mini_lot_kg": 0.1,
        "comex_lot_oz": 100.0,
        "dgcx_lot_inr": 2_000_000.0,
        "charges": 80.0,          # per 10g display basis
        "slippage": 50.0,
        "target_profit_per_kg": 500.0,
        "stop_loss_per_kg": 1000.0,
        "margin_note": "Gold display uses MCX ₹/10g comparison. Check exact contract and conversion before live.",
    },
    "Copper": {
        "yahoo": "HG=F",          # $/lb
        "mcx_manual": 950.0,      # ₹/kg manual
        "comex_manual": 5.0,      # $/lb
        "duty": 5.0,
        "mcx_lot_kg": 2500.0,
        "mcx_mini_lot_kg": 250.0,
        "comex_lot_oz": 25000.0,  # actually lbs, kept in field for lot matching with custom converter
        "dgcx_lot_inr": 2_000_000.0,
        "charges": 2.0,
        "slippage": 1.5,
        "target_profit_per_kg": 8.0,
        "stop_loss_per_kg": 12.0,
        "margin_note": "Copper COMEX HG is $/lb. App converts lb to kg. Manual MCX recommended until broker API.",
    },
    "Zinc": {
        "yahoo": None,
        "mcx_manual": 290.0,
        "comex_manual": 2900.0,   # $/MT placeholder/manual global
        "duty": 5.0,
        "mcx_lot_kg": 5000.0,
        "mcx_mini_lot_kg": 1000.0,
        "comex_lot_oz": 25000.0,
        "dgcx_lot_inr": 2_000_000.0,
        "charges": 1.5,
        "slippage": 1.0,
        "target_profit_per_kg": 5.0,
        "stop_loss_per_kg": 8.0,
        "margin_note": "Zinc uses manual global $/MT and MCX ₹/kg for demo.",
    },
    "Aluminium": {
        "yahoo": None,
        "mcx_manual": 270.0,
        "comex_manual": 2700.0,   # $/MT placeholder/manual global
        "duty": 5.0,
        "mcx_lot_kg": 5000.0,
        "mcx_mini_lot_kg": 1000.0,
        "comex_lot_oz": 25000.0,
        "dgcx_lot_inr": 2_000_000.0,
        "charges": 1.5,
        "slippage": 1.0,
        "target_profit_per_kg": 5.0,
        "stop_loss_per_kg": 8.0,
        "margin_note": "Aluminium uses manual global $/MT and MCX ₹/kg for demo.",
    },
}

st.set_page_config(page_title=APP_TITLE, page_icon="📈", layout="wide", initial_sidebar_state="expanded")

CUSTOM_CSS = """
<style>
.block-container {padding-top: 1rem; padding-bottom: 2rem;}
[data-testid="stMetricValue"] {font-size: 1.45rem;}
.small-note {font-size:0.86rem; color: #6b7280;}
.good {color:#0a7f3f; font-weight:700;}
.bad {color:#a91e1e; font-weight:700;}
.warn {color:#a66a00; font-weight:700;}
.card {border:1px solid rgba(128,128,128,0.25); border-radius:14px; padding:14px; margin:6px 0;}
@media (max-width: 768px) {
  [data-testid="stMetricValue"] {font-size: 1.15rem;}
  .block-container {padding-left: 0.75rem; padding-right: 0.75rem;}
}
</style>
"""
st.markdown(CUSTOM_CSS, unsafe_allow_html=True)

# ---------- Utility ----------
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def safe_float(x, default=np.nan):
    try:
        if x is None:
            return default
        if isinstance(x, str):
            x = x.replace(",", "").strip()
        return float(x)
    except Exception:
        return default

def format_inr(x, decimals=0):
    if pd.isna(x):
        return "—"
    return f"₹{x:,.{decimals}f}"

def format_usd(x, decimals=3):
    if pd.isna(x):
        return "—"
    return f"${x:,.{decimals}f}"

@st.cache_data(ttl=12, show_spinner=False)
def fetch_yahoo_last(symbol: str) -> Tuple[float, str]:
    if not symbol:
        return np.nan, "No symbol"
    try:
        t = yf.Ticker(symbol)
        fast = getattr(t, "fast_info", {}) or {}
        price = fast.get("last_price") or fast.get("lastPrice")
        if price:
            return float(price), f"Yahoo {symbol} fast_info"
        hist = t.history(period="1d", interval="1m")
        if not hist.empty:
            return float(hist["Close"].dropna().iloc[-1]), f"Yahoo {symbol} 1m"
    except Exception as e:
        return np.nan, f"Yahoo failed: {str(e)[:80]}"
    return np.nan, "Yahoo no data"

@st.cache_data(ttl=20, show_spinner=False)
def fetch_url_text(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=DEFAULT_TIMEOUT)
    r.raise_for_status()
    return r.text

def extract_numbers_near(text: str, keywords: List[str], min_val: float, max_val: float) -> Tuple[float, str]:
    clean = re.sub(r"\s+", " ", text)
    best = []
    for kw in keywords:
        for m in re.finditer(re.escape(kw), clean, flags=re.I):
            start = max(0, m.start() - 500)
            end = min(len(clean), m.end() + 700)
            chunk = clean[start:end]
            nums = re.findall(r"(?<![A-Za-z])\d{1,3}(?:,\d{2,3})*(?:\.\d+)?|\d+(?:\.\d+)?", chunk)
            for num in nums:
                val = safe_float(num)
                if min_val <= val <= max_val:
                    best.append((val, kw))
    if best:
        return best[0][0], f"matched near {best[0][1]}"
    return np.nan, "not found"

@st.cache_data(ttl=20, show_spinner=False)
def fetch_mcx_silver_public() -> Tuple[float, str]:
    sources = []
    urls = [
        ("Groww MCX Silver", "https://groww.in/commodities/futures/mcx_silver"),
        ("Economic Times Silver", "https://economictimes.indiatimes.com/commoditysummary/symbol-SILVER.cms"),
    ]
    for name, url in urls:
        try:
            html = fetch_url_text(url)
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(" ")
            val, reason = extract_numbers_near(text, ["Silver", "SILVER", "Last", "Price", "MCX"], 50000, 400000)
            if not pd.isna(val):
                return val, f"{name} scrape ({reason})"
            sources.append(f"{name}: no number")
        except Exception as e:
            sources.append(f"{name}: {str(e)[:60]}")
    return np.nan, "; ".join(sources)

@st.cache_data(ttl=20, show_spinner=False)
def fetch_dgcx_dinr_public() -> Tuple[float, str]:
    urls = [
        ("Barchart DGCX INR", "https://www.barchart.com/futures/quotes/INR*0/futures-prices"),
        ("DGCX product", "https://www.dgcx.ae/products/inr-usd-futures"),
    ]
    for name, url in urls:
        try:
            html = fetch_url_text(url)
            text = BeautifulSoup(html, "html.parser").get_text(" ")
            val, reason = extract_numbers_near(text, ["Last", "INR", "Indian Rupee", "DINR"], 80, 130)
            if not pd.isna(val):
                return val, f"{name} scrape ({reason})"
        except Exception as e:
            continue
    return np.nan, "DGCX public scrape failed; use manual/broker rate"

@st.cache_data(ttl=20, show_spinner=False)
def fetch_mcx_gold_public() -> Tuple[float, str]:
    urls = [
        ("Groww MCX Gold", "https://groww.in/commodities/futures/mcx_gold"),
        ("Economic Times Gold", "https://economictimes.indiatimes.com/commoditysummary/symbol-GOLD.cms"),
    ]
    for name, url in urls:
        try:
            text = BeautifulSoup(fetch_url_text(url), "html.parser").get_text(" ")
            val, reason = extract_numbers_near(text, ["Gold", "GOLD", "Last", "Price", "MCX"], 50000, 200000)
            if not pd.isna(val):
                return val, f"{name} scrape ({reason})"
        except Exception:
            pass
    return np.nan, "Gold public scrape failed"

# ---------- Conversion ----------
def dgcx_to_usdinr(dgcx_quote: float) -> float:
    # DINR quoted as US cents per ₹100. USDINR = 100 / (quote / 100)
    if pd.isna(dgcx_quote) or dgcx_quote <= 0:
        return np.nan
    return 100.0 / (dgcx_quote / 100.0)

def convert_landed(commodity: str, comex: float, usdinr: float, duty_pct: float) -> float:
    if pd.isna(comex) or pd.isna(usdinr):
        return np.nan
    duty_mult = 1 + duty_pct / 100.0
    if commodity in ["Silver", "Gold"]:
        # COMEX $/oz -> INR/kg. For Gold display, later app compares per kg internally.
        return comex * usdinr * OZ_PER_KG * duty_mult
    if commodity == "Copper":
        # COMEX HG $/lb -> INR/kg. 1 kg = 2.20462262185 lb
        return comex * usdinr * 2.20462262185 * duty_mult
    if commodity in ["Zinc", "Aluminium"]:
        # manual global $/metric ton -> INR/kg
        return (comex * usdinr / 1000.0) * duty_mult
    return np.nan

def normalize_mcx_for_compare(commodity: str, mcx_price: float) -> float:
    # Silver, copper, zinc, aluminium are ₹/kg. Gold manual/fetched MCX usually ₹/10g; convert to ₹/kg.
    if pd.isna(mcx_price):
        return np.nan
    if commodity == "Gold":
        return mcx_price * 100.0
    return mcx_price

def denormalize_for_display(commodity: str, price_per_kg: float) -> float:
    if pd.isna(price_per_kg): return np.nan
    if commodity == "Gold":
        return price_per_kg / 100.0
    return price_per_kg

def suggest_lots(commodity: str, comex_lots: float, cfg: Dict[str, Any], landed_per_kg: float) -> Dict[str, float]:
    if commodity == "Copper":
        comex_qty_kg = comex_lots * cfg["comex_lot_oz"] * 0.45359237  # lbs to kg
    elif commodity in ["Zinc", "Aluminium"]:
        # placeholder assumes manual global exchange lot mapped roughly as cfg comex_lot_oz kg
        comex_qty_kg = comex_lots * cfg["comex_lot_oz"]
    else:
        comex_qty_kg = comex_lots * cfg["comex_lot_oz"] / OZ_PER_KG
    mcx_lots = math.floor(comex_qty_kg / cfg["mcx_lot_kg"])
    remainder = comex_qty_kg - mcx_lots * cfg["mcx_lot_kg"]
    mcx_mini_lots = round(remainder / cfg["mcx_mini_lot_kg"]) if cfg.get("mcx_mini_lot_kg", 0) else 0
    matched_qty = mcx_lots * cfg["mcx_lot_kg"] + mcx_mini_lots * cfg["mcx_mini_lot_kg"]
    exposure_inr = matched_qty * landed_per_kg if not pd.isna(landed_per_kg) else 0
    dgcx_lots = round(exposure_inr / cfg["dgcx_lot_inr"]) if cfg.get("dgcx_lot_inr") else 0
    return {
        "comex_qty_kg": comex_qty_kg,
        "mcx_lots": mcx_lots,
        "mcx_mini_lots": mcx_mini_lots,
        "matched_qty_kg": matched_qty,
        "mismatch_kg": matched_qty - comex_qty_kg,
        "dgcx_lots": dgcx_lots,
        "exposure_inr": exposure_inr,
    }

# ---------- Storage ----------
def read_local_csv(path: Path, columns: List[str]) -> pd.DataFrame:
    if path.exists():
        try:
            return pd.read_csv(path)
        except Exception:
            return pd.DataFrame(columns=columns)
    return pd.DataFrame(columns=columns)

def append_local(path: Path, row: Dict[str, Any], columns: List[str]):
    df = read_local_csv(path, columns)
    df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
    df.to_csv(path, index=False)

def overwrite_local(path: Path, df: pd.DataFrame):
    df.to_csv(path, index=False)

TRADE_COLS = ["trade_id","opened_at","closed_at","status","commodity","direction","matched_qty_kg","comex_lots","mcx_lots","dgcx_lots","entry_comex","entry_mcx","entry_dgcx","entry_usdinr","entry_landed","entry_gross_spread","entry_cost_per_kg","entry_net_spread","exit_comex","exit_mcx","exit_dgcx","exit_usdinr","exit_landed","exit_gross_spread","exit_cost_per_kg","exit_net_spread","realized_pnl","mae","mfe","notes"]
CASH_COLS = ["id","ts","type","amount","note"]
RATE_COLS = ["id","ts","commodity","comex","mcx","dgcx","usdinr","landed","gross_spread","cost_per_kg","net_spread","source_summary"]

class Store:
    def __init__(self):
        self.mode = "CSV"
        self.client = None
        url = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL", "")) if hasattr(st, "secrets") else os.getenv("SUPABASE_URL", "")
        key = st.secrets.get("SUPABASE_KEY", os.getenv("SUPABASE_KEY", "")) if hasattr(st, "secrets") else os.getenv("SUPABASE_KEY", "")
        if create_client and url and key:
            try:
                self.client = create_client(url, key)
                self.mode = "Supabase"
            except Exception:
                self.client = None
                self.mode = "CSV"

    def table(self, name):
        return self.client.table(name)

    def read_trades(self):
        if self.client:
            try:
                return pd.DataFrame(self.table("paper_trades").select("*").order("opened_at", desc=True).execute().data)
            except Exception as e:
                st.warning(f"Supabase trades read failed, using CSV fallback: {e}")
        return read_local_csv(TRADES_CSV, TRADE_COLS)

    def read_cash(self):
        if self.client:
            try:
                return pd.DataFrame(self.table("cash_ledger").select("*").order("ts", desc=False).execute().data)
            except Exception as e:
                st.warning(f"Supabase cash read failed, using CSV fallback: {e}")
        return read_local_csv(CASH_CSV, CASH_COLS)

    def read_rates(self, limit=1000):
        if self.client:
            try:
                return pd.DataFrame(self.table("rate_history").select("*").order("ts", desc=True).limit(limit).execute().data)
            except Exception as e:
                st.warning(f"Supabase rates read failed, using CSV fallback: {e}")
        df = read_local_csv(RATES_CSV, RATE_COLS)
        if not df.empty:
            return df.tail(limit).iloc[::-1]
        return df

    def add_cash(self, amount, note, typ="deposit"):
        row = {"id": str(uuid.uuid4()), "ts": now_iso(), "type": typ, "amount": float(amount), "note": note}
        if self.client:
            try:
                self.table("cash_ledger").insert(row).execute(); return
            except Exception as e:
                st.error(f"Supabase cash insert failed: {e}")
        append_local(CASH_CSV, row, CASH_COLS)

    def add_trade(self, row):
        if self.client:
            try:
                self.table("paper_trades").insert(row).execute(); return
            except Exception as e:
                st.error(f"Supabase trade insert failed: {e}")
        append_local(TRADES_CSV, row, TRADE_COLS)

    def update_trade_close(self, trade_id, updates):
        if self.client:
            try:
                self.table("paper_trades").update(updates).eq("trade_id", trade_id).execute(); return
            except Exception as e:
                st.error(f"Supabase close update failed: {e}")
        df = read_local_csv(TRADES_CSV, TRADE_COLS)
        for k, v in updates.items():
            df.loc[df["trade_id"] == trade_id, k] = v
        overwrite_local(TRADES_CSV, df)

    def add_rate(self, row):
        if self.client:
            try:
                self.table("rate_history").insert(row).execute(); return
            except Exception:
                pass
        append_local(RATES_CSV, row, RATE_COLS)

store = Store()

# ---------- Sidebar ----------
st.sidebar.title("⚙️ Control")
auto_refresh = st.sidebar.toggle("Auto refresh", value=True)
refresh_sec = st.sidebar.select_slider("Refresh seconds", options=[10, 15, 20, 30, 60], value=15)
if auto_refresh:
    st_autorefresh(interval=refresh_sec * 1000, key="auto_refresh")
st.sidebar.caption(f"Storage: **{store.mode}**")
st.sidebar.caption("For months of mobile tracking, use Streamlit Cloud + Supabase.")

commodity = st.sidebar.selectbox("Commodity", list(COMMODITIES.keys()), index=0)
cfg = COMMODITIES[commodity]

st.sidebar.subheader("Manual fallback rates")
manual_comex = st.sidebar.number_input("Manual COMEX/global price", value=float(cfg["comex_manual"]), step=0.01, format="%.5f")
manual_mcx_display = st.sidebar.number_input("Manual MCX price", value=float(cfg["mcx_manual"]), step=1.0, format="%.5f")
manual_dgcx = st.sidebar.number_input("Manual DGCX DINR", value=104.81, step=0.01, format="%.5f")
manual_usdinr = st.sidebar.number_input("Manual USDINR", value=95.41, step=0.01, format="%.5f")

duty = st.sidebar.number_input("Duty %", value=float(cfg["duty"]), step=0.1)
charges = st.sidebar.number_input("Charges buffer per kg", value=float(cfg["charges"]), step=1.0)
slippage = st.sidebar.number_input("Slippage buffer per kg", value=float(cfg["slippage"]), step=1.0)
cost_per_kg = charges + slippage

prefer_dgcx = st.sidebar.toggle("Use DGCX for USDINR hedge", value=True)
log_rates = st.sidebar.toggle("Save rate snapshot", value=True)

# ---------- Header ----------
st.title("📱 ESTA Badla Final Pro")
st.caption("Read-only live/demo rates + real-like paper trading. No live orders are placed.")

# ---------- Fetch rates ----------
with st.spinner("Fetching latest rates..."):
    y_comex, src_comex = fetch_yahoo_last(cfg["yahoo"]) if cfg.get("yahoo") else (np.nan, "No Yahoo symbol")
    y_usdinr, src_usdinr = fetch_yahoo_last("INR=X")
    if commodity == "Silver":
        auto_mcx, src_mcx = fetch_mcx_silver_public()
    elif commodity == "Gold":
        auto_mcx, src_mcx = fetch_mcx_gold_public()
    else:
        auto_mcx, src_mcx = np.nan, "Manual recommended for this commodity"
    auto_dgcx, src_dgcx = fetch_dgcx_dinr_public()

comex = y_comex if not pd.isna(y_comex) else manual_comex
mcx_display = auto_mcx if not pd.isna(auto_mcx) else manual_mcx_display
dgcx = auto_dgcx if not pd.isna(auto_dgcx) else manual_dgcx
usd_from_dgcx = dgcx_to_usdinr(dgcx)
usdinr = usd_from_dgcx if prefer_dgcx and not pd.isna(usd_from_dgcx) else (y_usdinr if not pd.isna(y_usdinr) else manual_usdinr)

mcx_per_kg = normalize_mcx_for_compare(commodity, mcx_display)
landed = convert_landed(commodity, comex, usdinr, duty)
gross_spread = mcx_per_kg - landed if not pd.isna(mcx_per_kg) and not pd.isna(landed) else np.nan
net_spread = gross_spread - cost_per_kg if not pd.isna(gross_spread) else np.nan

source_summary = json.dumps({
    "comex": src_comex, "mcx": src_mcx, "dgcx": src_dgcx, "usdinr": src_usdinr,
    "selected_usdinr_source": "DGCX implied" if prefer_dgcx else "Yahoo/manual"
})

if log_rates and not pd.isna(gross_spread):
    # Avoid too many duplicate rows on rerun: log every actual refresh/run; okay for paper history.
    store.add_rate({
        "id": str(uuid.uuid4()), "ts": now_iso(), "commodity": commodity, "comex": float(comex), "mcx": float(mcx_display),
        "dgcx": float(dgcx), "usdinr": float(usdinr), "landed": float(landed), "gross_spread": float(gross_spread),
        "cost_per_kg": float(cost_per_kg), "net_spread": float(net_spread), "source_summary": source_summary
    })

# ---------- Metrics ----------
cols = st.columns(2)
with cols[0]:
    st.metric("COMEX/global", format_usd(comex, 4) if commodity in ["Silver", "Gold", "Copper"] else format_usd(comex, 2))
    st.caption(src_comex)
with cols[1]:
    display_unit = "₹/10g" if commodity == "Gold" else "₹/kg"
    st.metric(f"MCX ({display_unit})", format_inr(mcx_display, 2 if commodity != "Silver" else 0))
    st.caption(src_mcx)

cols = st.columns(2)
with cols[0]:
    st.metric("DGCX DINR", f"{dgcx:,.4f}" if not pd.isna(dgcx) else "—")
    st.caption(src_dgcx)
with cols[1]:
    st.metric("Implied USDINR", f"{usdinr:,.4f}" if not pd.isna(usdinr) else "—")
    st.caption("DGCX implied" if prefer_dgcx else src_usdinr)

cols = st.columns(3)
with cols[0]:
    landed_display = denormalize_for_display(commodity, landed)
    st.metric("COMEX landed", format_inr(landed_display, 2 if commodity != "Silver" else 0))
with cols[1]:
    spread_display = denormalize_for_display(commodity, gross_spread)
    st.metric("Gross spread", format_inr(spread_display, 2 if commodity != "Silver" else 0))
with cols[2]:
    net_display = denormalize_for_display(commodity, net_spread)
    st.metric("Net after buffer", format_inr(net_display, 2 if commodity != "Silver" else 0))

if pd.isna(gross_spread):
    st.error("Not enough data. Use manual rates in the sidebar.")
elif gross_spread > cost_per_kg:
    st.success("Direction signal: MCX is higher → SELL MCX + BUY COMEX/global + hedge DGCX/INR.")
elif gross_spread < -cost_per_kg:
    st.info("Direction signal: MCX is lower → BUY MCX + SELL COMEX/global + reverse hedge.")
else:
    st.warning("No clean trade: spread is inside buffer zone.")

# ---------- Tabs ----------
tab_trade, tab_journal, tab_chart, tab_settings = st.tabs(["📌 Paper trade", "📒 Journal", "📊 History", "🛠 Setup"])

with tab_trade:
    st.subheader("Paper money")
    cash_df = store.read_cash()
    trades_df = store.read_trades()
    deposits = cash_df["amount"].astype(float).sum() if not cash_df.empty and "amount" in cash_df else 0.0
    realized = 0.0
    if not trades_df.empty and "realized_pnl" in trades_df:
        realized = pd.to_numeric(trades_df.get("realized_pnl"), errors="coerce").fillna(0).sum()
    paper_cash = deposits + realized

    open_df = trades_df[trades_df["status"].astype(str).str.upper() == "OPEN"] if not trades_df.empty and "status" in trades_df else pd.DataFrame(columns=TRADE_COLS)

    def calc_open_pnl(row) -> float:
        qty = safe_float(row.get("matched_qty_kg"), 0)
        entry_g = safe_float(row.get("entry_gross_spread"), 0)
        direction = row.get("direction", "SELL_MCX_BUY_COMEX")
        if direction == "SELL_MCX_BUY_COMEX":
            return (entry_g - gross_spread) * qty - cost_per_kg * qty if not pd.isna(gross_spread) else 0
        return (gross_spread - entry_g) * qty - cost_per_kg * qty if not pd.isna(gross_spread) else 0

    open_pnl = open_df.apply(calc_open_pnl, axis=1).sum() if not open_df.empty else 0.0
    equity = paper_cash + open_pnl
    c1, c2, c3 = st.columns(3)
    c1.metric("Paper cash", format_inr(paper_cash, 0))
    c2.metric("Open P&L", format_inr(open_pnl, 0))
    c3.metric("Paper equity", format_inr(equity, 0))

    with st.expander("Add paper money", expanded=paper_cash <= 0):
        add_amt = st.number_input("Amount to add", min_value=0.0, value=10000000.0, step=100000.0)
        note = st.text_input("Note", value="Paper capital")
        if st.button("Add paper money"):
            store.add_cash(add_amt, note, "deposit")
            st.success("Paper money added.")
            st.rerun()

    st.subheader("Lot matching")
    default_comex_lots = 1.0 if commodity in ["Silver", "Gold", "Copper"] else 0.1
    comex_lots = st.number_input("COMEX/global lots", min_value=0.0, value=default_comex_lots, step=0.1)
    lot_info = suggest_lots(commodity, comex_lots, cfg, landed)
    lc = st.columns(4)
    lc[0].metric("COMEX qty kg", f"{lot_info['comex_qty_kg']:,.2f}")
    lc[1].metric("MCX lots", f"{lot_info['mcx_lots']} + mini {lot_info['mcx_mini_lots']}")
    lc[2].metric("Matched kg", f"{lot_info['matched_qty_kg']:,.2f}")
    lc[3].metric("DGCX hedge lots", f"{lot_info['dgcx_lots']}")
    st.caption(cfg["margin_note"])

    st.subheader("Open new paper spread")
    direction_options = {
        "MCX high: SELL MCX + BUY COMEX + SELL/hedge DGCX": "SELL_MCX_BUY_COMEX",
        "MCX low: BUY MCX + SELL COMEX + reverse hedge": "BUY_MCX_SELL_COMEX",
    }
    default_idx = 0 if gross_spread >= 0 else 1
    direction_label = st.selectbox("Direction", list(direction_options.keys()), index=default_idx)
    direction = direction_options[direction_label]
    notes = st.text_area("Trade notes", value="Demo paper trade")
    target_per_kg = st.number_input("Target profit per kg", value=float(cfg["target_profit_per_kg"]), step=10.0)
    stop_per_kg = st.number_input("Max adverse per kg alert", value=float(cfg["stop_loss_per_kg"]), step=10.0)
    gross_expected = lot_info["matched_qty_kg"] * abs(net_spread) if not pd.isna(net_spread) else 0
    st.info(f"Current notional spread on matched quantity: {format_inr(gross_expected, 0)} before real broker/exchange charges.")

    if st.button("OPEN PAPER TRADE", type="primary", disabled=pd.isna(gross_spread) or lot_info["matched_qty_kg"] <= 0):
        row = {c: None for c in TRADE_COLS}
        row.update({
            "trade_id": str(uuid.uuid4()), "opened_at": now_iso(), "closed_at": None, "status": "OPEN",
            "commodity": commodity, "direction": direction, "matched_qty_kg": float(lot_info["matched_qty_kg"]),
            "comex_lots": float(comex_lots), "mcx_lots": float(lot_info["mcx_lots"] + lot_info["mcx_mini_lots"]), "dgcx_lots": float(lot_info["dgcx_lots"]),
            "entry_comex": float(comex), "entry_mcx": float(mcx_display), "entry_dgcx": float(dgcx), "entry_usdinr": float(usdinr),
            "entry_landed": float(landed), "entry_gross_spread": float(gross_spread), "entry_cost_per_kg": float(cost_per_kg), "entry_net_spread": float(net_spread),
            "realized_pnl": 0.0, "mae": 0.0, "mfe": 0.0, "notes": notes + f" | target/kg={target_per_kg}, stop/kg={stop_per_kg}",
        })
        store.add_trade(row)
        st.success("Paper trade opened.")
        st.rerun()

    st.subheader("Open trades")
    if open_df.empty:
        st.caption("No open paper trades.")
    else:
        for _, row in open_df.iterrows():
            pnl = calc_open_pnl(row)
            qty = safe_float(row.get("matched_qty_kg"), 0)
            pnl_per_kg = pnl / qty if qty else 0
            trade_id = row.get("trade_id")
            with st.container(border=True):
                st.write(f"**{row.get('commodity')} | {row.get('direction')}**")
                st.caption(f"Opened: {row.get('opened_at')} | ID: {str(trade_id)[:8]}")
                cc = st.columns(4)
                cc[0].metric("Entry spread", format_inr(safe_float(row.get("entry_gross_spread")), 2))
                cc[1].metric("Current spread", format_inr(gross_spread, 2))
                cc[2].metric("P&L", format_inr(pnl, 0))
                cc[3].metric("P&L/kg", format_inr(pnl_per_kg, 2))
                if st.button(f"Close trade {str(trade_id)[:8]}", key=f"close_{trade_id}"):
                    updates = {
                        "closed_at": now_iso(), "status": "CLOSED", "exit_comex": float(comex), "exit_mcx": float(mcx_display),
                        "exit_dgcx": float(dgcx), "exit_usdinr": float(usdinr), "exit_landed": float(landed),
                        "exit_gross_spread": float(gross_spread), "exit_cost_per_kg": float(cost_per_kg), "exit_net_spread": float(net_spread),
                        "realized_pnl": float(pnl)
                    }
                    store.update_trade_close(trade_id, updates)
                    st.success("Trade closed and P&L realized.")
                    st.rerun()

with tab_journal:
    st.subheader("Trade journal")
    trades = store.read_trades()
    if trades.empty:
        st.caption("No trades yet.")
    else:
        st.dataframe(trades, use_container_width=True)
        st.download_button("Download trades CSV", trades.to_csv(index=False).encode(), "paper_trades.csv", "text/csv")
    st.subheader("Cash ledger")
    cash = store.read_cash()
    st.dataframe(cash, use_container_width=True)
    if not cash.empty:
        st.download_button("Download cash CSV", cash.to_csv(index=False).encode(), "cash_ledger.csv", "text/csv")

with tab_chart:
    st.subheader("Rate and spread history")
    rates = store.read_rates(limit=2000)
    if rates.empty:
        st.caption("No rate history yet. Keep Save rate snapshot ON.")
    else:
        rates["ts"] = pd.to_datetime(rates["ts"], errors="coerce")
        filt = rates[rates["commodity"] == commodity].sort_values("ts")
        if filt.empty:
            st.caption("No history for selected commodity yet.")
        else:
            fig = px.line(filt, x="ts", y=["gross_spread", "net_spread"], title=f"{commodity} spread history")
            st.plotly_chart(fig, use_container_width=True)
            st.dataframe(filt.tail(200), use_container_width=True)
            st.download_button("Download rate history CSV", rates.to_csv(index=False).encode(), "rate_history.csv", "text/csv")

with tab_settings:
    st.subheader("Source health")
    health = pd.DataFrame([
        {"Rate": "COMEX/global", "Value": comex, "Source": src_comex, "Auto OK": not pd.isna(y_comex)},
        {"Rate": "MCX", "Value": mcx_display, "Source": src_mcx, "Auto OK": not pd.isna(auto_mcx)},
        {"Rate": "DGCX DINR", "Value": dgcx, "Source": src_dgcx, "Auto OK": not pd.isna(auto_dgcx)},
        {"Rate": "USDINR Yahoo", "Value": y_usdinr, "Source": src_usdinr, "Auto OK": not pd.isna(y_usdinr)},
    ])
    st.dataframe(health, use_container_width=True)
    st.subheader("Supabase setup")
    if store.mode == "Supabase":
        st.success("Supabase connected. Your data should persist even after Streamlit restarts.")
    else:
        st.warning("Currently using local CSV. On Streamlit Cloud, local CSV can reset. Add Supabase secrets for months-long tracking.")
        st.code('SUPABASE_URL="https://YOUR_PROJECT.supabase.co"\nSUPABASE_KEY="YOUR_ANON_KEY"', language="toml")
    st.subheader("Backup")
    import zipfile, io
    mem = io.BytesIO()
    with zipfile.ZipFile(mem, "w", zipfile.ZIP_DEFLATED) as z:
        for p in [TRADES_CSV, CASH_CSV, RATES_CSV]:
            if p.exists(): z.write(p, p.name)
    st.download_button("Download full CSV backup ZIP", mem.getvalue(), "esta_badla_backup.zip", "application/zip")

st.caption("Demo/paper system only. It may use delayed or scraped public rates. Verify all rates with broker terminals before real trading.")
