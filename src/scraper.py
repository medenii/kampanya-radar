"""Playwright tabanlı, JS-render eden kazıyıcı.

Tasarım kararları:
  * Tek bir tarayıcı örneği açılır, her hedef için ayrı context (izolasyon).
  * Bir hedef patlarsa diğerleri etkilenmez (per-target try/except).
  * Liste sayfasından SADECE (url, title) çıkarılır -> ucuz.
  * Detay sayfası YALNIZCA yeni bulunan item'lar için açılır -> hem hızlı hem
    LLM token tasarrufu sağlar.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlparse, urlunparse

from playwright.async_api import Browser, Page, async_playwright

from .config import Target, settings

log = logging.getLogger(__name__)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Takip parametrelerini temizle -> aynı kampanya iki kez "yeni" görünmesin
TRACKING_PARAMS = re.compile(
    r"^(utm_|gclid|fbclid|mc_cid|mc_eid|ref|referrer|source|_ga)", re.I
)

NOISE_TITLES = {
    "devamı", "detay", "detaylar", "incele", "daha fazla", "tümünü gör",
    "başvur", "hemen başvur", "kampanyalar", "", "daha fazla göster",
}


@dataclass
class ScrapedItem:
    source: str
    title: str
    url: str
    detail_text: str = ""

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "detail_text": self.detail_text,
        }


@dataclass
class ScrapeResult:
    target: Target
    items: list[ScrapedItem]
    error: str = ""


# --------------------------------------------------------------------------- #
# Yardımcılar
# --------------------------------------------------------------------------- #
def normalize_url(base: str, href: str) -> str:
    """Mutlak, fragment'siz, takip parametresiz, sondaki '/' atılmış URL."""
    if not href:
        return ""
    absolute = urljoin(base, href.strip())
    absolute, _ = urldefrag(absolute)
    parsed = urlparse(absolute)
    if parsed.scheme not in ("http", "https"):
        return ""
    query_parts = [
        kv for kv in parsed.query.split("&")
        if kv and not TRACKING_PARAMS.match(kv.split("=", 1)[0])
    ]
    path = parsed.path.rstrip("/") or "/"
    return urlunparse(
        (parsed.scheme, parsed.netloc.lower(), path, "", "&".join(query_parts), "")
    )


def clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip()


def _matches(url: str, includes: list[str], excludes: list[str]) -> bool:
    if includes and not any(re.search(p, url, re.I) for p in includes):
        return False
    if any(re.search(p, url, re.I) for p in excludes):
        return False
    return True


# --------------------------------------------------------------------------- #
# Sayfa etkileşimleri
# --------------------------------------------------------------------------- #
async def _prepare_page(page: Page, target: Target) -> None:
    """Lazy-load / infinite scroll / 'daha fazla' butonlarını çöz."""
    if target.wait_selector:
        try:
            await page.wait_for_selector(target.wait_selector, timeout=15000)
        except Exception:
            log.warning("[%s] wait_selector bulunamadı: %s", target.name, target.wait_selector)

    if target.wait_ms:
        await page.wait_for_timeout(target.wait_ms)

    if target.click_more:
        for i in range(target.max_clicks):
            try:
                btn = page.locator(target.click_more).first
                if not await btn.is_visible(timeout=3000):
                    break
                await btn.click(timeout=5000)
                await page.wait_for_timeout(1500)
            except Exception:
                log.debug("[%s] click_more %d. denemede durdu", target.name, i + 1)
                break

    if target.scroll:
        previous = 0
        for _ in range(12):
            await page.mouse.wheel(0, 4000)
            await page.wait_for_timeout(900)
            height = await page.evaluate("document.body.scrollHeight")
            if height == previous:
                break
            previous = height
        await page.wait_for_timeout(600)


async def _extract_items(page: Page, target: Target) -> list[ScrapedItem]:
    """Önce item_selector, o yoksa tüm <a>'lar + pattern filtresi."""
    base = page.url
    raw: list[tuple[str, str]] = []

    if target.item_selector:
        cards = await page.locator(target.item_selector).all()
        for card in cards:
            try:
                link = card.locator(target.link_selector or "a").first
                href = await link.get_attribute("href")
                if target.title_selector:
                    title = await card.locator(target.title_selector).first.inner_text()
                else:
                    title = await card.inner_text()
                raw.append((href or "", title or ""))
            except Exception:
                continue
    else:
        raw = await page.eval_on_selector_all(
            "a[href]",
            """els => els.map(e => [
                 e.getAttribute('href') || '',
                 (e.innerText || e.getAttribute('title') || e.getAttribute('aria-label') || '')
               ])""",
        )

    items: dict[str, ScrapedItem] = {}
    for href, title in raw:
        url = normalize_url(base, href)
        if not url or not _matches(url, target.include_patterns, target.exclude_patterns):
            continue
        title = clean_text(title)[:220]
        if title.lower() in NOISE_TITLES or len(title) < 4:
            # Başlık işe yaramazsa slug'dan üret
            slug = urlparse(url).path.rstrip("/").split("/")[-1]
            title = clean_text(slug.replace("-", " ").replace("_", " ")).title()
        if len(title) < 4:
            continue
        # Aynı URL birden çok kez geçerse en uzun başlığı sakla
        existing = items.get(url)
        if existing and len(existing.title) >= len(title):
            continue
        items[url] = ScrapedItem(source=target.name, title=title, url=url)

    return list(items.values())


async def scrape_target(browser: Browser, target: Target) -> ScrapeResult:
    context = await browser.new_context(
        user_agent=UA,
        locale="tr-TR",
        timezone_id="Europe/Istanbul",
        viewport={"width": 1440, "height": 1000},
        ignore_https_errors=True,
    )
    # Görsel/font indirme -> gereksiz, engelle (hız + kota)
    await context.route(
        re.compile(r"\.(png|jpe?g|gif|webp|svg|woff2?|ttf|mp4|avi)$"),
        lambda route: asyncio.ensure_future(route.abort()),
    )
    page = await context.new_page()
    page.set_default_timeout(settings.nav_timeout_ms)

    try:
        last_err: Exception | None = None
        for attempt in range(1, 4):  # 3 deneme
            try:
                await page.goto(
                    target.url, wait_until="domcontentloaded",
                    timeout=settings.nav_timeout_ms,
                )
                try:
                    await page.wait_for_load_state("networkidle", timeout=12000)
                except Exception:
                    pass  # networkidle bazı sitelerde hiç oluşmaz, sorun değil
                last_err = None
                break
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                log.warning("[%s] %d. deneme başarısız: %s", target.name, attempt, exc)
                await asyncio.sleep(3 * attempt)
        if last_err:
            raise last_err

        await _prepare_page(page, target)
        items = await _extract_items(page, target)
        log.info("[%s] %d item bulundu", target.name, len(items))
        return ScrapeResult(target=target, items=items)

    except Exception as exc:  # noqa: BLE001
        log.error("[%s] HATA: %s", target.name, exc)
        return ScrapeResult(target=target, items=[], error=f"{type(exc).__name__}: {exc}")
    finally:
        await context.close()


async def fetch_detail(browser: Browser, item: ScrapedItem, limit: int = 6000) -> None:
    """Yeni bir item'ın detay sayfasındaki metni çeker (LLM'e girdi olacak)."""
    context = await browser.new_context(user_agent=UA, locale="tr-TR")
    try:
        page = await context.new_page()
        page.set_default_timeout(settings.nav_timeout_ms)
        await page.goto(item.url, wait_until="domcontentloaded")
        try:
            await page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        await page.wait_for_timeout(1200)
        text = await page.evaluate(
            """() => {
                 document.querySelectorAll('script,style,nav,footer,header,noscript')
                   .forEach(e => e.remove());
                 const main = document.querySelector('main,[role=main],article,.content,#content');
                 return (main || document.body).innerText;
               }"""
        )
        item.detail_text = clean_text(text)[:limit]
    except Exception as exc:  # noqa: BLE001
        log.warning("Detay alınamadı (%s): %s", item.url, exc)
        item.detail_text = ""
    finally:
        await context.close()


async def run_scrape(targets: list[Target]) -> list[ScrapeResult]:
    """Tüm hedefleri sırayla tarar (bot-koruma tetiklememek için seri)."""
    results: list[ScrapeResult] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=settings.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-blink-features=AutomationControlled"],
        )
        try:
            for target in targets:
                results.append(await scrape_target(browser, target))
                await asyncio.sleep(2)
        finally:
            await browser.close()
    return results


async def enrich_details(items: list[ScrapedItem]) -> None:
    """Yeni item'ların detay metinlerini paralel (max 3) olarak doldurur."""
    if not items:
        return
    sem = asyncio.Semaphore(3)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=settings.headless,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

        async def worker(it: ScrapedItem) -> None:
            async with sem:
                await fetch_detail(browser, it)

        try:
            await asyncio.gather(*(worker(i) for i in items))
        finally:
            await browser.close()
