"""
=============================================================================
MODULE 3 — SCR Calculation Engine (Standard Formula — simplified)
=============================================================================
project_insight / alpha v1.0

REGULATORY BASIS
----------------
Solvency II Delegated Regulation (EU) 2015/35, Articles 165–180.

This module computes two SCR sub-modules:
  1. SCR_interest_rate  — parallel yield-curve stress (up and down)
  2. SCR_spread         — spread widening shock on corporate/covered bonds

Final SCR_market is obtained by aggregating the two sub-modules using the
Solvency II correlation matrix under Article 164(2) of Delegated Regulation
(EU) 2015/35 (ρ = 0.00 between IR and Spread when IR_up dominates; ρ = 0.50
when IR_down dominates):

    SCR_market = sqrt( SCR_ir² + SCR_spread² + 2·ρ·SCR_ir·SCR_spread )

HOW EACH SUB-MODULE WORKS  (the shock-and-reprice method)
----------------------------------------------------------
1. Compute baseline Own Funds  (OF_base).
2. Apply the regulatory stress (shift the curve / widen spreads).
3. Recompute Own Funds under the stress  (OF_stressed).
4. Capital charge = max(0, OF_base − OF_stressed).

A positive charge means the stress makes the insurer POORER (assets lose
more than liabilities, or liabilities grow faster than assets).
=============================================================================
"""

import numpy as np
from m1_portfolio import Liability, Bond, LIABILITIES, ASSETS
from m2_valuation import (
    FlatYieldCurve,
    LiabilityValuation,
    AssetValuation,
    build_balance_sheet,
)


# ---------------------------------------------------------------------------
# EIOPA PARALLEL SHIFT MAGNITUDES (Art. 166 — simplified flat-curve version)
# ---------------------------------------------------------------------------
# In the standard formula these are RELATIVE shocks.
# For a flat-curve model we use representative absolute shocks (bps):

IR_SHOCK_UP_BPS   = 100    # +100 bps  (≈ typical relative shock for 10yr at 3.5 %)
IR_SHOCK_DOWN_BPS = -75    # −75  bps

#TODO předělat na dynamické šoky závislé na duraci a segmentu křivky

# ---------------------------------------------------------------------------
# EIOPA SPREAD STRESS FACTORS (Delegated Regulation (EU) 2015/35, Art. 176)
# ---------------------------------------------------------------------------
# METODICKÝ A REGULAČNÍ KONCEPT:
# Spreadové riziko měří citlivost tržní hodnoty aktiv na rozšíření kreditní
# přirážky (kreditního spreadu) nad bezrizikovou výnosovou křivku.
#
# Základní vzorec pro zátěžový faktor jednoho dluhopisu:
#   Faktor_i = min( SPREAD_FACTOR[rating] * Modifikovaná_Durace_i, 1.0 )
#   Kapitálový požadavek (Charge_i) = Tržní_Hodnota_i * Faktor_i
#
# DVA KLÍČOVÉ HÝBATELE KAPITÁLOVÉHO POŽADAVKU (SCR_spread):
# 1. Rating emitenta (Skladba portfolia): Čím horší rating, tím vyšší roční
#    šok na spread. Spekulativní dluhopisy (BB až CCC) vytvářejí masivní
#    kapitálový požadavek (penalizace za riziko defaultu/kreditního selhání).
# 2. Modifikovaná durace (ModD): Působí jako páka. Čím delší splatnost
#    dluhopis má, tím více let se roční spreadový šok nasčítá do celkové
#    procentuální ztráty tržní ceny.
#
# ODLIŠENÍ OD ÚROKOVÉHO RIZIKA (ALM / Duration Mismatch):
# Zatímco úrokové riziko (SCR_ir) řeší nesoulad durací aktiv a pasiv
# při posunu výnosové křivky, spreadové riziko chápeme jako jednostranný
# krizový šok na kreditní kvalitu emitentů v majetku pojišťovny.
# ---------------------------------------------------------------------------

# Roční faktor šoku na spread podle kreditního ratingu (za každý rok modifikované durace)
SPREAD_FACTOR = {
    "AAA":  0.0090,  #  0,90 % / rok durace (vysoká bonita, minimální SCR)
    "AA":   0.0110,  #  1,10 % / rok durace
    "A":    0.0140,  #  1,40 % / rok durace
    "BBB":  0.0250,  #  2,50 % / rok durace (prahový investiční stupeň)
    "BB":   0.0450,  #  4,50 % / rok durace (spekulativní pásmo – prudký nárůst SCR)
    "B":    0.0750,  #  7,50 % / rok durace
    "CCC":  0.1000,  # 10,00 % / rok durace (vysoké riziko selhání)
}

# Kryté dluhopisy / Hypoteční zástavní listy (Covered Bonds):
# Regulátor přiznává 40% slevu na kapitálovém požadavku (aplikuje se 60 % korporátního šoku)
# díky mechanismu dvojího krytí (Dual Recourse – nárok vůči bance + krycímu bloku hypoték).
COVERED_BOND_MULTIPLIER = 0.60

# Státní dluhopisy EHP v domácí měně (Government Bonds):
# Standardní vzorec Solvency II přiznává státním dluhopisům EHP v domácí měně 
# nulový kapitálový požadavek na spreadové riziko (GOVT_SPREAD_FACTOR = 0.00).
GOVT_SPREAD_FACTOR = 0.00


# ---------------------------------------------------------------------------
# AGGREGATION CORRELATION  (Delegated Regulation (EU) 2015/35, Art. 164(2))
# ---------------------------------------------------------------------------
# Korelace mezi úrokovým a spreadovým rizikem závisí na dominantním scénáři SCR_ir:
#   - ρ = 0.00, pokud úrokovému riziku dominuje scénář růstu sazeb (UP).
#   - ρ = 0.50, pokud úrokovému riziku dominuje scénář poklesu sazeb (DOWN).

RHO_IR_SPREAD_UP   = 0.00  # Art. 164(2): rho = 0.0 when IR UP shock dominates
RHO_IR_SPREAD_DOWN = 0.50  # Art. 164(2): rho = 0.5 when IR DOWN shock dominates
RHO_IR_SPREAD      = RHO_IR_SPREAD_DOWN  # Default for DOWN-dominated legacy book


def get_ir_spread_correlation(dominant_scenario: str) -> float:
    """Return regulatory correlation parameter per Art. 164(2) of (EU) 2015/35."""
    return RHO_IR_SPREAD_UP if dominant_scenario == "UP" else RHO_IR_SPREAD_DOWN


def _compute_of(curve: FlatYieldCurve, liabilities: list, bonds: list) -> float:
    """
    pomocný 'Repricing Engine' pro přístup 'Shock & Reprice'.
    Přepočítá celou rozvahu a vrátí hodnotu Vlastních zdrojů pro dodanou křivku.
    """
    
    # --- 1. STRANA PASIV (Závazky vůči pojistníkům) ---
    # Vytvoříme oceňovací instanci, která do sebe "nasaje" dodanou křivku 
    # (ta může být buď základní, nebo už posunutá o šok UP/DOWN).
    lv  = LiabilityValuation(curve)
    
    # Projdeme všechny pojistné smlouvy (liabilities), každou diskontujeme touto křivkou a sečteme je do jedné celkové sumy Best Estimate of Liabilities.
    bel = sum(lv.value(l)["bel"] for l in liabilities)

    # --- 2. STRANA AKTIV (Krytí závazků) ---
    # Vytvoříme obdobnou oceňovací instanci pro aktiva.
    av   = AssetValuation(curve)
    
    # Spočítáme celkovou tržní hodnotu (Market Value) všech dluhopisů v portfoliu 
    # diskontovanou totožnou křivkou jako pasiva.
    a_mv = av.portfolio_value(bonds)

    # --- 3. EKONOMICKÁ ROZVAHA A VLASTNÍ ZDROJE (Own Funds) ---
    # Funkce build_balance_sheet (z Modulu 2) vezme nová Aktiva a nové BEL.
    # Uvnitř automaticky přičte i Risk Margin (náklad na držení kapitálu) 
    # a sestaví kompletní Solvency II rozvahu pro tento specifický tržní scénář.
    bs = build_balance_sheet(a_mv, bel)
    
    # Z hotové rozvahy vyextrahujeme jen čisté jmění (Own Funds = Aktiva - Technické rezervy).
    # Toto jediné číslo je to, co nás zajímá, protože z jeho poklesu budeme počítat SCR.
    return bs["own_funds"]


# ---------------------------------------------------------------------------
# SUB-MODULE 1: Interest Rate Risk SCR
# ---------------------------------------------------------------------------

def scr_interest_rate(base_curve: FlatYieldCurve, liabilities: list, bonds: list) -> dict:
    """
    Výpočet kapitálového požadavku na úrokové riziko (SCR_ir).
    Na základě principu 'Shock & Reprice' testuje dva scénáře (UP a DOWN)
    a jako výsledné SCR vybírá ten horší z nich.
    """
    
    # --- 1. ZÁKLADNÍ SCÉNÁŘ (Baseline) ---
    # Jak je na tom pojišťovna před krizí. Použijeme nezměněnou (base) výnosovou křivku a spočítáme základní Own Funds.
    of_base = _compute_of(base_curve, liabilities, bonds)

    # --- 2. SCÉNÁŘ RŮSTU SAZEB (Shock UP) ---
    # Vytvoříme novou křivku, která je posunutá nahoru o definovaný šok.
    curve_up = base_curve.shift(IR_SHOCK_UP_BPS)
    
    # Přepočítáme celou rozvahu s touto vyšší křivkou.
    of_up = _compute_of(curve_up, liabilities, bonds)
    
    # Spočítáme ztrátu. Solvency II je asymetrický režim – pokud by růst sazeb pojišťovně pomohl (of_up > of_base), regulátor nedovolí zapsat si "záporné SCR"
    # Proto používáme max(0.0, ...), což ztrátu zastropuje na nule.
    loss_up = max(0.0, of_base - of_up)

    # --- 3. SCÉNÁŘ POKLESU SAZEB (Shock DOWN) ---
    # Vytvoříme další křivku, posunutou dolů o definovaný šok.
    curve_down = base_curve.shift(IR_SHOCK_DOWN_BPS)
    
    # Znovu přepočítáme celou rozvahu.
    #TODO H0: uvidíme obrovský propad Own Funds
    of_down = _compute_of(curve_down, liabilities, bonds)
    loss_down = max(0.0, of_base - of_down)

    # --- 4. VÝSLEDNÝ KAPITÁLOVÝ POŽADAVEK (SCR_ir) ---
    # Podle Solvency II je úrokové riziko definováno jako ten horší (maximální) ze dvou scénářů.
    scr_ir = max(loss_up, loss_down)

    dominant = "UP" if loss_up >= loss_down else "DOWN"

    return {
        "of_base": of_base,
        "of_up": of_up,
        "loss_up": loss_up,
        "of_down": of_down,
        "loss_down": loss_down,
        "scr_ir": scr_ir,
        "dominant_scenario": dominant
    }


# ---------------------------------------------------------------------------
# SUB-MODULE 2: Spread Risk SCR
# ---------------------------------------------------------------------------

def _modified_duration(bond: Bond, base_curve: FlatYieldCurve = None) -> float:
    """
    Výpočet Modifikované durace dluhopisu (ModD = MacD / (1 + y)).
    
    METODICKÝ A REGULAČNÍ KONCEPT:
    Modifikovaná durace udává procentuální změnu ceny dluhopisu při posunu výnosu o 100 bps.
    Ve standardním vzorci Solvency II (Art. 176) vystupuje ModD jako přímý násobič:
        Kapitálová zátěž = Tržní_Cena * SPREAD_FACTOR[rating] * ModD
    """
    # 1. Extrakce základních parametrů dluhopisu
    # Převod splatnosti na celé roky (pro diskrétní roční cash-flow), kupónové sazby a kreditního spreadu.
    T = int(round(bond.maturity_years))
    c = bond.coupon_rate
    s = getattr(bond, "credit_spread", 0.0)
    
    # 2. Výnosová míra do splatnosti (Yield to Maturity = r_f(t) + credit_spread)
    rf_T = base_curve.spot_rate(T) if base_curve is not None else 0.035
    y = rf_T + s

    # 3. Akumulátory pro přítomnou hodnotu cash-flow (pv_sum) a vážený čas (wt_sum):
    pv_sum = 0.0
    wt_sum = 0.0
    
    # 4. Výpočetní cyklus přes jednotlivé roky do splatnosti:
    for t in range(1, T + 1):
        # Algebraický trik: do posledního roku (t == T) přičteme ke kupónu i jistinu (1.0).
        cf  = c if t < T else (c + 1.0)
        r_t = base_curve.spot_rate(t) if base_curve is not None else 0.035
        y_t = r_t + s
        
        # Diskontování cash-flow na přítomnou hodnotu (PV_t = CF_t / (1 + r_t + s)^t)
        pv  = cf / (1.0 + y_t) ** t
        
        # Časově vážené cash-flow (t * PV_t) pro čitatele Macaulayovy durace
        pv_sum += pv
        wt_sum += t * pv

    # 5. Macaulayova durace (klasická durace v letech)
    mac_dur = wt_sum / pv_sum if pv_sum > 0 else T
    
    # 6. Převod Macaulayovy durace na Modifikovanou duraci (citlivost v % na 1% posun výnosové sazby)
    return mac_dur / (1.0 + y)


def scr_spread(bonds: list, base_curve: FlatYieldCurve = None) -> dict:
    """
    Výpočet celkového kapitálového požadavku na spreadové riziko (SCR_spread).

    METODICKÝ A REGULAČNÍ KONCEPT (Solvency II, Art. 176):
    Pro každý dluhopis 'i' se vypočítá individuální kapitálová zátěž podle vzorce:
        Faktor_i = min( base_factor(rating) * Modifikovaná_Durace_i, 1.0 )
        Charge_i = Tržní_Hodnota_i * Faktor_i
    Vzorec v podstatě přetavuje úvěrovou bonitu (rating) a časovou citlivost (duraci) do konkrétní korunové ztráty, kterou musí pojišťovna pokrýt vlastním kapitálem.

    SPECIFIKA EMITENTŮ:
    1. Státní dluhopisy EHP (GOVT): Faktor_i = 0.0 (výjimka ze standardního vzorce).
    2. Kryté dluhopisy (COVERED): Aplikuje se 40% sleva (násobitel 0.60).
    3. Korporátní dluhopisy (CORP): Plná zátěž dle kreditního ratingu.

    Returns
    -------
    dict: 
        - "per_bond": seznam detailních výsledků pro každý dluhopis
        - "scr_spread_total": celkový součet kapitálových požadavků
    """
    if base_curve is None:
        curve = FlatYieldCurve(rate=0.035, label="Base (flat 3.5 %)")
        av    = AssetValuation(curve)
    elif isinstance(base_curve, AssetValuation):
        av    = base_curve
        curve = av.curve
    else:
        curve = base_curve
        av    = AssetValuation(curve)

    per_bond = []

    for bond in bonds:
        # 1. Jednotný zdroj pravdy (Single Source of Truth):
        # Tržní hodnota se počítá přímo přes AssetValuation(base_curve).price_bond(bond)
        mv      = av.price_bond(bond)
        bond.market_price = (mv / bond.face_value) * 100.0
        mod_dur = _modified_duration(bond, curve)

        # 2. Určení zátěžového faktoru podle typu emitenta a ratingu
        if bond.issuer_type == "GOVT":
            factor = 0.0
        else:
            # Načtení základního ročního šoku dle ratingu (s fallbackem na 3.0 % pro neznámé ratingy)
            base_factor = SPREAD_FACTOR.get(bond.rating, 0.030)
            
            # Úprava pro kryté dluhopisy (Covered Bonds / HZL)
            if bond.issuer_type == "COVERED":
                base_factor *= COVERED_BOND_MULTIPLIER
            
            # Výpočet Modifikované durace a finálního šoku s horním stropem 100 % (1.0)
            factor  = min(base_factor * mod_dur, 1.0)

        # 3. Absolutní kapitálový požadavek pro daný dluhopis (v tis. Kč / EUR)
        charge = mv * factor

        per_bond.append({
            "bond":        bond.name,
            "rating":      bond.rating,
            "issuer_type": bond.issuer_type,
            "mv_kczk":     mv,
            "mod_dur":     mod_dur,
            "factor":      factor,
            "charge_kczk": charge,
        })

    # 5. Celkový kapitálový požadavek za spreadové riziko portfolia
    scr_spread_total = sum(r["charge_kczk"] for r in per_bond)
    
    return {
        "per_bond": per_bond, 
        "scr_spread_total": scr_spread_total
    }


# ---------------------------------------------------------------------------
# AGGREGATION: SCR_market
# ---------------------------------------------------------------------------

def aggregate_scr(
    scr_ir: float,
    scr_sp: float,
    rho: float = None,
    dominant_scenario: str = "DOWN",
) -> float:
    """
    Agregace podmodulů tržního rizika podle standardního vzorce Solvency II (Článek 164(2)).
        SCR_market = sqrt( SCR_ir² + SCR_sp² + 2·ρ·SCR_ir·SCR_sp )
    """
    if rho is None:
        rho = get_ir_spread_correlation(dominant_scenario)

    return float(np.sqrt(
        scr_ir ** 2
        + scr_sp ** 2
        + 2.0 * rho * scr_ir * scr_sp
    ))


# ---------------------------------------------------------------------------
# SOLVENCY RATIO
# ---------------------------------------------------------------------------

def solvency_ratio(own_funds: float, scr: float) -> float:
    """
    Výpočet Solvenčního poměru (Solvency Ratio = Own Funds / SCR).

    METODICKÝ A REGULAČNÍ KONCEPT:
    Klíčový ukazatel kapitálové přiměřenosti v Solvency II (Pillar 1 KPI):
    - Solvency Ratio ≥ 1.0 (100 %): Zákonné minimum. Pojišťovna plní kapitálový požadavek.
    - Solvency Ratio < 1.0 (< 100 %): Regulační kmit. Pojišťovna musí do 2 měsíců 
      předložit ČNB Ozdravný plán (Recovery Plan).
    - Solvency Ratio v praxi (180 % - 250 %): Běžný interní terč velkých skupin (ORSA/Rating).

    OŠETŘENÍ HRANIČNÍCH STAVŮ:
    Pokud by SCR bylo ≤ 0 (teoretické portfolio bez jakéhokoliv rizika),
    funkce bezpečně vracejí hodnotu 'inf' (nekonečno), čímž zamezí dělení nulou.
    """
    if scr <= 0:
        return float("inf")
    
    return own_funds / scr