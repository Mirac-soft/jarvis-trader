"""Herkese açık geçmiş fiyat verisini indirir (API anahtarı KULLANMAZ).

Veri ~/.trader/data içinde saklanır (OneDrive'a gitmez) ve her
indirmede bozukluk kontrolünden geçer.
"""
from __future__ import annotations

import msvcrt
import os
import time
from contextlib import contextmanager

import ccxt
import pandas as pd

from .safety import HOME, setup_logging

DATA_DIR = HOME / "data"
COLS = ["ts", "open", "high", "low", "close", "volume"]
log = setup_logging()


def cache_path(exchange: str, symbol: str, timeframe: str):
    return DATA_DIR / f"{exchange}_{symbol.replace('/', '-')}_{timeframe}.csv.gz"


@contextmanager
def _file_lock(path, timeout=180):
    """İki bot aynı veri dosyasına aynı anda yazmasın."""
    f = open(str(path) + ".lock", "a+")
    f.seek(0)
    deadline = time.time() + timeout
    while True:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            break
        except OSError:
            if time.time() > deadline:
                f.close()
                raise TimeoutError("veri dosyası kilidi alınamadı")
            time.sleep(0.5)
    try:
        yield
    finally:
        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        f.close()


def download(exchange: str, symbol: str, timeframe: str, since: str) -> pd.DataFrame:
    """Eksik kısmı indirip önbelleğe ekler, tüm veriyi döndürür."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with _file_lock(cache_path(exchange, symbol, timeframe)):
        return _download(exchange, symbol, timeframe, since)


def _download(exchange: str, symbol: str, timeframe: str, since: str) -> pd.DataFrame:
    path = cache_path(exchange, symbol, timeframe)
    old = pd.read_csv(path) if path.exists() else pd.DataFrame(columns=COLS)

    # Anahtarsız istemci: sadece herkese açık uç noktalar kullanılabilir.
    ex = getattr(ccxt, exchange)({"enableRateLimit": True})
    step = ex.parse_timeframe(timeframe) * 1000
    cursor = int(old["ts"].iloc[-1]) + step if len(old) else ex.parse8601(f"{since}T00:00:00Z")
    now = ex.milliseconds()

    rows = []
    while cursor < now - step:
        batch = ex.fetch_ohlcv(symbol, timeframe, since=cursor, limit=1000)
        if not batch:
            break
        rows.extend(batch)
        cursor = batch[-1][0] + step
        if len(rows) % 20000 < 1000:
            log.info("indiriliyor %s %s: %s", symbol, timeframe, pd.Timestamp(cursor, unit="ms"))
        time.sleep(ex.rateLimit / 1000)

    new = pd.DataFrame(rows, columns=COLS)
    new = new[new["ts"] < now - step]          # Henüz kapanmamış son bar alınmaz
    df = pd.concat([old, new]).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    validate(df, step)
    tmp = path.with_name(path.name + ".tmp")
    df.to_csv(tmp, index=False, compression="gzip")
    os.replace(tmp, path)          # okuyan biri yarım dosya görmez
    log.info("veri hazır: %d bar, %s", len(df), path)
    return df


def validate(df: pd.DataFrame, step_ms: int) -> None:
    """Bozuk veriyle test yapılmasın diye temel tutarlılık kontrolleri."""
    if df.empty:
        raise ValueError("Veri boş.")
    bad = (df["low"] > df[["open", "close"]].min(axis=1)) | (df["high"] < df[["open", "close"]].max(axis=1))
    if bad.any():
        raise ValueError(f"{int(bad.sum())} barda fiyatlar tutarsız (low/high).")
    if (df[["open", "high", "low", "close"]] <= 0).any().any():
        raise ValueError("Sıfır veya negatif fiyat var.")
    gaps = int((df["ts"].diff().dropna() != step_ms).sum())
    if gaps:
        log.warning("veride %d boşluk var (borsa bakımı vb.), testte dikkate alınır", gaps)


def load(exchange: str, symbol: str, timeframe: str) -> pd.DataFrame:
    df = pd.read_csv(cache_path(exchange, symbol, timeframe))
    df.index = pd.to_datetime(df["ts"], unit="ms", utc=True)
    return df[["open", "high", "low", "close", "volume"]].astype(float)
