"""Risk yöneticisi: pozisyon büyüklüğü ve frenler.

Usta trader kuralı: "Önce savunma." (Paul Tudor Jones)
Her işlemde sermayenin sabit küçük bir yüzdesi riske girer; böylece
üst üste 10 kayıp bile hesabı bitirmez.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .safety import HARD_LIMITS


@dataclass
class RiskManager:
    risk_per_trade: float
    max_position_fraction: float
    max_daily_loss: float
    max_drawdown: float
    min_notional: float
    fee: float
    peak: float = 0.0
    day_start: float = 0.0
    halted: bool = False          # Kalıcı dur: insan onayı olmadan açılmaz
    day_blocked: bool = False     # Bugünlük yeni işlem yok
    events: list = field(default_factory=list)

    def __post_init__(self):
        # Çift kontrol: ayar dosyası atlatılsa bile tavanlar burada da uygulanır.
        for key, cap in HARD_LIMITS.items():
            if getattr(self, key) > cap:
                raise ValueError(f"{key} tavanı aşıyor")

    def new_day(self, equity: float) -> None:
        self.day_start = equity
        self.day_blocked = False

    def update(self, equity: float, when) -> None:
        self.peak = max(self.peak, equity)
        if not self.halted and equity < self.peak * (1 - self.max_drawdown):
            self.halted = True
            self.events.append((when, "DUR", f"zirveden %{self.max_drawdown*100:.0f} düşüş"))
        if not self.day_blocked and equity < self.day_start * (1 - self.max_daily_loss):
            self.day_blocked = True
            self.events.append((when, "GUN_FRENI", "günlük zarar limiti"))

    def can_open(self) -> bool:
        return not (self.halted or self.day_blocked)

    def size(self, equity: float, entry: float, stop: float) -> float:
        """Zarar-kese gelirse kayıp ~risk_per_trade olacak miktar (coin adedi)."""
        per_unit = (entry - stop) + entry * self.fee * 2
        if per_unit <= 0 or stop <= 0:
            return 0.0
        qty = equity * self.risk_per_trade / per_unit
        cap = equity * self.max_position_fraction / (entry * (1 + self.fee))
        qty = min(qty, cap)
        return qty if qty * entry >= self.min_notional else 0.0
