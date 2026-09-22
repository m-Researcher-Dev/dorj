
# Searcher — قرارداد لایه

## جایگاه در استک

```
ai.py         ←  اسم محصول از توضیحات
scorer.py     ←  list[Link] → best Link
extractor.py  ←  URL → list[Link]
searcher.py   ←  name → list[URL]        ← این لایه
browser/httpx ←  (Playwright)
```

## Interface عمومی

```python
async def search(query: str) -> list[str]: ...
```

- **Input:** اسم محصول (`str`)
- **Output:** لیست URL صفحه‌های محصول (`list[str]`)
- **تنها نقطهٔ ورود.** هیچ تابع دیگری از این فایل نباید import شود.

## چیزهایی که لایهٔ بالا نباید بداند

- کدام سایت‌ها جستجو شدند
- Playwright استفاده شد یا httpx
- فرم POST بود یا GET
- ترتیب نتایج
- کدام سایت fail شد

## ساختار داخلی (private)

```
searcher.py
├── _PRODUCT_PATTERNS : dict[site, regex]        # الگوی URL محصول هر سایت
├── _search_soft98(page, query)      -> list[str]
├── _search_p30download(page, query) -> list[str]
├── _search_yasdl(page, query)       -> list[str]
└── search(query)                    -> list[str]   # public
```

## قائده‌ها

1. الگوی URL محصول را **قبل از کد** دستی از سایت واقعی استخراج کن.
2. استخراج لینک با **regex روی `a[href]`**، نه CSS selector روی لیست نتایج.
3. `searcher` فقط URL برمی‌گرداند. پارس محتوا وظیفهٔ `extractor` است.
4. تغییر در یک سایت فقط `_PRODUCT_PATTERNS` و تابع `_search_<site>` را لمس می‌کند. لایهٔ بالا دست نمی‌خورد.
5. موازی‌سازی بین سایت‌ها **داخل** `search()` است، نه در لایهٔ بالا.
6. dedupe فقط داخل هر سایت (`seen`). dedupe بین سایت‌ها معنا ندارد چون URLها یکسان نیستند.

## سؤالاتی که باید قبل از پیاده‌سازی جواب داده شوند

- [ ] امضای `search()` قطعی شد؟ (فقط query و list[str])
- [ ] اگر یک سایت fail شود، `search()` exception می‌دهد یا لیست بقیه را برمی‌گرداند؟
- [ ] الگوی URL هر سه سایت دستی تأیید شد؟
