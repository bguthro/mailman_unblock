#!/usr/bin/env python3

# pip install requests beautifulsoup4
import os
import sys
import argparse
import logging
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE    = os.environ.get("MAILMAN_BASE_URL")
LIST    = os.environ.get("MAILMAN_LIST_NAME")
ADMINPW = os.environ.get("MAILMAN_ADMIN_PW")

sess = requests.Session()
sess.headers.update({"User-Agent": "mailman-unblock/1.3"})

logger = logging.getLogger("mailman_unblock")

def require_env():
    missing = [k for k, v in [
        ("MAILMAN_BASE_URL", BASE),
        ("MAILMAN_LIST_NAME", LIST),
        ("MAILMAN_ADMIN_PW", ADMINPW)
    ] if not v]
    if missing:
        logger.error("Missing env var(s): %s", ", ".join(missing))
        sys.exit(2)

def letters_from_args(args):
    if args.letters:
        out = []
        for chunk in args.letters.split(","):
            out.extend(list(chunk.strip()))
        return [c.lower() for c in out if c]
    if args.letter:
        return [args.letter.lower()]
    return ["1"] + [chr(c) for c in range(ord("a"), ord("z")+1)]

def absolutize_action(form_action: str | None, current_url: str) -> str:
    """Return an absolute URL for form submission."""
    if not form_action:
        return current_url
    return urljoin(current_url, form_action)

def login():
    members_url = f"{BASE.rstrip('/')}/mailman/admin/{LIST}/members"
    logger.info("Logging into %s", members_url)
    r = sess.get(members_url, timeout=20)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form")
    pw_field = soup.find("input", {"name": "adminpw"})

    if form and pw_field:
        # Collect baseline payload (hidden tokens, etc.)
        data = {}
        for inp in form.find_all("input"):
            n = inp.get("name")
            if not n:
                continue
            typ = (inp.get("type") or "").lower()
            val = inp.get("value", "")
            if typ in ("submit", "image"):
                continue
            if typ == "checkbox":
                if inp.has_attr("checked"):
                    data.setdefault(n, val if val != "" else "on")
            else:
                data.setdefault(n, val)

        # Overwrite password with the real value
        data["adminpw"] = ADMINPW

        post_url = absolutize_action(form.get("action"), r.url)
        method = (form.get("method") or "post").lower()

        if method == "post":
            r2 = sess.post(post_url, data=data, timeout=20)
        else:
            r2 = sess.get(post_url, params=data, timeout=20)

        r2.raise_for_status()
        logger.debug("Submitted login form for list %s", LIST)
    else:
        logger.debug("No admin password form found; assuming already authenticated.")

def parse_members_form(html):
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    return soup, form

def collect_payload_preserving_browser_behavior(form):
    """
    Build a payload that mirrors browser behavior across:
    - input (text/hidden/checkbox/radio/etc)
    - select (single and multi)
    - textarea
    Only checked checkboxes/radios are included.
    """
    payload = {}

    # Inputs
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        typ = (inp.get("type") or "").lower()
        val = inp.get("value", "")

        if typ in ("submit", "image", "button", "file"):
            continue
        if typ in ("checkbox", "radio"):
            if inp.has_attr("checked"):
                payload.setdefault(name, val if val != "" else "on")
            continue
        # text/password/hidden/number/etc
        payload.setdefault(name, val)

    # Textareas
    for ta in form.find_all("textarea"):
        name = ta.get("name")
        if not name:
            continue
        payload.setdefault(name, ta.text or "")

    # Selects
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        multiple = sel.has_attr("multiple")
        chosen = []
        for opt in sel.find_all("option"):
            if opt.has_attr("selected"):
                chosen.append(opt.get("value", opt.text or ""))
        if multiple:
            # Multiple selects submit multiple key=value pairs (requests accepts list)
            if chosen:
                payload.setdefault(name, chosen)
        else:
            # Single select: pick the selected value, or the first option if none selected
            if chosen:
                payload.setdefault(name, chosen[0])
            else:
                first = sel.find("option")
                if first:
                    payload.setdefault(name, first.get("value", first.text or ""))

    return payload

def find_blocked_rows(form):
    """
    Return (blocked_checkbox_names, addresses).
    A row is 'blocked' if it has a checked checkbox with name *_nomail.
    We try to grab the address from a sibling hidden 'user' field in the same <tr>.
    """
    blocked_boxes = form.select("input[type='checkbox'][name$='_nomail'][checked]")
    names, addrs = [], []
    for box in blocked_boxes:
        names.append(box.get("name"))
        # try to find a sibling 'user' input in same row
        tr = box.find_parent("tr")
        user_inp = tr.find("input", attrs={"name": "user"}) if tr else None
        if user_inp and user_inp.get("value"):
            addrs.append(user_inp.get("value"))
        else:
            # fallback: nearest next 'user' input
            user_inp = box.find_next("input", attrs={"name": "user"})
            addrs.append(user_inp.get("value") if user_inp and user_inp.get("value") else "(unknown)")
    return names, addrs

def process_letter(letter, dry_run=False) -> int:
    url = f"{BASE.rstrip('/')}/mailman/admin/{LIST}/members?letter={letter}"
    r = sess.get(url, timeout=25)
    r.raise_for_status()
    soup, form = parse_members_form(r.text)
    if not form:
        logger.debug("[%s] No members form found.", letter)
        return 0

    blocked_names, blocked_addrs = find_blocked_rows(form)
    count = len(blocked_names)
    if count == 0:
        logger.debug("[%s] No blocked members.", letter)
        return 0

    if dry_run:
        logger.info("[%s] Would unblock %d member(s): %s", letter, count, ", ".join(blocked_addrs))
        return count

    payload = collect_payload_preserving_browser_behavior(form)
    # Remove each *_nomail that was checked to "uncheck" it
    for name in blocked_names:
        payload.pop(name, None)
    # Real submit button name/value
    payload["setmemberopts_btn"] = "Submit Your Changes"

    post_url = absolutize_action(form.get("action"), r.url)
    method = (form.get("method") or "post").lower()
    if method == "post":
        pr = sess.post(post_url, data=payload, timeout=25)
    else:
        pr = sess.get(post_url, params=payload, timeout=25)
    pr.raise_for_status()

    logger.info("[%s] Unblocked %d member(s).", letter, count)
    return count

def main():
    parser = argparse.ArgumentParser(description="Unblock Mailman 2.1 members (clear *_nomail).")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed but do not submit.")
    parser.add_argument("--letter", help="Process a single letter page (e.g., 'b').")
    parser.add_argument("--letters", help="Process multiple letters (e.g., '1,abc' or 'abc').")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    require_env()
    login()

    total = 0
    for L in letters_from_args(args):
        try:
            total += process_letter(L, dry_run=args.dry_run)
        except requests.HTTPError as e:
            logger.error("[%s] HTTP error: %s", L, e)
        except Exception:
            logger.exception("[%s] Unexpected error", L)

    if args.dry_run:
        logger.info("Dry run complete. Would unblock %d member(s) total.", total)
    else:
        logger.info("Done. Unblocked %d member(s) total.", total)

if __name__ == "__main__":
    main()