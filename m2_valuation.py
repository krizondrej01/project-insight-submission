"""
=============================================================================
MODULE 2 — Valuation Engine
=============================================================================
project_insight / alpha v1.0
Bachelor's Thesis: SCR Sensitivity Analysis – Ondřej Kříž, IES FSV UK 2026/27

=============================================================================
"""

import numpy as np
from m1_portfolio import Liability, Bond


# ---------------------------------------------------------------------------
# RISK-FREE YIELD CURVE ENGINE
# ---------------------------------------------------------------------------

class FlatYieldCurve:
    """
    Plochá bezriziková výnosová křivka (Diskontní engine pro Proof of Concept).

    -------------------------------------------
    1. Izolace parametrů:
       Použití konstantní úrokové sazby pro všechny splatnosti eliminuje
       komplexitu termínové struktury. Umožňuje přesnou izolaci reakce rozvahy
       a okamžitou ověřitelnost výpočtů (BEL, tržní cena dluhopisů, úrokové SCR)
       bez matematického šumu způsobeného zakřivením výnosové křivky.

    2. Validace výpočetního enginu:
       Slouží k verifikaci toku dat mezi oceňováním aktiv, pasiv a regulačním
       kalkulátorem (Modul 3). Metoda 'shift()' simuluje čistě paralelní posuny
       křivky (+100 bps / -75 bps) v rámci přístupu 'shock-and-reprice'.

    PLÁNOVANÝ PŘECHOD DO BETAVERZE (Finální model)
    ----------------------------------------------
    Tato třída bude ve finální fázi nahrazena třídou `EIOPAYieldCurve`, která:
      - Bude stahovat oficiální bezrizikové křivky z ECB Data Portalu.
      - Aplikuje Smith-Wilsonovu extrapolaci a Volatility Adjustment (VA).
      - Umožní aplikaci neparalelních zátěžových šoků (Sklon/Twist a Zakřivení/Butterfly)
        odvozených z PCA analýzy historických dat (Litterman & Scheinkman, 1991).

    """

    def __init__(self, rate: float, label: str = "Base"):
        self.rate  = rate
        self.label = label

    def spot_rate(self, tenor: float) -> float:
        return self.rate
    #TODO Toto volá oceňovací engine aktiv a pasiv. Až plochou křivku nahradíme modelem Nelson-Siegel modelem, začne vracet sazbu závislou na čase r(t)

    def discount_factor(self, tenor: float) -> float:
        """df(t) = 1 / (1 + r)^t"""
        if tenor <= 0:
            return 1.0
        return 1.0 / (1.0 + self.rate) ** tenor
    #Net present value


    def shift(self, delta_bps: float, label: str = None) -> "FlatYieldCurve":
        """Return a parallel-shifted copy of the yield curve."""
        new_rate = self.rate + delta_bps / 10_000.0 #převodník bps na desetinné číslo
        return FlatYieldCurve(
            rate  = max(new_rate, 0.0),  # Floor at 0 % (no negative rates)
            label = label or f"{self.label}{delta_bps:+.0f}bps",
        )


# ---------------------------------------------------------------------------
# MORTALITY TABLE
"""možno doplnit přes API plné úmrtnostní tabulky
aktuálně pouze zparametrizováno"""
#TODO až bude hotovo, můžeme vyzkoušet napojení - zkontrolujeme, zda to splnilo očekávání - H0=bude to fungovat naprosto stejně s minimálním rozdílem
# ---------------------------------------------------------------------------

_MA = 0.0007     # Accident component
_MB = 0.000022   # Gompertz base
_MC = 1.099      # Gompertz growth rate, death doubles every 7,3 years ln(2)/ln(1,099) = 7,26


def _qx(age: int, loading: float = 1.0) -> float:
    """Annual mortality probability q_x at given age. - fce přepočítává spojitou pravděpodobnost úmrtí na diskrétní hodnoty"""
    mu_x  = _MA + _MB * (_MC ** age)
    mu_x1 = _MA + _MB * (_MC ** (age + 1))
    q = 1.0 - np.exp(-(mu_x + mu_x1) / 2.0)
    return float(np.clip(q * loading, 0.0, 1.0))

    """
    LITERATURE REFERENCE - Makeham-Gompertzův úmrtnostní model
    ---------------------------------------------------------
    Dickson, D. C. M., Hardy, M. R., & Waters, H. R. (2020). 
    Actuarial Mathematics for Life Contingent Risks (3rd ed.). 
    Cambridge University Press.
    --> Chapter 2 & 3: Survival Models, Force of Mortality & Makeham Law.
    """


# ---------------------------------------------------------------------------
# LIABILITY VALUATION (Best Estimate Liability - BEL)
# ---------------------------------------------------------------------------

class LiabilityValuation:
    """
    Projects and discounts liability cash flows for the Endowment model point.

    Cash-flow logic per year t = 1 … T:
      Outflows : Death claims + Lapse payouts (80 % SA) + Expenses
      Inflows  : Premiums
      Final T  : Maturity benefit (Surviving policies × Sum Assured)

    Net CF_t = Outflows_t − Inflows_t  (positive = insurer pays out)
    BEL      = Σ_t Net_CF_t × df(t)
    """

    # ---------------------------------------------------------------------------
    # METODICKÁ POZNÁMKA: ASYMETRIE PŘECEŇOVÁNÍ A EFEKT DNA (FLOOR EFFECT)
    # ---------------------------------------------------------------------------
    # Při prudkých posunech úrokových sazeb vzniká v rozvaze Solvency II asymetrie:
    # 1. Aktiva (dluhopisy): Podléhají čistému Mark-to-Market bez jakéhokoliv dna.
    #    Při růstu sazeb/inflaci jejich tržní hodnota padá bez omezení.
    # 2. Pasiva (BEL): Diskontují se regulační křivkou, která naráží na umělé
    #    hranice (dno na 0 % nebo dlouhodobou kotvu UFR).
    #
    # DŮSLEDEK (Duration Gap + Regulatory Floor):
    # Pokud pasiva narazí na dno a nedokážou klesnout stejně hluboko jako aktiva,
    # dojde k prudkému smrštění Vlastních zdrojů (Own Funds = Assets - Liabilities).
    # Tento efekt vytváří umělý procyklický kapitálový stres pojišťovny.
    #
    # Odkaz na literaturu: 
    # - ESRB (2020): Enhancing the macroprudential dimension of Solvency II.
    # - Nařízení Komise v přenesené pravomoci (EU) 2015/35 (Články 75 a 77).
    # ---------------------------------------------------------------------------

    EXPENSE_PCT = 0.05      # 5 % of annual premium
    EXPENSE_FIX = 0.50      # CZK 500 per policy per year (in KCZK)
    #EIOPA Technical Specifications (2014)

    def __init__(self, curve: FlatYieldCurve):
        self.curve = curve

    def value(self, liab: Liability) -> dict:
        """Compute BEL, Macaulay duration, and cash flow schedule for the policy group."""
        T       = liab.remaining_term
        age0    = liab.age
        lapse   = liab.lapse_rate
        sa      = liab.sum_assured
        prem    = liab.annual_premium
        n_pol   = liab.num_policies
        #parametry pasiv pro výpočet BEL

        active  = float(n_pol)
        #počet aktivních smluv na začátku modelového období
        rows    = []
        #tady budou uloženy časové řady peněžních toků - zatím prázdná řada
        bel     = 0.0
        #celková diskontovaná hodnota pasiv
        pv_abs_weighted_sum = 0.0
        pv_abs_sum          = 0.0
        #akumulační proměnná pro výpočet durace

        for t in range(1, T + 1):
            #smyčka přes časové období t=1,...,T
            age_t = age0 + t - 1
            #věk v daném roce t
            q     = _qx(age_t)
            #pravděpodobnost úmrtí v daném roce t

            deaths_t  = active * q
            #počet zemřelých v daném roce t
            lapses_t  = (active - deaths_t) * lapse
            #storna v daném roce t
            survivors = active - deaths_t - lapses_t
            #pokračující smlouvy v daném roce t do dalšího období t+1

            # --- Cash flows (KCZK) ---
            premiums_t     = active * prem
            death_claims_t = deaths_t * sa
            lapse_payout_t = lapses_t * sa * 0.80  # Surrender value (80 %)
            maturity_t     = (survivors * sa) if t == T else 0.0 #výplata dožití
            expenses_t     = active * prem * self.EXPENSE_PCT + active * self.EXPENSE_FIX #správa kmene

            outflows_t = death_claims_t + lapse_payout_t + maturity_t + expenses_t
            net_cf_t   = outflows_t - premiums_t

            df_t   = self.curve.discount_factor(t) #Diskontní faktor
            pv_t   = net_cf_t * df_t #PV daného roku
            bel   += pv_t #sčítáme PV do BEL

            pv_abs_weighted_sum += t * abs(pv_t) # vážený součet PV pro výpočet durace
            pv_abs_sum          += abs(pv_t) # součet PV pro výpočet durace

            rows.append({
                "year":          t,
                "active":        round(active),
                "premiums":      premiums_t,
                "death_claims":  death_claims_t,
                "maturity":      maturity_t,
                "lapse_payouts": lapse_payout_t,
                "expenses":      expenses_t,
                "net_cf":        net_cf_t,
                "df":            df_t,
                "pv_net_cf":     pv_t,
            })

            active = max(survivors, 0.0)

        duration = (pv_abs_weighted_sum / pv_abs_sum) if pv_abs_sum > 0 else 0.0

        return {"bel": bel, "duration": duration, "cf_schedule": rows}


# ---------------------------------------------------------------------------
# ASSET VALUATION (Bond Model Price)
# ---------------------------------------------------------------------------

class AssetValuation:
    """
    Re-prices bond portfolio dynamically using the yield curve.
    Ensures 100 % consistency between baseline and stressed balance sheets.
    """

    def __init__(self, curve: FlatYieldCurve):
        self.curve = curve

    def price_bond(self, bond: Bond) -> float:
        """Compute present value of bond cash flows (in KCZK)."""
        F = bond.face_value #jmenovitá hodnota dluhopisu
        c = bond.coupon_rate #kuponová sazba v %
        T = int(round(bond.maturity_years)) #doba do splatnosti

        pv = 0.0
        for t in range(1, T + 1):
            cf = c * F if t < T else (c + 1.0) * F  # Coupon vs Principal + Coupon
            pv += cf * self.curve.discount_factor(t)
        return pv

    def portfolio_value(self, bonds: list) -> float:
        """Return total model value of the bond portfolio (KCZK). - agregát celého portfolia"""
        return sum(self.price_bond(b) for b in bonds)


# ---------------------------------------------------------------------------
# SOLVENCY II BALANCE SHEET
# ---------------------------------------------------------------------------

RISK_MARGIN_PCT = 0.03  # PoC Placeholder: 3 % of |BEL| - Prozatímní hodnota


def build_balance_sheet(asset_mv: float, bel_total: float) -> dict:
    """Build the Solvency II economic balance sheet and derive Own Funds."""
    risk_margin       = abs(bel_total) * RISK_MARGIN_PCT
    
    # 1. CELKOVÁ PASIVA / TECHNICKÉ REZERVY (Technical Provisions - TP)
    # --------------------------------------------------------------------------
    # Logika: Spojuje čistý odhad závazků (BEL) a bezpečnostní přirážku (RM).
    # Představuje celkovou částku, kterou pojišťovna dluží pojistníkům + rezerva.
    #
    # Příklad:
    #   BEL = 100 mil. Kč, Risk Margin = 3 mil. Kč
    #   -> total_liabilities = 103 mil. Kč (celkový ekonomický závazek)

    total_liabilities = bel_total + risk_margin
    own_funds         = asset_mv - total_liabilities

    return {
        "asset_mv":          asset_mv,
        "bel":               bel_total,
        "risk_margin":       risk_margin,
        "total_liabilities": total_liabilities,
        "own_funds":         own_funds,
    }