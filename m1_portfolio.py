"""
=============================================================================
MODULE 1 — Portfolio Definition
=============================================================================
project_insight / alpha v1.0
Bachelor's Thesis: SCR Sensitivity Analysis – Ondřej Kříž, IES FSV UK 2026/27
=============================================================================
"""

from dataclasses import dataclass
from typing import List


# ---------------------------------------------------------------------------
# DATA CLASSES
# ---------------------------------------------------------------------------

@dataclass
class Liability:
    """
    One representative group of insurance policies ('model point').

    Fields
    ------
    name          : human-readable label
    product       : ENDOWMENT | TERM_LIFE | ANNUITY
    num_policies  : number of contracts in this group
    age           : current age of a representative policyholder (years)
    remaining_term: years until contract expires / maturity
    tmr           : guaranteed technical interest rate
    sum_assured   : insured amount per policy (CZK thousands)
    annual_premium: annual premium per policy   (CZK thousands; 0 for in-pmt annuity)
    lapse_rate    : annual probability of voluntary termination (proportion 0–1)
    """
    name: str
    product: str
    num_policies: int
    age: int
    remaining_term: int
    tmr: float
    sum_assured: float
    annual_premium: float
    lapse_rate: float


@dataclass
class Bond:
    """
    One bond in the asset portfolio.

    Fields
    ------
    name          : human-readable label
    issuer_type   : GOVT | CORP | COVERED
    rating        : credit rating (AAA … CCC)
    face_value    : par value of the whole position (CZK thousands)
    coupon_rate   : annual coupon as a decimal (e.g. 0.035 = 3.5 %)
    maturity_years: remaining time to maturity (years)
    market_price  : current dirty price per 100 of face value
    """
    name: str
    issuer_type: str
    rating: str
    face_value: float
    coupon_rate: float
    maturity_years: float
    market_price: float


# ---------------------------------------------------------------------------
# PORTFOLIO DEFINITION
# ---------------------------------------------------------------------------

LIABILITIES: List[Liability] = [
    Liability(
        name          = "Legacy Endowment (Baseline)",
        product       = "ENDOWMENT",
        num_policies  = 2200,
        age           = 45,
        remaining_term= 15,
        tmr           = 0.040,
        sum_assured   = 600.0,
        annual_premium=  36.0,
        lapse_rate    = 0.030,
    ),
]

ASSETS: List[Bond] = [
    Bond(
        name          = "CZ Govt 3.5 % 2034",
        issuer_type   = "GOVT",
        rating        = "AA",
        face_value    = 50_000.0,
        coupon_rate   = 0.0350,
        maturity_years= 8.0,
        market_price  = 99.8,
    ),
    Bond(
        name          = "DE Govt 2.5 % 2032",
        issuer_type   = "GOVT",
        rating        = "AAA",
        face_value    = 30_000.0,
        coupon_rate   = 0.0250,
        maturity_years= 6.0,
        market_price  = 98.5,
    ),
    Bond(
        name          = "Komerční Banka Covered 3.8 % 2031",
        issuer_type   = "COVERED",
        rating        = "AA",
        face_value    = 20_000.0,
        coupon_rate   = 0.0380,
        maturity_years= 5.0,
        market_price  = 100.2,
    ),
    Bond(
        name          = "ČEZ Corp A 4.2 % 2036",
        issuer_type   = "CORP",
        rating        = "A",
        face_value    = 15_000.0,
        coupon_rate   = 0.0420,
        maturity_years= 12.0,
        market_price  = 101.5,
    ),
    Bond(
        name          = "Slovak Telekom BBB 5.1 % 2030",
        issuer_type   = "CORP",
        rating        = "BBB",
        face_value    = 10_000.0,
        coupon_rate   = 0.0510,
        maturity_years= 4.0,
        market_price  = 100.0,
    ),
]
