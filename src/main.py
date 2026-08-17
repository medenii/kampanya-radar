"""Orkestrasyon: scrape -> diff -> detay -> LLM -> e-posta -> state kaydet."""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .config import load_targets, settings
from .llm import summarize_all
from .mailer import send_campaigns, send_error_report
from .scraper import ScrapedItem, enrich_details, run_scrape
from .state import StateStore


def setup_logging(debug: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s | %(levelname)-7s | %(name)-12s | %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


log = logging.getLogger("main")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Banka kampanya takip agent'ı")
    p.add_argument("--dry-run", action="store_true",
                   help="E-posta gönderme ve state'i güncelleme; sadece raporla")
    p.add_argument("--no-llm", action="store_true", help="LLM çağrısı yapma")
    p.add_argument("--only", default="", help="Sadece adı eşleşen hedefi tara")
    p.add_argument("--reset-source", default="",
                   help="Bu kaynağın kayıtlarını sil (yeniden bootstrap için)")
    p.add_argument("--debug", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(args.debug)

    targets = load_targets()
    if args.only:
        targets = [t for t in targets if args.only.lower() in t.name.lower()]
    if not targets:
        log.error("Taranacak hedef yok.")
        return 1

    store = StateStore()

    if args.reset_source:
        keys = [k for k, v in store.data["seen"].items()
                if v.get("source") == args.reset_source]
        for k in keys:
            del store.data["seen"][k]
        store.save()
        log.info("%s için %d kayıt silindi.", args.reset_source, len(keys))
        return 0

    log.info("=== %d hedef taranıyor ===", len(targets))
    results = asyncio.run(run_scrape(targets))

    errors = [f"{r.target.name}: {r.error}" for r in results if r.error]
    new_to_notify: list[ScrapedItem] = []
    bootstrap_items: list[ScrapedItem] = []
    stats: dict[str, dict] = {}

    for result in results:
        if result.error:
            continue
        name = result.target.name
        first_run = store.is_first_run_for(name)
        new_items = store.diff(result.items)

        # Site yapısı bozulmuş olabilir: hepsi birden "yeni" görünüyorsa şüphelen
        if not first_run and len(new_items) > settings.max_new_per_target:
            log.warning("[%s] %d yeni item?! Muhtemelen selector/URL değişti. "
                        "İlk %d tanesi bildirilecek, kalanı sessizce kaydedilecek.",
                        name, len(new_items), settings.max_new_per_target)
            bootstrap_items += new_items[settings.max_new_per_target:]
            new_items = new_items[: settings.max_new_per_target]

        if first_run:
            log.info("[%s] İLK ÇALIŞMA (bootstrap): %d item sessizce kaydediliyor.",
                     name, len(result.items))
            bootstrap_items += result.items
        else:
            new_to_notify += new_items

        # Görülen her şeyin last_seen'i tazelensin
        store.mark_seen(result.items, notified=False)
        stats[name] = {"scraped": len(result.items), "new": len(new_items),
                       "bootstrap": first_run}

    log.info("Toplam yeni (bildirilecek): %d | bootstrap: %d | hata: %d",
             len(new_to_notify), len(bootstrap_items), len(errors))

    summaries = []
    if new_to_notify:
        capped = new_to_notify[: settings.max_detail_fetch]
        log.info("Detay sayfaları çekiliyor (%d)...", len(capped))
        asyncio.run(enrich_details(capped))

        if args.no_llm:
            from .llm import CampaignSummary
            summaries = [
                CampaignSummary(kampanya_adi=i.title, ozet=i.detail_text[:200],
                                source=i.source, url=i.url)
                for i in capped
            ]
        else:
            log.info("LLM özetleri üretiliyor...")
            summaries = summarize_all(capped)

    if args.dry_run:
        log.info("--- DRY RUN --- state güncellenmedi, e-posta atılmadı")
        for s in summaries:
            print(f"  [{s.source}] {s.kampanya_adi} -> {s.url}")
        for name, st in stats.items():
            print(f"  {name}: {st}")
        for e in errors:
            print(f"  HATA: {e}")
        return 0

    sent = send_campaigns(summaries, errors) if summaries else False
    if errors and not sent:
        send_error_report(errors)

    # State'i güncelle: bildirilenler notified=True
    store.mark_seen(new_to_notify, notified=sent)
    store.mark_seen(bootstrap_items, notified=False)
    for s in summaries:
        from .state import item_key
        rec = store.data["seen"].get(item_key(s.url))
        if rec:
            rec["notified"] = sent
    store.prune()
    store.record_run({"stats": stats, "new": len(new_to_notify),
                      "sent": sent, "errors": errors})
    store.save()

    # Tüm hedefler patladıysa iş akışını kırmızı yap ki fark edelim
    if errors and len(errors) == len(results):
        log.error("Tüm hedefler başarısız.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
