# JARVIS Trader

Güvenlik öncelikli kripto spot (kaldıraçsız) bot altyapısı.

## Uygulama
Masaüstündeki **JARVIS** kısayoluna çift tıkla: 🐢 Kaplumbağa ve 🏄 Sörfçü botları
ve panel arka planda penceresiz başlar, JARVIS penceresi açılır. Pencereyi kapatmak
botları durdurmaz; her botun kartında kendi "Durdur" düğmesi var.

## Komutlar
Python: `%USERPROFILE%\.trader\venv\Scripts\python.exe`

| Komut | Ne yapar |
|---|---|
| `python jarvis.py durum` | Güvenlik ayarlarını gösterir |
| `python jarvis.py veri` | Geçmiş fiyat verisini indirir (anahtarsız) |
| `python jarvis.py test` | Stratejileri geçmiş veride test eder |
| `python jarvis.py ekip-test` | Kaplumbağa + Sörfçü ekibini yardımlaşma kurallarıyla test eder |
| `python jarvis.py uygulama` | İki bot + panel + pencere (kısayolun yaptığı) |
| `python jarvis.py bot kaplumbaga` | Tek bir botu çalıştırır (`sorfcu` da olur) |
| `python jarvis.py fren-sifirla kaplumbaga` | Düşüş freninden sonra botu yeniden aç (onay ister) |
| `python -m unittest discover tests` | Güvenlik, motor ve ekip testleri (16 test) |

## Klasörler
- `trader/` — kod (safety, risk, backtest, strateji, veri, durum deposu)
- `config/settings.toml` — ayarlar (gizli bilgi YOK)
- `docs/` — STRATEJI, GUVENLIK, MOBIL
- `%USERPROFILE%\.trader\` — veriler, loglar, veritabanı, anahtarlar (OneDrive dışı)

## Acil durdurma
`%USERPROFILE%\.trader\DUR` adlı boş bir dosya oluştur → bot işlem açmaz.
