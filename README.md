# ESTA Badla Signal Pro

Mobile-first paper badla trading terminal for MCX vs international commodity spreads.

## Run locally
```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Cloud secrets
Add these in Streamlit Cloud > App > Settings > Secrets:

```toml
SUPABASE_URL="https://YOUR_PROJECT.supabase.co"
SUPABASE_KEY="YOUR_ANON_PUBLIC_KEY"
```

## Supabase SQL setup
Run this in Supabase SQL Editor. If the tables already exist, it is safe to run again.

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

This app is for paper trading and learning only. Public/free data can be delayed or stale. Before live execution, use broker feeds for MCX, COMEX and DGCX.
