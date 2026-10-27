"""Sonuç ölçüleri: getiri, en büyük düşüş, isabet oranı vb."""
from __future__ import annotations

import numpy as np
import pandas as pd


def summarize(equity: pd.Series, trades: list, start_equity: float) -> dict:
    daily = equity.resample("1D").last().dropna()
    rets = daily.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    final = float(equity.iloc[-1])
    dd = float((equity / equity.cummax() - 1).min())
    pnls = np.array([t.pnl for t in trades]) if trades else np.array([0.0])
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    return {
        "son_sermaye": final,
        "toplam_getiri_%": (final / start_equity - 1) * 100,
        "yillik_getiri_%": ((final / start_equity) ** (1 / years) - 1) * 100,
        "en_buyuk_dusus_%": dd * 100,
        "sharpe": float(rets.mean() / rets.std() * np.sqrt(365)) if rets.std() > 0 else 0.0,
        "islem_sayisi": len(trades),
        "isabet_%": len(wins) / max(len(trades), 1) * 100,
        "kar_faktoru": float(wins.sum() / -losses.sum()) if losses.sum() < 0 else float("inf"),
        "ort_kazanc": float(wins.mean()) if len(wins) else 0.0,
        "ort_kayip": float(losses.mean()) if len(losses) else 0.0,
    }


def buy_and_hold(close: pd.Series, start_equity: float, fee: float) -> pd.Series:
    return start_equity * (1 - fee) ** 2 * close / close.iloc[0]
