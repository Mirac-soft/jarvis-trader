# Güvenlik Duvarları

Her katman bir öncekinden bağımsız çalışır; biri atlatılsa bile diğeri durdurur.

## Katman 1 — Bilgisayar ve dosyalar
- Gizli anahtarlar, veriler, sanal ortam ve loglar **OneDrive dışında**:
  `%USERPROFILE%\.trader`. Buluta senkronize olmaz.
- Kod, anahtar dosyası OneDrive içindeyse çalışmayı reddeder.
- Loglarda anahtar benzeri uzun diziler otomatik maskelenir (`***GIZLI***`).
- Python paketleri **sabit sürümlü** ve en az 14 günlük; kurulumda OSV açık
  veritabanında tarandı (27 paket, bilinen açık yok).

## Katman 2 — Ayar duvarı (`trader/safety.py`)
- Sabit tavanlar: işlem başı risk ≤ %2, kaldıraç yok, günlük zarar ≤ %5,
  toplam düşüş ≤ %30. Ayar dosyası bunları aşarsa program **hiç başlamaz**.
- Sadece izinli semboller (BTC, ETH / USDT, TRY).
- Gerçekçi olmayan düşük komisyon reddedilir (testler yalancı iyi çıkmasın).
- **Gerçek para modu kodda kapalı.** Açmak için kod değişikliği ve kullanıcı onayı gerekir.

## Katman 3 — Risk yöneticisi (`trader/risk.py`)
- Her işlemde sermayenin %1'i riske girer.
- Pozisyon asla sermayeden büyük olamaz.
- Gün içinde %3 kayıp → o gün yeni işlem yok.
- Zirveden %25 düşüş → bot tamamen durur, insan onayı olmadan açılmaz.
- Tavanlar risk yöneticisinde ikinci kez kontrol edilir.

## Katman 4 — Acil durdurma
- `%USERPROFILE%\.trader\DUR` adlı dosya oluşturulursa bot hiçbir işlem açmaz.

## Katman 5 — Borsa (gerçek paraya geçerken eklenecek)
- API anahtarı: **para çekme izni KAPALI**, sadece senin IP adresine izinli.
- Emir öncesi kontroller: fiyat son fiyattan %2'den fazla sapamaz, emir
  büyüklüğü tavanı, dakikada en fazla N emir.
- Borsa hesabında iki adımlı doğrulama (uygulama ile, SMS değil).

## Katman 6 — Masaüstü paneli (`trader/dashboard.py`)
- Sadece `127.0.0.1` dinler: ağdaki başka cihazlar erişemez.
- Veritabanını salt-okunur açar.
- Host kontrolü (DNS rebinding), Origin + özel başlık kontrolü (başka sitelerin
  tarayıcın üzerinden komut göndermesi engelli), sıkı CSP başlıkları.
- Panelden alım-satım emri verilemez; sadece acil durdur ve botu aç/kapa.
- Düşüş freni panelden sıfırlanamaz: `jarvis.py fren-sifirla` + "EVET" onayı gerekir.
- Test edildi (2026-10-28): yabancı Host, başlıksız komut, yabancı site, emir
  denemesi, dosya okuma denemesi → hepsi reddedildi.

## Katman 7 — Canlı bot (`trader/paper.py`)
- Borsaya giriş yapmaz, anahtar kullanmaz: sadece herkese açık fiyatları okur.
- Aynı anda tek kopya (kilit dosyası); durum her döngüde güvenli yazılır,
  kapanıp açılınca pozisyon ve frenler kaybolmaz.
- Bağlantı hatasında çökmez; bekleyip tekrar dener, hatayı panele bildirir.
- Penceresiz çalışır (arka planda terminal açılıp kapanmaz).

## Katman 8 — Bot ekibi (`trader/team.py`)
- Her botun kendi sermayesi, riski ve düşüş freni var; bot ayarları da sabit tavanlara tabi.
- Sermaye payları toplamı %100 olmak zorunda (fazladan para "yaratılamaz").
- Yardımlaşma riski **asla artıramaz**: sadece azaltır veya engeller (testli).
- Haber panosundan 15 dakikadan eski haber "haber yok" sayılır.
- Fren dayanışması: bir botun freni çalışırsa diğeri de yeni işlem açmaz.
- İki bot aynı veri dosyasına aynı anda yazamaz (dosya kilidi + atomik yazma).
- Haberleşme: bir haber stopu sadece YUKARI çekebilir (asla aşağı indiremez),
  geçersiz/bozuk haber (bilinmeyen coin, tür veya gönderen) yok sayılır, botu düşüremez.
  Haberle gelen fiyat değil, alan botun kendi güncel fiyatı kullanılır.

## Katman 9 — Kontrol raporu (`trader/report.py`)
- Canlı botların kararlarını aynı dönemde test motorunun kararlarıyla karşılaştırır:
  eksik/fazla işlem, %1'den büyük fiyat farkı, farklı açık pozisyon → "FARK VAR".
- Botun kapalı kaldığı süreleri bulur (kapalıyken stoplar çalışmaz) → "UYARI".
- Haftalık otomatik; gerçek paraya geçiş şartı raporların UYUMLU olması.

## Katman 10 — Sürüm kaydı
- Kod yerel git deposunda; her değişiklik kayıtlı, geri alınabilir.
- `.gitignore` gizli dosyaları (`.env`, `secrets*`, anahtarlar) ve verileri dışarıda tutar.

## Katman 11 — Mobil erişim (bkz. MOBIL.md)
- Telefon sadece **okur**. Emir verme yetkisi yok.
- İnternete açık değil; sadece Tailscale özel ağı üzerinden erişim.

## Testler
`tests/` klasöründeki testler bu kuralların bozulmadığını her değişiklikte kontrol eder:
canlı mod engeli, risk tavanları, OneDrive engeli, kaldıraç engeli, geleceği görme
hatası ve komisyonun gerçekten kesildiği.
