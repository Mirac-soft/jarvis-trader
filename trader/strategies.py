"""Stratejiler: her bar KAPANIŞINDA sinyal üretir, işlem bir SONRAKİ barın açılışında yapılır.

Çıktı sütunları:
  entry      -> bu bar kapanışında alım sinyali
  exit       -> bu bar kapanışında çıkış sinyali
  stop_dist  -> giriş fiyatı ile ilk zarar-kes arasındaki mesafe
  trail_dist -> en yüksek kapanıştan iz süren zarar-kes mesafesi
"""
from __future__ import annotations

import pandas as pd

from . import indicators as ind


def turtle(df15: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Strateji A - Kaplumbağa: 4 saatlik 55 bar zirvesinin kırılması.

    Richard Dennis'in 1983 Kaplumbağa deneyindeki kanal kırılımı mantığı.
    Sadece uzun vadeli trend yukarıyken (EMA200 üstü) alır.
    """
    df = ind.resample(df15, "4h")
    a = ind.atr(df, p["atr_period"])
    hi = df["high"].rolling(p["entry_channel"]).max().shift(1)
    lo = df["low"].rolling(p["exit_channel"]).min().shift(1)
    trend = df["close"] > ind.ema(df["close"], p["trend_ema"])
    out = df.copy()
    out["entry"] = (df["close"] > hi) & trend
    out["exit"] = df["close"] < lo
    out["stop_dist"] = p["stop_atr"] * a
    out["trail_dist"] = p["trail_atr"] * a
    out["add_step"] = p["add_step_atr"] * a      # Kaplumbağa: her 0,5 ATR yükselişte bir birim ekle
    # Panelde gösterilecek bilgiler
    out["breakout_level"], out["exit_level"], out["trend_level"] = hi, lo, ind.ema(df["close"], p["trend_ema"])
    out["trend_up"] = trend
    return out.dropna()


def surfer(df15: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Strateji C - Trend Sörfçüsü: günlük grafikte büyük dalgaya biner.

    Günlük kapanış EMA50'nin, EMA50 de EMA200'ün üstündeyse dalga var: biner.
    Kapanış EMA50'nin altına inerse dalga bitti: iner.
    Geniş stop (3 ATR) ve iz süren stop (4 ATR): küçük sallantılarda düşmez.
    """
    d = ind.resample(df15, "1D")
    fast, slow, a = ind.ema(d["close"], p["fast_ema"]), ind.ema(d["close"], p["slow_ema"]), ind.atr(d, p["atr_period"])
    out = d.copy()
    out["trend_up"] = (d["close"] > fast) & (fast > slow)
    out["entry"] = out["trend_up"]
    out["exit"] = d["close"] < fast
    out["stop_dist"] = p["stop_atr"] * a
    out["trail_dist"] = p["trail_atr"] * a
    out["breakout_level"], out["exit_level"], out["trend_level"] = fast, fast, slow   # panel bilgisi
    return out.dropna()


def pullback(df15: pd.DataFrame, p: dict) -> pd.DataFrame:
    """Strateji B - Trend + geri çekilme: büyük trend yukarıyken küçük düşüşü alır.

    4s: EMA50 > EMA200, fiyat EMA50 üstünde ve ADX güçlü trend diyor.
    15dk: RSI aşırı satımdan geri dönüyor (geri çekilme bitti).
    """
    h4 = ind.resample(df15, "4h")
    h4_fast, h4_slow = ind.ema(h4["close"], p["fast_ema"]), ind.ema(h4["close"], p["slow_ema"])
    up = (h4_fast > h4_slow) & (h4["close"] > h4_fast) & (ind.adx(h4, 14) > p["adx_min"])
    trend = ind.align_closed(up.astype(float), df15.index).fillna(0) > 0.5

    h1 = ind.resample(df15, "1h")
    atr1h = ind.align_closed(ind.atr(h1, p["atr_period"]), df15.index)

    r = ind.rsi(df15["close"], p["rsi_period"])
    trigger = (r.shift(1) < p["rsi_trigger"]) & (r >= p["rsi_trigger"])

    out = df15.copy()
    out["entry"] = trend & trigger
    out["exit"] = ~trend                       # Büyük trend bozulunca çık
    out["stop_dist"] = p["stop_atr"] * atr1h
    out["trail_dist"] = p["trail_atr"] * atr1h
    return out.dropna()
