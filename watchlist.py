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
