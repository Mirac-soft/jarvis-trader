"""Ağ ayarı: borsa bağlantıları her zaman IPv4 üzerinden.

Sebep: API anahtarı IPv4 adresine kısıtlı (IP kısıtı). Bilgisayar IPv6 ile bağlanırsa
borsa isteği tanımadığı bir adresten gelmiş sayar ve reddeder. Ev IPv6 adresleri sık
değiştiği için izinli listeye eklemek de güvenli bir çözüm değil.

Sadece bu programın (Python süreci) bağlantılarını etkiler; bilgisayarın geri kalanına dokunmaz.
"""
from __future__ import annotations

import socket

import urllib3.util.connection as _conn

_applied = False


def force_ipv4() -> None:
    global _applied
    if not _applied:
        _conn.allowed_gai_family = lambda: socket.AF_INET
        _applied = True
