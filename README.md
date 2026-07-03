# ESTA Badla Final Pro

Mobile-first paper-trading dashboard for MCX/COMEX/DGCX badla/spread learning.

## Run locally

```bat
cd %USERPROFILE%\Downloads\esta_badla_final_pro
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

## Streamlit Cloud
Upload these files to a GitHub repo:

- `app.py`
- `requirements.txt`
- `README.md`

Deploy with **Main file path: `app.py`**.

## Supabase persistence (recommended)

Create a free Supabase project, open SQL Editor, and run this SQL:

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

Then in Streamlit Cloud, set Secrets:

```toml
SUPABASE_URL="https://YOUR_PROJECT.supabase.co"
SUPABASE_KEY="YOUR_ANON_KEY"
```

Local CSV mode is used automatically if Supabase secrets are missing.
