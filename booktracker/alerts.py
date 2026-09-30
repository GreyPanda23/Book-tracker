"""E-mail alerts when a To Read book drops below the target price you set.

Uses Gmail with an "App Password" (not your normal password). Put these in `.env`:
    EMAIL_ADDRESS=you@gmail.com
    EMAIL_APP_PASSWORD=abcd efgh ijkl mnop
    EMAIL_TO=you@gmail.com          # optional, defaults to EMAIL_ADDRESS
"""

from __future__ import annotations

import logging
import smtplib
import sqlite3
from email.message import EmailMessage

from . import config

log = logging.getLogger("booktracker.alerts")


def email_configured() -> bool:
    return bool(config.get_secret("EMAIL_ADDRESS") and config.get_secret("EMAIL_APP_PASSWORD"))


def _cheapest(conn: sqlite3.Connection, book_id: int, run_id: int | None) -> dict | None:
    """Cheapest in-stock price for a book within one run."""
    if run_id is None:
        return None
    row = conn.execute(
        """SELECT * FROM prices WHERE book_id = ? AND run_id = ?
              AND price_aed IS NOT NULL AND (in_stock IS NULL OR in_stock = 1)
            ORDER BY price_aed LIMIT 1""", (book_id, run_id)).fetchone()
    return dict(row) if row else None


def _previous_run(conn: sqlite3.Connection, book_id: int, run_id: int) -> int | None:
    row = conn.execute("SELECT MAX(run_id) FROM prices WHERE book_id = ? AND run_id < ?",
                       (book_id, run_id)).fetchone()
    return row[0] if row else None


def find_price_drops(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """To Read books whose cheapest price in this run is newly below target.

    "Newly" = last week's cheapest wasn't already below target at this price,
    so you get one e-mail per drop instead of the same one every Sunday.
    """
    drops = []
    books = conn.execute("SELECT * FROM books WHERE status = 'To Read' AND target_price > 0").fetchall()
    for book in books:
        now = _cheapest(conn, book["id"], run_id)
        if not now or now["price_aed"] >= book["target_price"]:
            continue
        before = _cheapest(conn, book["id"], _previous_run(conn, book["id"], run_id))
        if before and before["price_aed"] < book["target_price"] and now["price_aed"] >= before["price_aed"]:
            continue  # already alerted at this (or a lower) price
        drops.append({"title": book["title"], "author": book["author"], "target": book["target_price"],
                      "price": now["price_aed"], "store": now["store_name"], "type": now["type"],
                      "url": now["product_url"], "branch": now["branch_location"]})
    return drops


def build_email(drops: list[dict], sender: str, to: str) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = (f"📚 Price drop: {drops[0]['title']}" if len(drops) == 1
                      else f"📚 {len(drops)} books dropped below your target price")
    msg["From"], msg["To"] = sender, to
    lines = ["Good news! These books on your To Read list are now below your target price:", ""]
    for d in drops:
        where = f"{d['store']} ({'in store: ' + d['branch'] if d['type'] == 'in-store' else 'online'})"
        lines += [f"• {d['title']}" + (f" by {d['author']}" if d["author"] else ""),
                  f"  AED {d['price']:.2f} at {where} — your target: AED {d['target']:.2f}",
                  f"  {d['url']}", ""]
    lines.append("— Your Book Tracker")
    msg.set_content("\n".join(lines))
    return msg


def send_price_alerts(drops: list[dict], smtp_factory=smtplib.SMTP_SSL) -> bool:
    """E-mail the drops. Returns True if an e-mail was sent."""
    if not drops:
        return False
    if not email_configured():
        log.info("%d price drop(s) found, but e-mail isn't set up in .env", len(drops))
        return False
    sender = config.get_secret("EMAIL_ADDRESS")
    to = config.get_secret("EMAIL_TO") or sender
    msg = build_email(drops, sender, to)
    try:
        with smtp_factory("smtp.gmail.com", 465, timeout=30) as smtp:
            smtp.login(sender, config.get_secret("EMAIL_APP_PASSWORD").replace(" ", ""))
            smtp.send_message(msg)
    except Exception as exc:  # never let e-mail problems break the price update
        log.error("could not send price alert e-mail: %s", exc)
        return False
    log.info("sent price alert e-mail for %d book(s)", len(drops))
    return True
