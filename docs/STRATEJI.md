# Strateji Analizi

## Örnek aldığımız ustalar ve kuralları

| Usta | Ne yaptı | Bottaki karşılığı |
|---|---|---|
| **Richard Dennis & Kaplumbağalar** (1983) | Sıfırdan eğittiği öğrencilere basit kural seti verdi: kanal kırılımında al, ATR'ye göre pozisyon aç, her işlemde sermayenin ~%1'ini riske at. Grup yüz milyonlarca dolar kazandı. | Strateji A (55 bar kırılım, 20 bar çıkış, ATR stop, %1 risk) |
| **Ed Seykota** | "Kayıpları kes, kazananları koştur." Trend takibinin babası. | İz süren zarar-kes: kâr büyüdükçe stop yukarı taşınır, asla aşağı inmez |
| **Paul Tudor Jones** | "Önce savunma." Kaybetmemeye odaklan, kazanç sonra gelir. | Sabit tavanlar, günlük zarar freni, %25 düşüşte tam durma |
| **Jim Simons (Renaissance)** | Duygusuz, tamamen sistematik; her fikir veriyle test edilir. | Hiçbir kural test edilmeden canlıya çıkmaz; görülmemiş veride kontrol |
| **Larry Hite** | "Hiçbir işlemde sermayenin %1'inden fazlasını riske atma." | `risk_per_trade = 0.01` |

Ortak ders: Hepsi **hayatta kalmayı** kazançtan önce koyar. Para kazananlar en
iyi tahmini yapanlar değil, kötü günlerde batmayanlardır.

## İlk test sonuçları (BTC/USDT, 2020-01 → 2026-10, 1000 USDT, %0,20 komisyon + %0,05 kayma)

| | A: Kaplumbağa (4s) | B: Geri çekilme (15dk) | Al-Tut |
|---|---|---|---|
| Toplam getiri (tüm dönem) | **+61%** | +5% | +1014% |
| En büyük düşüş | **-13%** | -25% (fren çalıştı) | -77% |
| Görülmemiş veri (2024+) | +8%, kâr faktörü 1,24 | **-17%**, kâr faktörü 0,71 | +90% |
| İşlem sayısı | 127 | 180 (fren sonrası durdu) | 1 |
| Piyasada kalma süresi | %21 | — | %100 |

### Yorum
1. **B (sık işlem) kaybettiriyor.** Komisyon kârı yiyor; görülmemiş veride de
   zarar etti. Saniyelik/dakikalık işlemin neden işe yaramadığının kanıtı.
   Fren mekanizması tam olması gerektiği gibi çalıştı ve kaybı %25'te durdurdu.
2. **A (Kaplumbağa) sağlam bir temel.** Kâr faktörü 1,65; düşüş sadece %13.
   Zamanın sadece %21'inde, sermayenin küçük bir kısmıyla piyasada.
3. **Al-tut daha çok kazandırdı, ama %77 düşüş yaşattı.** 1000 lira 230 liraya
   inerken çoğu insan panikle satar. Bot o düşüşü %13'te tuttu.
4. Görülmemiş dönemde A'nın kârı azaldı; bu normal ama izlenmeli.

## Güçlendirme testi (2026-10-28) — önceden belirlenen 3 varyant

| Varyant | 2020–2023 | 2024+ (görülmemiş) | Tümü | En büyük düşüş |
|---|---|---|---|---|
| V1: sadece BTC | +49% | +8% | +61% | -13% |
| **V2: BTC + ETH** ✓ | **+82%** | **+36%** | **+148%** | -20% |
| V3: BTC + ETH + 4 kademe | +86% | +54% | +86% | -25% (fren → durdu) |
| BTC al-tut | +443% | +90% | +937% | -77% |

**Karar: V2 canlıya (paper) çıktı.** İki dönemde de tutarlı kâr. V3, 2023'te
%25 düşüş frenine takılıp kendini kapattı; frene takılan strateji canlıya çıkmaz.
2024+ sonucu iyi görünse de seçimi değiştirmedik (görülmemiş veriye bakarak
seçim yapmak, testi anlamsız kılar).

## Bot ekibi (2026-10-28): 🐢 Kaplumbağa + 🏄 Sörfçü

Sermaye ikiye bölündü, iki ayrı bot ayrı süreçlerde çalışıyor:

| Bot | Mantık | Risk / fren | Sermaye |
|---|---|---|---|
| 🐢 Kaplumbağa | 4s: 55 bar zirve kırılımı, EMA200 üstü, 20 bar dip çıkışı | %1 / %25 | 500 $ |
| 🏄 Sörfçü | 1g: kapanış > EMA50 > EMA200 iken bin, EMA50 altında in | %2 / %30 | 500 $ |

"Daha agresif" adaylar (2 kat risk, hızlı kaplumbağa) testte hem daha az kazandı
hem frene takıldı. Sörfçü agresif değil, **farklı karakterde**: ikisi farklı
zamanlarda kazandığı için birbirini dengeliyor.

### Yardımlaşma kuralları testi (ekip, 1000 $)
| Kural | Tümü | 2024+ | En büyük düşüş |
|---|---|---|---|
| bağımsız | +157% | +24% | -14% |
| büyük resim (🐢 sadece 🏄 onaylarsa alır) | +118% | +16% | -15% |
| risk paylaşımı (aynı coinde yarım risk) | +113% | +20% | -12% |
| **fren dayanışması** ✓ | **+157%** | **+24%** | **-14%** |
| hepsi | +87% | +12% | -10% |

Ders: botların birbirinin alım kararına karışması kârı düşürdü (Kaplumbağa'nın
değeri trendi Sörfçü'den ÖNCE yakalamasında). Fren dayanışması hiç kazanç
kaybettirmeden kriz anı sigortası sağlıyor → canlıda bu kural var. Botlar ayrıca
haber panosunda görüşlerini paylaşıyor (panelde "Ekip telsizi").

## Eski sonraki adımlar
1. Strateji A'yı güçlendir — sadece mantıklı ve az parametreli eklemelerle:
   birden fazla coin (BTC + ETH), Kaplumbağaların kademeli alım (pyramiding) kuralı.
   Her değişiklik önce 2020-2023'te, sonra 2024+ verisinde kontrol edilecek.
2. Kâğıt üzerinde canlı test (paper): bot canlı fiyatla, sahte parayla çalışır.
3. Mobil izleme API'si (salt-okunur).
4. En az 4-8 hafta başarılı paper sonrası, kullanıcı onayıyla küçük gerçek para.
