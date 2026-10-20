"""Offline demo data: three small RSS feeds with fictional stories (no network, no API keys needed)."""
from __future__ import annotations

from datetime import timedelta
from email.utils import format_datetime
from pathlib import Path
from xml.sax.saxutils import escape

from .store import utcnow


def _feed(title: str, items: list[tuple[str, str, str, int]]) -> str:
    now = utcnow()
    body = ""
    for headline, link, description, hours_ago in items:
        body += (f"<item><title>{escape(headline)}</title><link>{escape(link)}</link>"
                 f"<pubDate>{format_datetime(now - timedelta(hours=hours_ago))}</pubDate>"
                 f"<description>{escape(description)}</description></item>")
    return f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>{escape(title)}</title>{body}</channel></rss>'


UPI_A = ("Nimbus Pay processed 20 billion UPI transactions in a single month. The company said the figure covers "
         "August 2026 and is a 31% rise over last year. Small shops accounted for 62% of the new volume. "
         "Most person to merchant payments still carry zero fees for the shopkeeper.")
UPI_B = ("Nimbus Pay processed 20 billion UPI transactions in August 2026, a 31% rise over last year, according to "
         "figures shared by the company on Monday. Small shops accounted for 62% of the new volume. "
         "Banks have asked regulators to allow a fee on very large merchants.")
UPI_C = ("Nimbus Pay processed 20 billion UPI transactions in a single month, the company's own statistics show. "
         "The August 2026 total is 31% higher than a year earlier. Small shops accounted for 62% of the new volume, "
         "and the company said payment fees remain at zero for most users.")
AI_A = ("Orbit Labs released a new language model that it says runs 3 times faster than its previous version. "
        "The model has 70 billion parameters and will be free for developers until December 2026. "
        "Orbit Labs said the model was trained on 15 trillion tokens of public text.")
AI_B = ("Orbit Labs released a language model that runs 3 times faster than the previous version, the company said. "
        "The model has 70 billion parameters and stays free for developers until December 2026. "
        "Analysts expect cheaper access to push more small startups to build on it.")
RUMOR = ("Sources say a chipmaker may unveil a new processor next quarter, according to a leak posted by an anonymous "
         "account. The rumoured chip has not been confirmed by the company, and the details come only from that unnamed "
         "leak. Analysts are speculating about 2 possible designs and a launch price near 499 dollars, but none of the "
         "figures could be independently checked by this outlet.")


def write_demo_feeds(directory: Path) -> list[dict]:
    directory.mkdir(parents=True, exist_ok=True)
    feeds = {
        "mint.xml": ("Demo Mint", [
            ("Nimbus Pay crosses 20 billion UPI transactions in a single month", "https://www.livemint.com/demo/upi-20-billion", UPI_A, 3),
            ("Orbit Labs releases language model that runs 3 times faster", "https://www.livemint.com/demo/orbit-model", AI_A, 4)]),
        "hindu.xml": ("Demo Hindu", [
            ("Nimbus Pay crosses 20 billion UPI transactions in August", "https://www.thehindu.com/demo/upi-20-billion", UPI_B, 2),
            ("Orbit Labs launches faster language model with 70 billion parameters", "https://www.thehindu.com/demo/orbit", AI_B, 5)]),
        "official.xml": ("Demo NPCI", [
            ("Nimbus Pay crosses 20 billion UPI transactions in a single month", "https://www.npci.org.in/demo/upi-stats", UPI_C, 1)]),
        "gossip.xml": ("Demo Gossip", [
            ("Rumoured new chipmaker processor leaks ahead of quarter", "https://www.example-gossip.com/demo/chip", RUMOR, 2)]),
    }
    entries = []
    for name, (title, items) in feeds.items():
        path = directory / name
        path.write_text(_feed(title, items), encoding="utf-8")
        tier = 1 if "official" in name else 2
        entries.append({"name": title, "url": path.as_uri(), "tier": tier, "pillar_hint": "tech"})
    return entries
