"""Backtest: stratejiyi geçmiş veride, canlı botla AYNI motorla çalıştırır.

Kötümser varsayımlar (bilerek):
- Sinyal bar kapanışında, işlem bir sonraki barın AÇILIŞINDA.
- Bar içinde zarar-kes seviyesine değildiyse zarar-kes çalışmış sayılır.
- Fiyat stopun altında açılırsa (gap) satış açılış fiyatından olur.
- Her alım ve satımda komisyon + kayma ödenir.
"""
from __future__ import annotations

import pandas as pd

from .engine import Portfolio, Trade  # noqa: F401  (Trade dışarıya da açık)


def run_portfolio(sigs: dict[str, pd.DataFrame], cfg: dict, start_equity: float, max_units: int = 1):
    pf = Portfolio(cfg, start_equity, list(sigs), max_units)
    frames = []
    for s, df in sigs.items():
        f = df.copy()
        f["symbol"] = s
        frames.append(f)
    allbars = pd.concat(frames).sort_index(kind="stable")

    times, equity = [], []
    prev_t = None
    for row in allbars.itertuples():
        t = row.Index
        if prev_t is not None and t != prev_t:
            times.append(prev_t)
            equity.append(pf.equity())
        pf.open_phase(row.symbol, t, row.open)
        pf.close_phase(row.symbol, t, row.low, row.close, row)
        prev_t = t

    for s, b in pf.books.items():          # test sonunda açık pozisyonları kapat
        if b.qty > 0:
            pf._sell(s, prev_t, pf.last[s], "test-sonu")
    times.append(prev_t)
    equity.append(pf.equity())
    return pd.Series(equity, index=pd.DatetimeIndex(times), name="equity"), pf.trades, pf.rm.events


def run(sig: pd.DataFrame, cfg: dict, start_equity: float, max_units: int = 1):
    """Tek coin için kısayol."""
    return run_portfolio({"X": sig}, cfg, start_equity, max_units)
