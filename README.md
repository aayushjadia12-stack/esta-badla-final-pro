# ESTA Badla Signal Pro v2

Mobile-first paper badla trading terminal for MCX vs international commodity spreads.

This version adds:
- Moneycontrol/Groww/ET MCX source comparison
- No hard price-range blocking; suspicious sources are shown as warnings only
- Manual MCX source selector and manual override
- Open Trade screen with manual lot input for COMEX/International, MCX and DGCX
- Manual exact ticket with exact prices and lots
- Past-price simulator with exact lots
- Trade management with what-if exit and manual close
- Estimated margin used by COMEX, MCX and DGCX legs
- Add paper money and remove paper money
- Refresh all history from storage
- Supabase online saving for months of paper trading

## Run locally
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Cloud update
Upload/replace these 3 files in your existing GitHub repository:

```text
app.py
requirements.txt
README.md
```

Do not upload:

```text
venv
.streamlit
secrets.toml
CSV files
__pycache__
```

Your existing Supabase data will remain safe because the database tables are unchanged.

## Streamlit Cloud secrets
Add these in Streamlit Cloud > App > Settings > Secrets:

```toml
SUPABASE_URL="https://YOUR_PROJECT.supabase.co"
SUPABASE_KEY="YOUR_ANON_PUBLIC_KEY"
```

## Supabase SQL setup
Run this in Supabase SQL Editor if you have not created the tables yet. If the tables already exist, it is safe to run again.

```sql
create table if not exists cash_ledger (
  id text primary key,
  ts timestamptz not null,
  type text not null,
  amount numeric not null,
  note text
);

create table if not exists paper_trades (
  trade_id text primary key,
  opened_at timestamptz not null,
  closed_at timestamptz,
  status text not null,
  commodity text not null,
  direction text not null,
  matched_qty_kg numeric not null,
  comex_lots numeric not null,
  mcx_lots numeric not null,
  dgcx_lots numeric not null,
  entry_comex numeric,
  entry_mcx numeric,
  entry_dgcx numeric,
  entry_usdinr numeric,
  entry_landed numeric,
  entry_gross_spread numeric,
  entry_cost_per_kg numeric,
  entry_net_spread numeric,
  exit_comex numeric,
  exit_mcx numeric,
  exit_dgcx numeric,
  exit_usdinr numeric,
  exit_landed numeric,
  exit_gross_spread numeric,
  exit_cost_per_kg numeric,
  exit_net_spread numeric,
  realized_pnl numeric,
  mae numeric,
  mfe numeric,
  notes text
);

create table if not exists rate_history (
  id text primary key,
  ts timestamptz not null,
  commodity text not null,
  comex numeric,
  mcx numeric,
  dgcx numeric,
  usdinr numeric,
  landed numeric,
  gross_spread numeric,
  cost_per_kg numeric,
  net_spread numeric,
  source_summary text
);
```

## Main menus
- Dashboard
- Badla Scanner
- Open Trade
- Manual Ticket
- Past Price Simulator
- Trade Management
- History
- Settings

## Important notes
This app is for paper trading and learning only. Public/free data can be delayed, stale or wrong. It shows source comparison and warnings, but it does not guarantee data accuracy. Before live execution, use broker-grade feeds for MCX, COMEX and DGCX.

Margin values are estimated for paper trading only. Real broker margin can differ due to SPAN, exposure margin, currency conversion, calendar spread benefits, exchange rules, and intraday changes.
