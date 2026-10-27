"""Teknik göstergeler. Hepsi sadece geçmiş veriyi kullanır (geleceği görmez)."""
from __future__ import annotations

import pandas as pd


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _rma(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False).mean()   # Wilder ortalaması


def atr(df: pd.DataFrame, n: int) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1).max(axis=1)
    return _rma(tr, n)


def rsi(close: pd.Series, n: int) -> pd.Series:
    d = close.diff()
    up, down = _rma(d.clip(lower=0), n), _rma(-d.clip(upper=0), n)
    return 100 - 100 / (1 + up / down.replace(0, 1e-12))


def adx(df: pd.DataFrame, n: int) -> pd.Series:
    up = df["high"].diff()
    down = -df["low"].diff()
    plus = up.where((up > down) & (up > 0), 0.0)
    minus = down.where((down > up) & (down > 0), 0.0)
    a = atr(df, n)
    pdi, mdi = 100 * _rma(plus, n) / a, 100 * _rma(minus, n) / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, 1e-12)
    return _rma(dx, n)


def resample(df: pd.DataFrame, rule: str) -> pd.DataFrame:
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    return df.resample(rule).agg(agg).dropna()


def align_closed(htf: pd.Series | pd.DataFrame, index: pd.DatetimeIndex):
    """Büyük zaman dilimi değerini küçük barlara taşır.

    shift(1): bir bar ancak KAPANDIKTAN sonra kullanılabilir. Bu, testlerde
    en sık yapılan "geleceği görme" hatasını engeller.
    """
    return htf.shift(1).reindex(index, method="ffill")
