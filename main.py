"""
=============================================================================
MAIN — project_insight alpha v1.0  (run this file)
=============================================================================
Bachelor's Thesis: SCR Sensitivity Analysis – Ondřej Kříž, IES FSV UK 2026/27

USAGE
-----
    python main.py

OUTPUT
------
Prints a structured report to the console AND saves:
  outputs/balance_sheet.csv
  outputs/liability_detail.csv
  outputs/asset_detail.csv
  outputs/scr_detail.csv
  outputs/summary.csv
=============================================================================
"""

import os, csv
import numpy as np
from m1_portfolio import LIABILITIES, ASSETS
from m2_valuation  import FlatYieldCurve, LiabilityValuation, AssetValuation, build_balance_sheet
from m3_scr        import (
    scr_interest_rate, scr_spread, aggregate_scr, solvency_ratio,
    get_ir_spread_correlation, IR_SHOCK_UP_BPS, IR_SHOCK_DOWN_BPS,
)

OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ─────────────────────────────────────────────────────────────────────────────
# FORMATTING HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _hr(char="─", width=72):
    print(char * width)

def _section(title):
    _hr("═")
    pad = (72 - len(title) - 2) // 2
    print("║" + " " * pad + title + " " * (72 - pad - len(title) - 2) + "║")
    _hr("═")

def _kczk(v):
    """Format a KCZK value with thousands separator."""
    return f"{v:>14,.1f} KCZK"

def _pct(v):
    return f"{v:>8.2%}"

def _save_csv(rows: list, filename: str):
    """Save a list-of-dicts to a CSV file."""
    path = os.path.join(OUTPUT_DIR, filename)
    if not rows:
        return
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    print(f"  ✓  Saved → outputs/{filename}")


# ─────────────────────────────────────────────────────────────────────────────
# KROK 0: ZÁKLADNÍ BEZRIZIKOVÁ VÝNOSOVÁ KŘIVKA
# ─────────────────────────────────────────────────────────────────────────────

BASE_RATE = 0.035
base_curve = FlatYieldCurve(rate=BASE_RATE, label="Base (flat 3.5 %)")

# ─────────────────────────────────────────────────────────────────────────────
# KROK 1: OCENĚNÍ KMENE PASIV (BEST ESTIMATE OF LIABILITIES – BEL)
# ─────────────────────────────────────────────────────────────────────────────

# Inicializace oceňovacího enginu pasiv s načtenou základní bezrizikovou křivkou
lv   = LiabilityValuation(base_curve)
liab_results = []

# Vykreslení grafické hlavičky
_section("MODUL 1 + 2 | PORTFOLIO PASIV A JEJICH OCENĚNÍ")
print(f"\n  Základní bezriziková sazba (base): {BASE_RATE:.2%}  (plochá křivka)\n")

# Vytištění záhlaví tabulky s přesným zarovnáním sloupců
print(f"  {'Model Point':<35}  {'Produkt':<12}  {'#Smluv':>5}  {'BEL':>14}  {'Durace':>8}")
_hr()

# Akumulátor celkového BEL za celou pojišťovnu a seznam pro CSV export
bel_total = 0.0
liab_rows = []

# Procházení jednotlivých modelových bodů (reprezentativních skupin smluv) z Modulu 1
for liab in LIABILITIES:
    # Ocenění daného modelového bodu (vypočítá BEL a efektivní duraci cash-flow)
    res = lv.value(liab)
    
    # Pričtení BEL k celkové sumě závazků pojišťovny
    bel_total += res["bel"]
    
    # Formátovaný tisk řádku do konzole pro okamžitou kontrolu
    print(f"  {liab.name:<35}  {liab.product:<12}  {liab.num_policies:>5}  "
          f"{_kczk(res['bel'])}  {res['duration']:>7.1f} yr")
    
    # Příprava strukturovaných dat pro výkaz do souboru liability_detail.csv
    liab_rows.append({
        "name":             liab.name,
        "product":          liab.product,
        "num_policies":     liab.num_policies,
        "age":              liab.age,
        "remaining_term":   liab.remaining_term,
        "tmr_pct":          f"{liab.tmr:.2%}",
        "sum_assured_kczk": liab.sum_assured,
        "bel_kczk":         round(res["bel"], 2),
        "duration_yr":      round(res["duration"], 2),
        "mod_duration_yr":  round(res["mod_duration"], 2),
    })

_hr()
# Vytištění finálního součtu celkového BEL za kompletní kmen pojišťovny
print(f"  {'CELKOVÉ BEL (Best Estimate)':<35}  {'':>18}  {_kczk(bel_total)}")


# ─────────────────────────────────────────────────────────────────────────────
# STEP 2: ASSET VALUATION - stejná logika jako krok 1
# ─────────────────────────────────────────────────────────────────────────────

_section("MODULE 2 | ASSET PORTFOLIO VALUATION")
print(f"\n  {'Bond':<40}  {'Rating':<6}  {'Face (KCZK)':>12}  {'Mkt Price':>9}  {'MV (KCZK)':>14}")
_hr()

av = AssetValuation(base_curve)
asset_mv_total = 0.0
asset_rows = []
for bond in ASSETS:
    # Single source of truth: DCF valuation via AssetValuation(base_curve)
    mv_model = av.price_bond(bond)
    bond.market_price = (mv_model / bond.face_value) * 100.0
    mv_mkt   = mv_model
    asset_mv_total += mv_model
    print(f"  {bond.name:<40}  {bond.rating:<6}  {bond.face_value:>12,.0f}  "
          f"{bond.market_price:>8.2f}  {_kczk(mv_mkt)}")
    asset_rows.append({
        "name":              bond.name,
        "issuer_type":       bond.issuer_type,
        "rating":            bond.rating,
        "face_value_kczk":   bond.face_value,
        "coupon_pct":        f"{bond.coupon_rate:.2%}",
        "credit_spread_bps": round(bond.credit_spread * 10_000, 1),
        "maturity_yr":       bond.maturity_years,
        "market_price":      round(bond.market_price, 4),
        "market_value_kczk": round(mv_mkt, 2),
        "model_value_kczk":  round(mv_model, 2),
    })

_hr()
print(f"  {'TOTAL ASSET MV':<40}  {'':>18}  {_kczk(asset_mv_total)}")


# ─────────────────────────────────────────────────────────────────────────────
# KROK 3: SESTAVENÍ EKONOMICKÉ ROZVAHY SOLVENCY II (ECONOMIC BALANCE SHEET)
# ─────────────────────────────────────────────────────────────────────────────

_section("MODUL 2 | EKONOMICKÁ ROZVAHA SOLVENCY II (Economic Balance Sheet)")

# Zavolání funkce z Modulu 2, která vezme celková aktiva a BEL,
# automaticky dopočítá Risk Margin (3 % * BEL) a sestaví rozvahu.
bs = build_balance_sheet(asset_mv_total, bel_total)

print(f"""
  ╔═════════════════════════════════════════════════════════════════╗
  ║                EKONOMICKÁ ROZVAHA SOLVENCY II                   ║
  ╠═════════════════════════════════════════════════════════════════╣
  ║  AKTIVA (Assets)                                                ║
  ║    Portfolio dluhopisů (tržní hodnota)      {_kczk(bs['asset_mv'])} ║
  ╠═════════════════════════════════════════════════════════════════╣
  ║  PASIVA A ZÁVAZKY (Liabilities)                                 ║
  ║    Nejlepší odhad závazků (BEL)             {_kczk(bs['bel'])} ║
  ║    Riziková přirážka (Risk Margin CoC)      {_kczk(bs['risk_margin'])} ║
  ║    Celková pasiva (Technical Provisions)    {_kczk(bs['total_liabilities'])} ║
  ╠═════════════════════════════════════════════════════════════════╣
  ║  VLASTNÍ ZDROJE (Own Funds = Assets - Liab) {_kczk(bs['own_funds'])} ║
  ╚═════════════════════════════════════════════════════════════════╝
""")

# Příprava zaokrouhlených dat rozvahy pro export do souboru balance_sheet.csv
bs_rows = [{k: round(v, 2) for k, v in bs.items()}]


# ─────────────────────────────────────────────────────────────────────────────
# KROK 4: VÝPOČET KAPITÁLOVÝCH POŽADAVKŮ (SCR CALCULATION)
# ─────────────────────────────────────────────────────────────────────────────

# --- 4A. PODMODUL ÚROKOVÉHO RIZIKA (SCR_ir) ---
_section("MODUL 3 | KAPITÁLOVÝ POŽADOVEK — ÚROKOVÉ RIZIKO (SCR_ir)")

# Spuštění funkce 'Shock & Reprice' z Modulu 3:
# Přepočítá celou rozvahu pro základní stav, stav po růstu sazeb (+100 bps) a stav po poklesu sazeb (-75 bps).
ir = scr_interest_rate(base_curve, LIABILITIES, ASSETS)

# Porovnávací report krizových scénářů do konzole
print(f"""
  Aplikované regulační šoky (Solvency II Art. 165):
    ↑ UP   šok : {IR_SHOCK_UP_BPS:+} bps  →  nová sazba = {BASE_RATE + IR_SHOCK_UP_BPS/10000:.2%}
    ↓ DOWN šok : {IR_SHOCK_DOWN_BPS:+} bps  →  nová sazba = {max(0, BASE_RATE + IR_SHOCK_DOWN_BPS/10000):.2%}

  {'Scénář':<22}  {'Vlastní Zdroje':>14}  {'Změna vs Base':>16}  {'Ztráta (Charge)':>16}
  {'-'*22}  {'-'*14}  {'-'*16}  {'-'*16}
  {'Základní (Base)':<22}  {_kczk(ir['of_base'])}  {'—':>16}  {'—':>16}
  {'Stresovaný UP':<22}  {_kczk(ir['of_up'])}  {_kczk(ir['of_up']-ir['of_base'])}  {_kczk(ir['loss_up'])}
  {'Stresovaný DOWN':<22}  {_kczk(ir['of_down'])}  {_kczk(ir['of_down']-ir['of_base'])}  {_kczk(ir['loss_down'])}

  ► Dominantní krizový scénář : {ir['dominant_scenario']}
  ► Výsledné SCR_interest_rate : {_kczk(ir['scr_ir'])}
""")


# --- 4B. PODMODUL SPREADOVÉHO RIZIKA (SCR_spread) ---
_section("MODUL 3 | KAPITÁLOVÝ POŽADOVEK — SPREADOVÉ RIZIKO (SCR_spread)")

# Spuštění výpočtu spreadového rizika z Modulu 3:
# Projde všechna aktiva a spočítá zátěž = Market_Value * min(base_factor * ModD, 1.0)
sp = scr_spread(ASSETS, base_curve)

# Vytiskne rozpad spreadového rizika po jednotlivých dluhopisových pozicích do konzole
print(f"\n  {'Dluhopis':<40}  {'Rating':<6}  {'Mod.Dur':>7}  {'Faktor':>8}  {'Kap. Zátěž (KCZK)':>14}")
_hr()

for r in sp["per_bond"]:
    print(f"  {r['bond']:<40}  {r['rating']:<6}  {r['mod_dur']:>7.2f}  "
          f"  {r['factor']:>6.4f}  {_kczk(r['charge_kczk'])}")

_hr()
# Celkový součet kapitálových požadavků za spreadové riziko
print(f"  {'Celkové SCR_spread':<59}  {_kczk(sp['scr_spread_total'])}\n")

# ─────────────────────────────────────────────────────────────────────────────
# KROK 5: AGREGACE RIZIK, SOLVENČNÍ POMĚR A EXPORT VÝKAZŮ
# ─────────────────────────────────────────────────────────────────────────────

_section("MODUL 3 | AGREGACE TRŽNÍHO RIZIKA A SOLVENČNÍ POMĚR (Solvency Ratio)")

# Načtení vypočtených kapitálových požadavků z předchozího kroku
scr_ir_val = ir["scr_ir"]
scr_sp_val = sp["scr_spread_total"]

# 1. Agregace tržních rizik přes variančně-kovarianční matici dle čl. 164(2) (EU) 2015/35:
# Při dominanci scénáře DOWN platí ρ = 0.50 (při scénáři UP platí ρ = 0.00).
rho_val    = get_ir_spread_correlation(ir["dominant_scenario"])
scr_mkt    = aggregate_scr(scr_ir_val, scr_sp_val, rho=rho_val)

# 2. Výpočet Solvenčního poměru (Solvency Ratio = Own Funds / SCR)
sr         = solvency_ratio(bs["own_funds"], scr_mkt)

# 3. Absolutní kapitálový přebytek / deficit (Capital Surplus = Own Funds - SCR)
surplus    = bs["own_funds"] - scr_mkt

# 4. Regulatory Compliance Status
status     = "✅  COMPLIANT (Plní požadavky)" if sr >= 1.0 else "❌  NON-COMPLIANT (Kapitálový deficit)"

# Vytiskne finální přehledový dashboard Pilíře 1 do konzole
print(f"""
  Agregační vzorec (Variančně-kovarianční matice, Art. 164(2), ρ = {rho_val:.2f}):
    SCR_market = √( SCR_ir²  +  SCR_spread²  +  2·ρ·SCR_ir·SCR_spread )
               = √( {scr_ir_val:.1f}² + {scr_sp_val:.1f}² + 2·{rho_val:.2f}·{scr_ir_val:.1f}·{scr_sp_val:.1f} )

  ┌─────────────────────────────────────────────────────────┐
  │  KOMPONENTA           │  HODNOTA (KCZK)                 │
  ├───────────────────────┼─────────────────────────────────┤
  │  SCR_interest_rate    │  {_kczk(scr_ir_val)}            │
  │  SCR_spread           │  {_kczk(scr_sp_val)}            │
  │  SCR_market (celkem)  │  {_kczk(scr_mkt)}               │
  ├───────────────────────┼─────────────────────────────────┤
  │  Vlastní zdroje (OF)  │  {_kczk(bs['own_funds'])}       │
  │  Kapitálový přebytek  │  {_kczk(surplus)}               │
  │  Solvenční poměr (SR) │  {sr:>8.1%}                     │
  │  Stav regulace        │  {status:<31}                   │
  └─────────────────────────────────────────────────────────┘
""")

# Příprava strukturovaných dat pro celkový souhrnný výkaz (summary.csv)
summary_rows = [{
    "base_rate_pct":        f"{BASE_RATE:.2%}",
    "bel_total_kczk":       round(bel_total, 2),
    "asset_mv_kczk":        round(asset_mv_total, 2),
    "own_funds_kczk":       round(bs["own_funds"], 2),
    "scr_ir_kczk":          round(scr_ir_val, 2),
    "scr_spread_kczk":      round(scr_sp_val, 2),
    "scr_market_kczk":      round(scr_mkt, 2),
    "solvency_ratio_pct":   f"{sr:.2%}",
    "status":               "COMPLIANT" if sr >= 1.0 else "NON-COMPLIANT",
}]

# Příprava dat pro detailní rozpad podmodulů SCR (scr_detail.csv)
scr_rows = [{
    "component":    "SCR_interest_rate",
    "dominant":     ir["dominant_scenario"],
    "loss_up_kczk": round(ir["loss_up"], 2),
    "loss_dn_kczk": round(ir["loss_down"], 2),
    "scr_kczk":     round(scr_ir_val, 2),
}, {
    "component":    "SCR_spread",
    "dominant":     "N/A",
    "loss_up_kczk": "N/A",
    "loss_dn_kczk": "N/A",
    "scr_kczk":     round(scr_sp_val, 2),
}, {
    "component":    "SCR_market (aggregated)",
    "dominant":     "N/A",
    "loss_up_kczk": "N/A",
    "loss_dn_kczk": "N/A",
    "scr_kczk":     round(scr_mkt, 2),
}]


_section("MODELOVÉ VÝSTUPY (OUTPUT FILES)")
print()

_save_csv(liab_rows,   "liability_detail.csv")
_save_csv(asset_rows,  "asset_detail.csv")
_save_csv(bs_rows,     "balance_sheet.csv")
_save_csv(scr_rows,    "scr_detail.csv")
_save_csv(summary_rows,"summary.csv")
_save_csv(sp["per_bond"], "spread_detail.csv")

print()
_hr("═")
print("  project_insight alpha v1.0  —  výpočet úspěšně dokončen")
_hr("═")