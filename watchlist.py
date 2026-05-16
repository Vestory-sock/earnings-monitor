"""Konfiguracja watchlisty i progow alertow.
Edytuj WATCHLIST zeby dodac/usunac spolki ktore Cie szczegolnie interesuja.
"""

# Spolki ktore obserwujesz - alert nawet przy mniejszych zaskoczeniach.
# Po Sesji 2a mozesz tu dorzucic: AVGO, MRVL, MU, SMCI, TSM, JPM, V, MA, DIS, NFLX, COST, WMT
WATCHLIST = {
    "NVDA",
    "AMD",
    "AAPL",
    "AMAT",
    "TSLA",
    "MSFT",
    "GOOGL",
    "META",
    "AMZN",
}

# Progi dla watchlisty (mniejsze niespodzianki tez sa ciekawe)
WATCHLIST_EPS_THRESHOLD = 7.0   # %
WATCHLIST_REV_THRESHOLD = 3.0   # %

# Progi dla reszty swiata (tylko duze niespodzianki)
GENERAL_EPS_THRESHOLD = 25.0    # %
GENERAL_REV_THRESHOLD = 10.0    # %

# Minimalny revenue estimate dla "reszty swiata" (eliminacja mikro-capow)
MIN_REVENUE_FOR_GENERAL = 1_000_000_000  # $1B

# CIKs SEC EDGAR dla watchlisty (Central Index Key — 10 cyfr z leading zeros)
# Zweryfikowane: NVDA. Resztę warto spot-checkować na sec.gov/cgi-bin/browse-edgar?CIK=AAPL
WATCHLIST_CIKS = {
    "NVDA":  "0001045810",
    "AMD":   "0000002488",
    "AAPL":  "0000320193",
    "AMAT":  "0000006951",
    "TSLA":  "0001318605",
    "MSFT":  "0000789019",
    "GOOGL": "0001652044",
    "META":  "0001326801",
    "AMZN":  "0001018724",
}
