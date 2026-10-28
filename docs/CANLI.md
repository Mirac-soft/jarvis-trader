# Gerçek Para Modu

Kod hazır ve **kapalı**. Açılması için aşağıdaki listenin tamamı gerekir.

## Nasıl çalışır
- Kararlar paper ile **aynı motorda** verilir (test edilen kod = canlıdaki kod).
- **ALIM:** bot emir göndermez, panelde **onay isteği** açar (bildirim gelir). Kullanıcı
  "Onayla" derse bot fiyatı, süreyi, frenleri ve acil durdurmayı **yeniden kontrol edip** alır.
  Onaylanan tutardan fazlası asla alınmaz; aynı onay iki kez uygulanamaz. Onay 60 dk geçerli.
- **Hemen ardından borsaya bekleyen stop emri (stopMarket)** konur: bilgisayar kapalıyken,
  internet yokken bile zarar-kes BtcTurk'te çalışır. Stop yükseldikçe borsadaki de taşınır.
- **SATIŞ onaysızdır** (kullanıcı kuralı). Satış önce borsadaki stopu iptal eder, fiyatı sınırlı
  satış dener, dolmayan kısmı piyasa fiyatından satar (çıkış garantili).
- Borsadaki stop kendiliğinden dolarsa fark edilir ve kayda geçer; stop kaybolursa yeniden konur.
- Gerçek maliyet bakiye farkından hesaplanır (komisyon + KDV dahil).
- İki bot aynı hesabı kullanır; emirler hesap kilidiyle sıraya girer.

## Sabit sınırlar
| | |
|---|---|
| Toplam gerçek sermaye | en fazla 300 USDT (kod tavanı) |
| Tek emir | `live.max_order_usdt` (varsayılan 60 USDT) |
| Fiyat bandı | %1 (emir anı), %2 (onay ile uygulama arası) |
| Kayma | en fazla %0,3 (fiyatı sınırlı emir) |
| Coinler | sadece BTC/USDT, ETH/USDT |

## Neden USDT çiftleri (TL değil)
Botlar zamanın çoğunda nakitte bekler. 2020→bugün TL, dolar karşısında %88 değer kaybetti:
TL çiftinde çalışan bot TL bazında +%348 yaptı ama **sadece USDT tutmak bile +%733** idi.
USDT çiftinde bot USD'de +%154 → TL karşılığı ≈ +%2.000. Nakit USDT'de durmalı.

## Minimum sermaye (USDT çiftleri, BtcTurk min emir ~10 USDT)
| Sermaye | İşlem açılabilme | Getiri (2020→) |
|---|---|---|
| 3.000 TL | %61 | +%109 |
| **5.000 TL** | **%96** | **+%150** |
| 10.000 TL | %100 | +%154 |

## Geçiş kontrol listesi
1. [ ] **Kısaltılmış paper dönemi:** en az 2 hafta, art arda 2 kontrol raporu UYUMLU,
       çalışma süresi ≥ %95 (gece bilgisayar uyumamalı).
2. [ ] Hesapta en az **5.000 TL karşılığı USDT** (TL→USDT dönüşümünü kullanıcı BtcTurk'te yapar).
3. [ ] Yeni API anahtarı: **Hesap + Toplam + Al-Sat**, IP kısıtlı.
4. [ ] **Borsa stop testi (denetimli):** küçük alım (onaylı) → dolmayacak uzaklıkta stop koy →
       borsada göründüğünü doğrula → iptal et → sat. stopMarket parametreleri resmi olmayan
       kaynaklara dayanıyor; bu adım ŞART.
5. [ ] Kullanıcının açık "başlat" onayı ile: `live.enabled = true`, `live.capital_usdt` ayarlanır,
       `~/.trader/CANLI_ONAY` dosyası oluşturulur, botlar yeniden başlatılır.
6. [ ] İlk hafta: her gün kontrol, ilk gerçek işlemde birlikte inceleme.

## Acil durumda
- Panelde **ACİL DURDUR**: yeni alım yok; açık pozisyonların borsadaki stopları çalışmaya devam eder.
- Her zaman BtcTurk uygulamasından elle satılabilir.
- Gerçek modu kapatmak: `live.enabled = false` veya `CANLI_ONAY` dosyasını silmek.
