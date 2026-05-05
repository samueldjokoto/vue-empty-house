"""
Vue Empty Screening Finder — Cloud edition
==========================================
Runs in GitHub Actions every 2 hours. Output: vue_results.json in repo root.
Dashboard fetches that file via GitHub Pages.
"""

import asyncio
import json
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional

from playwright.async_api import async_playwright, Page

CINEMAS = {
    "Croydon Purley Way": "https://www.myvue.com/cinema/croydon-purley-way/whats-on",
    "Croydon Grants": "https://www.myvue.com/cinema/croydon-grants/whats-on",
}

DELAY_BETWEEN_REQUESTS_SEC = 1.2
MAX_SESSIONS_PER_CINEMA = 250
HEADLESS = True
OUTPUT_FILE = "vue_results.json"


@dataclass
class Session:
    cinema: str
    film_title: str
    date_str: str = ""
    start_time: str = ""
    end_time: str = ""
    screen: str = ""
    booking_url: str = ""
    sold_seats: Optional[int] = None
    total_seats: Optional[int] = None
    available_seats: Optional[int] = None
    error: Optional[str] = None


async def collect_sessions_for_cinema(page: Page, cinema_name: str, url: str) -> list[Session]:
    print(f"\n[{cinema_name}] Loading what's on...")
    sessions: list[Session] = []

    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)
    except Exception as e:
        print(f"  Failed to load: {e}")
        return sessions

    try:
        await page.click("button#onetrust-accept-btn-handler", timeout=4000)
    except Exception:
        pass

    for _ in range(8):
        await page.mouse.wheel(0, 4000)
        await page.wait_for_timeout(500)

    for _ in range(5):
        try:
            clicked = await page.evaluate("""() => {
                const btns = Array.from(document.querySelectorAll('button'));
                const more = btns.find(b => /more|later|show all|load/i.test(b.innerText));
                if (more && !more.disabled) { more.click(); return true; }
                return false;
            }""")
            if not clicked:
                break
            await page.wait_for_timeout(1500)
        except Exception:
            break

    raw = await page.evaluate("""() => {
        const results = [];
        const sessionLinks = Array.from(document.querySelectorAll('a.session'));
        for (const a of sessionLinks) {
            let dateStr = '';
            let groupBlock = a.closest('.sessions__group-block');
            if (groupBlock) {
                const dt = groupBlock.querySelector('time[datetime]');
                if (dt) dateStr = dt.getAttribute('datetime') || '';
            }

            let filmTitle = '';
            let cur = a;
            while (cur && cur !== document.body) {
                const h = cur.querySelector('h2, h3, [class*="film-title"], [class*="title"]');
                if (h && h.innerText && h.innerText.trim().length > 2 && h.innerText.length < 200) {
                    filmTitle = h.innerText.trim();
                    break;
                }
                cur = cur.parentElement;
            }
            if (!filmTitle) {
                const filmCard = a.closest('[class*="film"], [data-test*="film"]');
                if (filmCard) {
                    const img = filmCard.querySelector('img[alt]');
                    if (img) filmTitle = img.alt.trim();
                }
            }

            const startEl = a.querySelector('.session-time__start');
            const endEl   = a.querySelector('.session-time__end');
            const screenEl = a.querySelector('.session-special-attributes__screen-name');
            const startTime = startEl ? (startEl.getAttribute('datetime') || startEl.innerText.trim()) : '';
            const endTime   = endEl   ? (endEl.getAttribute('datetime')   || endEl.innerText.trim())   : '';
            const screen    = screenEl ? screenEl.innerText.trim() : '';

            results.push({
                href: a.href, dateStr,
                filmTitle: filmTitle || 'Unknown film',
                startTime, endTime, screen,
            });
        }
        return results;
    }""")

    print(f"  Found {len(raw)} sessions across all films/days.")

    seen = set()
    for r in raw:
        if not r["href"] or r["href"] in seen:
            continue
        seen.add(r["href"])
        date_only = r["dateStr"].split("T")[0] if r["dateStr"] else ""
        sessions.append(Session(
            cinema=cinema_name,
            film_title=r["filmTitle"],
            date_str=date_only,
            start_time=r["startTime"],
            end_time=r["endTime"],
            screen=r["screen"],
            booking_url=r["href"],
        ))
        if len(sessions) >= MAX_SESSIONS_PER_CINEMA:
            break

    return sessions


async def check_seats_for_session(page: Page, session: Session) -> None:
    try:
        await page.goto(session.booking_url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3500)
    except Exception as e:
        session.error = f"Load failed: {type(e).__name__}"
        return

    is_unavailable = await page.evaluate("""() => {
        return !!document.querySelector('[data-test="booking-unavailable-modal-accept-button"]');
    }""")
    if is_unavailable:
        session.error = "Session unavailable (started or sold out)"
        return

    title = await page.evaluate("""() => {
        const h1 = document.querySelector('h1');
        return h1 ? h1.innerText.trim() : '';
    }""")
    if title and title.lower() not in ("book tickets", "now booking"):
        session.film_title = title

    counts = await page.evaluate("""() => {
        const seats = Array.from(document.querySelectorAll('button.seats__seat[data-seat-status]'));
        let sold = 0, available = 0, blocked = 0, total = 0;
        for (const s of seats) {
            const status = s.getAttribute('data-seat-status') || '';
            if (status === '*-*' || status.startsWith('*-')) continue;
            total++;
            const prefix = status.split('-')[0];
            if (prefix === '1') sold++;
            else if (prefix === '0' || prefix === '7') available++;
            else blocked++;
        }
        return { sold, available, blocked, total };
    }""")

    session.sold_seats = counts["sold"]
    session.available_seats = counts["available"]
    session.total_seats = counts["total"]


async def main():
    started_at = datetime.now()
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1400, "height": 900},
            locale="en-GB",
        )
        page = await context.new_page()

        all_sessions: list[Session] = []
        for cinema_name, url in CINEMAS.items():
            sessions = await collect_sessions_for_cinema(page, cinema_name, url)
            all_sessions.extend(sessions)
            await asyncio.sleep(DELAY_BETWEEN_REQUESTS_SEC)

        print(f"\n=== Total sessions to check: {len(all_sessions)} ===\n")

        for i, s in enumerate(all_sessions, 1):
            label = f"[{i}/{len(all_sessions)}] {s.cinema} | {s.date_str} {s.start_time} | {s.film_title[:50]}"
            print(label)
            await check_seats_for_session(page, s)
            if s.error:
                print(f"      ! {s.error}")
            else:
                pct = (s.sold_seats / s.total_seats * 100) if s.total_seats else 0
                print(f"      sold={s.sold_seats}/{s.total_seats} ({pct:.0f}%)")
            await asyncio.sleep(DELAY_BETWEEN_REQUESTS_SEC)

        await browser.close()

    output = {
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": (datetime.now() - started_at).total_seconds(),
        "sessions": [asdict(s) for s in all_sessions],
    }
    with open(OUTPUT_FILE, "w") as f:
        json.dump(output, f, indent=2)

    valid = [s for s in all_sessions if s.sold_seats is not None]
    empty = [s for s in valid if s.sold_seats == 0 and s.total_seats and s.total_seats > 10]
    low = [s for s in valid if s.sold_seats <= 4 and s.total_seats and s.total_seats > 10]

    print(f"\n=== DONE ===")
    print(f"  Total found: {len(all_sessions)}")
    print(f"  Successfully scanned: {len(valid)}")
    print(f"  Truly empty: {len(empty)}")
    print(f"  Low (<=4): {len(low)}")
    print(f"  Wrote {OUTPUT_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
