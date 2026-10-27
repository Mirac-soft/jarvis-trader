# Mobil İzleme Altyapısı

Masaüstü uygulaması (JARVIS paneli) ile telefon aynı API'yi kullanacak.
API şu an çalışıyor; telefona açmak için sadece aşağıdaki "Telefona açma"
adımları kaldı.

## Mimari

```
 Paper bot ──yazar──▶ state.db (SQLite, ~/.trader)
                          │ okur (mode=ro, yazamaz)
                          ▼
              Panel sunucusu: trader/dashboard.py (127.0.0.1:8765)
                ├── Bilgisayar: Edge uygulama penceresi (JARVIS kısayolu)
                └── Telefon (sonra): Tailscale özel ağ üzerinden aynı sayfa
```

## Hazır API (salt-okunur)
| Uç nokta | Ne verir |
|---|---|
| `GET /api/overview` | Bot durumu, sermaye, fiyatlar, pozisyonlar, analiz, frenler |
| `GET /api/equity` | Sermaye eğrisi (dakikalık) |
| `GET /api/events?after=ID` | Olay akışı (yeni olaylar → bildirim) |
| `GET /api/trades` | İşlem geçmişi |
| `GET /api/backtests` | Test sonuçları |
| `POST /api/control` | Sadece: acil durdur aç/kapa, botu başlat/durdur. **Emir verilemez.** |

Arayüz (`app/`) telefon ekranına da uyumlu (responsive).

## Telefona açma (yapılacak)
1. Telefona Tailscale kur, aynı hesaba bağla.
2. `tailscale serve` ile paneli SADECE tailnet'e aç (Funnel DEĞİL — internete açmaz).
3. Panelin Host/Origin kontrolüne tailnet adresini ekle.
4. Ek kimlik doğrulama: uzun rastgele token (çerezde, HttpOnly).
5. Telefonda sayfayı "Ana ekrana ekle" → uygulama gibi açılır (PWA).
6. Uzaktan "Botu başlat" komutu kapalı kalacak; telefondan sadece izleme + ACİL DURDUR.

## Neden Tailscale Funnel değil
Funnel servisi internete açar (mail asistanında kullanılan yöntem). Trading
paneli için gereksiz risk: sadece kendi cihazlarının girebildiği özel ağ yeterli.
