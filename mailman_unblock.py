#!/usr/bin/env python3
"""
mailman_unblock.py — Clear *_nomail (blocked) flags in Mailman 2.1 Members pages.

Usage examples:
  python mailman_unblock.py --dry-run --letter b
  python mailman_unblock.py --letters 1,abc
  python mailman_unblock.py --verbose
"""

import os
import sys
import argparse
import logging
from typing import List, Tuple, Optional
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# ---- Configuration via environment ----
BASE: Optional[str]    = os.environ.get("MAILMAN_BASE_URL")   # e.g. https://lists.example.com
LISTNAME: Optional[str]= os.environ.get("MAILMAN_LIST_NAME")  # e.g. skilodge
ADMINPW: Optional[str] = os.environ.get("MAILMAN_ADMIN_PW")   # list admin password

# ---- Globals ----
sess = requests.Session()
sess.headers.update({"User-Agent": "mailman-unblock/1.4"})
logger = logging.getLogger("mailman_unblock")
DUMP_HTML: bool = False
DUMP_DIR: Optional[Path] = None


# ------------------------------- Helpers --------------------------------- #

def require_env() -> None:
    missing = [k for k, v in [
        ("MAILMAN_BASE_URL", BASE),
        ("MAILMAN_LIST_NAME", LISTNAME),
        ("MAILMAN_ADMIN_PW", ADMINPW),
    ] if not v]
    if missing:
        logger.error("Missing env var(s): %s", ", ".join(missing))
        sys.exit(2)


def ensure_dump_dir() -> None:
    global DUMP_DIR
    if DUMP_HTML and DUMP_DIR is not None:
        DUMP_DIR.mkdir(parents=True, exist_ok=True)


def dump_text(filename: str, content: str) -> None:
    if not DUMP_HTML or DUMP_DIR is None:
        return
    ensure_dump_dir()
    (DUMP_DIR / filename).write_text(content, encoding="utf-8", errors="ignore")


def redact_pairs(pairs: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
    """Redact sensitive values when logging/dumping payload pairs."""
    SENSITIVE = {"adminpw", "password", "passwd"}
    out = []
    for k, v in pairs:
        vk = (k or "").lower()
        if vk in SENSITIVE:
            out.append((k, "<REDACTED>"))
        else:
            out.append((k, v))
    return out


def letters_from_args(args: argparse.Namespace) -> List[str]:
    if args.letters:
        out: List[str] = []
        for chunk in args.letters.split(","):
            out.extend(list(chunk.strip()))
        return [c.lower() for c in out if c]
    if args.letter:
        return [args.letter.lower()]
    # Default: all Mailman letter tabs
    return ["1"] + [chr(c) for c in range(ord("a"), ord("z")+1)]


def absolutize_action(form_action: Optional[str], current_url: str) -> str:
    """Return an absolute URL for a form submission action."""
    if not form_action:
        return current_url
    return urljoin(current_url, form_action)


def parse_members_form(html: str):
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    return soup, form


def login() -> None:
    """Open Members page; if an admin password form is present, submit it."""
    members_url = f"{BASE.rstrip('/')}/mailman/admin/{LISTNAME}/members"
    logger.info("Logging into %s", members_url)
    r = sess.get(members_url, timeout=20)
    r.raise_for_status()
    dump_text("login_get_members.html", r.text)

    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form")
    pw_field = soup.find("input", {"name": "adminpw"})

    if not (form and pw_field):
        logger.debug("No admin password form found; assuming already authenticated.")
        return

    # Build a simple payload (form is typically urlencoded here)
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

    data["adminpw"] = ADMINPW
    post_url = absolutize_action(form.get("action"), r.url)
    method = (form.get("method") or "post").lower()

    resp = sess.post(post_url, data=data, timeout=20) if method == "post" else sess.get(post_url, params=data, timeout=20)
    resp.raise_for_status()
    logger.debug("Submitted login form for list %s", LISTNAME)
    dump_text("login_submit_response.html", resp.text)


def collect_payload_pairs(form) -> List[Tuple[str, str]]:
    """
    Build a browser-faithful payload as an *ordered* list of (name, value) pairs.
    - Includes only checked checkboxes/radios
    - Preserves duplicates (e.g., multiple 'user' inputs — crucial for Mailman)
    - Handles <input>, <textarea>, and <select> (single & multi)
    """
    pairs: List[Tuple[str, str]] = []

    # <input>
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
                pairs.append((name, val if val != "" else "on"))
            continue
        # text/hidden/password/number/etc
        pairs.append((name, val))

    # <textarea>
    for ta in form.find_all("textarea"):
        name = ta.get("name")
        if not name:
            continue
        pairs.append((name, ta.text or ""))

    # <select>
    for sel in form.find_all("select"):
        name = sel.get("name")
        if not name:
            continue
        multiple = sel.has_attr("multiple")
        options = sel.find_all("option")
        selected = [o for o in options if o.has_attr("selected")]
        if multiple:
            for opt in selected:
                pairs.append((name, opt.get("value", opt.text or "")))
        else:
            if selected:
                pairs.append((name, selected[0].get("value", selected[0].text or "")))
            elif options:
                # Browser submits first option if none are selected
                pairs.append((name, options[0].get("value", options[0].text or "")))

    return pairs


def _nomail_reason_from_box(box) -> Optional[str]:
    """Infer the reason letter adjacent to the *_nomail checkbox: 'B', 'A', 'U' or None."""
    # Look at immediate next siblings for a short token like [B], [A], [U]
    try:
        sibs = list(box.next_siblings)
    except Exception:
        sibs = []
    for s in sibs[:3]:
        if hasattr(s, 'strip'):
            txt = str(s).strip()
        else:
            txt = s.get_text(strip=True) if hasattr(s, 'get_text') else str(s)
        if txt.startswith('[') and ']' in txt:
            ch = txt[1].upper()
            if ch in ('A', 'B', 'U'):
                return ch
    # Fallback: scan the containing cell's text
    td = box.find_parent('td')
    if td:
        txt = td.get_text(" ", strip=True)
        for ch in ('A', 'B', 'U'):
            token = f"[{ch}]"
            if token in txt:
                return ch
    return None


def find_blocked_rows_with_reasons(form):
    """
    Return a list of tuples: (checkbox_name, address, reason_letter)
    Only includes rows with a checked '*_nomail' checkbox.
    """
    blocked_boxes = form.select("input[type='checkbox'][name$='_nomail'][checked]")
    rows: List[Tuple[str, str, Optional[str]]] = []
    for box in blocked_boxes:
        name = box.get("name")
        tr = box.find_parent("tr")
        user_inp = tr.find("input", attrs={"name": "user"}) if tr else None
        addr = user_inp.get("value") if user_inp and user_inp.get("value") else None
        if not addr:
            # nearest 'user' input as fallback
            user_inp = box.find_next("input", attrs={"name": "user"})
            addr = user_inp.get("value") if user_inp and user_inp.get("value") else "(unknown)"
        reason = _nomail_reason_from_box(box)
        rows.append((name, addr, reason))
    return rows


def pick_submit_control(form) -> Tuple[str, str]:
    """Pick a generic submit control's (name, value). Defaults to first submit."""
    for inp in form.find_all("input"):
        if (inp.get("type") or "").lower() == "submit" and inp.get("name"):
            return inp.get("name"), (inp.get("value") or "Submit")
    return "submit", "Submit"


def pick_members_submit(form) -> Tuple[str, str]:
    """
    Pick the correct submit button on Members page.
    Prefer buttons like 'Submit Your Changes'/'Change'/'Apply' and avoid 'Search'.
    Also prefer names containing 'setmember'.
    """
    candidates: list[Tuple[str, str, int]] = []  # (name, value, score)
    for inp in form.find_all("input"):
        if (inp.get("type") or "").lower() != "submit":
            continue
        name = inp.get("name")
        if not name:
            continue
        val = inp.get("value") or ""
        text = f"{name} {val}".lower()
        score = 0
        if any(w in text for w in ["submit your changes", "submit", "change", "apply", "update", "setmember"]):
            score += 5
        if "search" in text or "findmember" in text:
            score -= 10
        # Slight preference for explicit Mailman naming
        if "setmember" in text:
            score += 3
        candidates.append((name, val or "Submit", score))

    if candidates:
        # pick highest score; if tie, first occurrence
        candidates.sort(key=lambda x: x[2], reverse=True)
        return candidates[0][0], candidates[0][1]

    # Fallback to generic picker
    return pick_submit_control(form)


# ---------------------------- Main Workhorse ------------------------------ #

def pick_bounce_submit(form) -> Tuple[str, str]:
    """Find a submit control on the Bounce page that clears selected users."""
    candidates = []
    for inp in form.find_all("input"):
        if (inp.get("type") or "").lower() == "submit":
            name = inp.get("name")
            val  = inp.get("value") or ""
            if name:
                candidates.append((name, val))
                # Prefer buttons that look like 'Clear', 'clear', 'Reset bounce info', etc.
                low = (name + " " + val).lower()
                if any(w in low for w in ["clear", "reset", "remove", "process"]):
                    return name, val or "Submit"
    # Fallback: first submit
    return candidates[0] if candidates else ("submit", "Submit")


def clear_bounces_for_users(target_addrs: list[str]) -> bool:
    """
    Visit /bounce admin page, select only target_addrs by ticking their 'user' inputs,
    and submit a 'clear' action. Returns True if POST 200 and form reloads.
    """
    bounce_url = f"{BASE.rstrip('/')}/mailman/admin/{LISTNAME}/bounce"
    r = sess.get(bounce_url, timeout=25)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    dump_text("bounce_before.html", r.text)
    form = soup.find("form")
    if not form:
        logger.debug("Bounce page has no form; skipping bounce clear.")
        return False

    # Build ordered pairs preserving CSRF and any other hidden fields
    pairs: list[tuple[str, str]] = []
    # include all non-submit inputs (hidden, text, etc.)
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        typ = (inp.get("type") or "").lower()
        val = inp.get("value", "")
        if typ in ("submit", "image", "button", "file"):
            continue
        if name == "user":
            # only include user fields that match our targets
            if val and val in target_addrs:
                pairs.append((name, val))
        else:
            # CSRF etc.
            pairs.append((name, val))

    # If no matching users found on the bounce page, nothing to do.
    if not any(k == "user" for (k, _) in pairs):
        logger.debug("Bounce page had no matching users for: %s", ", ".join(target_addrs))
        return False

    # Add a 'clear' submit button (best-effort match)
    submit_name, submit_val = pick_bounce_submit(form)
    pairs.append((submit_name, submit_val))

    # Multipart submit (most bounce pages are multipart too)
    files = [(k, (None, v)) for (k, v) in pairs]
    headers = {
        "Origin": BASE.rstrip("/"),
        "Referer": bounce_url,
    }
    post_url = absolutize_action(form.get("action"), r.url)
    logger.debug("Bounce clear submit '%s'='%s'", submit_name, submit_val)
    logger.debug("Bounce action URL: %s", post_url)
    logger.debug("Bounce clear POST keys (sample): %s", [k for (k, _) in pairs][:30])
    pr = sess.post(post_url, files=files, headers=headers, timeout=25)
    pr.raise_for_status()
    dump_text("bounce_post_response.html", pr.text)

    # quick reload to ensure page is reachable after action
    vr = sess.get(bounce_url, timeout=20)
    vr.raise_for_status()
    dump_text("bounce_after.html", vr.text)
    return True

def pairs_to_debug_names(pairs):
    names = [k for (k, _) in pairs]
    # show nomail keys first; cap length to keep logs readable
    nomails = [n for n in names if n.endswith("_nomail")]
    if nomails:
        return (nomails + [n for n in names if not n.endswith("_nomail")])[:40]
    return names[:40]

def process_letter(letter: str, dry_run: bool = False) -> int:
    page_url = f"{BASE.rstrip('/')}/mailman/admin/{LISTNAME}/members?letter={letter}"
    r = sess.get(page_url, timeout=25)
    r.raise_for_status()
    dump_text(f"members_{letter}_before.html", r.text)
    soup, form = parse_members_form(r.text)
    if not form:
        logger.debug("[%s] No members form found.", letter)
        return 0

    blocked_rows = find_blocked_rows_with_reasons(form)
    if logger.isEnabledFor(logging.DEBUG):
        from collections import Counter
        reason_counts = Counter(r or '?' for (_, _, r) in blocked_rows)
        logger.debug("[%s] Blocked members by reason: %s", letter, dict(reason_counts))
    # Only target addresses disabled by bounces [B]
    target = [(n, a) for (n, a, r) in blocked_rows if r == 'B']
    blocked_names = [n for (n, _) in target]
    blocked_addrs = [a for (_, a) in target]
    before = len(blocked_names)
    if before == 0:
        logger.debug("[%s] No bounce-disabled [B] members.", letter)
        return 0

    if dry_run:
        logger.info("[%s] Would unblock %d member(s): %s", letter, before, ", ".join(blocked_addrs))
        # Build and dump the would-be payload without submitting
        pairs_preview = collect_payload_pairs(form)
        nomail_set_preview = set(blocked_names)
        pairs_preview = [(k, v) for (k, v) in pairs_preview if k not in nomail_set_preview]
        # Do not include *_nomail for targeted users (unchecked fields are omitted by browsers)
        sub_name_preview, sub_val_preview = pick_members_submit(form)
        pairs_preview.append((sub_name_preview, sub_val_preview))
        post_url_preview = absolutize_action(form.get("action"), r.url)
        logger.debug("[%s] (dry-run) Using submit '%s'='%s'", letter, sub_name_preview, sub_val_preview)
        logger.debug("[%s] (dry-run) Action URL: %s", letter, post_url_preview)
        dump_text(f"members_{letter}_payload.txt", "\n".join([f"{k}={v}" for (k, v) in redact_pairs(pairs_preview)]))
        dump_text(f"members_{letter}_action.txt", post_url_preview)
        return before

    # Build browser-equivalent payload (ordered pairs, duplicates preserved)
    pairs = collect_payload_pairs(form)
    # Uncheck by removing checked *_nomail entries for targeted [B] users
    nomail_set = set(blocked_names)
    pairs = [(k, v) for (k, v) in pairs if k not in nomail_set]

    # Real submit button
    submit_name, submit_value = pick_members_submit(form)
    pairs.append((submit_name, submit_value))

    # Multipart POST to members form
    # Use application/x-www-form-urlencoded to match browser behavior
    post_url = absolutize_action(form.get("action"), r.url)
    headers = {"Origin": BASE.rstrip("/"), "Referer": page_url}

    logger.debug("[%s] Using submit '%s'='%s'", letter, submit_name, submit_value)
    logger.debug("[%s] Action URL: %s", letter, post_url)
    logger.debug("[%s] POST keys (sample): %s", letter, [k for (k, _) in pairs][:40])
    dump_text(f"members_{letter}_payload.txt", "\n".join([f"{k}={v}" for (k, v) in redact_pairs(pairs)]))
    pr = sess.post(post_url, data=pairs, headers=headers, timeout=25)
    pr.raise_for_status()
    dump_text(f"members_{letter}_post_response.html", pr.text)

    # Verify
    vr = sess.get(page_url, timeout=20)
    vr.raise_for_status()
    dump_text(f"members_{letter}_after.html", vr.text)
    _, vform = parse_members_form(vr.text)
    if vform:
        remaining_rows = find_blocked_rows_with_reasons(vform)
        remaining = sum(1 for (_, _, r) in remaining_rows if r == 'B')
    else:
        remaining = before
    if remaining < before:
        unblocked = before - remaining
        logger.info("[%s] Unblocked %d → %d remaining.", letter, before, remaining)
        return unblocked

    # Fallback: clear bounces for the affected users, then retry members submit once
    logger.info("[%s] No change after submit; attempting Bounce clear for %d user(s).", letter, before)
    did_bounce_clear = clear_bounces_for_users(blocked_addrs)
    if did_bounce_clear:
        # Reload current letter (CSRF, latest state)
        r2 = sess.get(page_url, timeout=25); r2.raise_for_status()
        dump_text(f"members_{letter}_before_retry.html", r2.text)
        _, form2 = parse_members_form(r2.text)
        if form2:
            # rebuild pairs and resubmit without *_nomail
            pairs2 = collect_payload_pairs(form2)
            blocked_rows2 = find_blocked_rows_with_reasons(form2)
            target2 = [(n, a) for (n, a, r) in blocked_rows2 if r == 'B']
            nomail_set2 = set(n for (n, _) in target2)
            pairs2 = [(k, v) for (k, v) in pairs2 if k not in nomail_set2]
            name, value = pick_members_submit(form2)
            pairs2.append((name, value))

            post_url2 = absolutize_action(form2.get("action"), r2.url)
            logger.debug("[%s] Retry submit '%s'='%s' → %s", letter, name, value, post_url2)
            dump_text(f"members_{letter}_payload_retry.txt", "\n".join([f"{k}={v}" for (k, v) in redact_pairs(pairs2)]))
            pr2 = sess.post(post_url2, data=pairs2, headers=headers, timeout=25)
            pr2.raise_for_status()
            dump_text(f"members_{letter}_post_response_retry.html", pr2.text)

            vr2 = sess.get(page_url, timeout=20); vr2.raise_for_status()
            dump_text(f"members_{letter}_after_retry.html", vr2.text)
            _, vform2 = parse_members_form(vr2.text)
            if vform2:
                remaining_rows2 = find_blocked_rows_with_reasons(vform2)
                remaining2 = sum(1 for (_, _, r) in remaining_rows2 if r == 'B')
            else:
                remaining2 = before
            if remaining2 < before:
                unblocked = before - remaining2
                logger.info("[%s] Unblocked %d → %d remaining after Bounce clear.", letter, before, remaining2)
                return unblocked

    logger.warning("[%s] Still blocked after members submit%s.",
                   letter, " + bounce clear" if did_bounce_clear else "")
    return 0


# --------------------------------- CLI ----------------------------------- #

def main() -> None:
    parser = argparse.ArgumentParser(description="Unblock Mailman 2.1 members (clear *_nomail).")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed but do not submit.")
    parser.add_argument("--letter", help="Process a single letter page (e.g., 'b').")
    parser.add_argument("--letters", help="Process multiple letters (e.g., '1,abc' or 'abc').")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug logging.")
    parser.add_argument("--dump-html", action="store_true", help="Dump fetched pages and POST responses to --dump-dir.")
    parser.add_argument("--dump-dir", default="debug-dump", help="Directory for --dump-html artifacts (default: debug-dump)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    global DUMP_HTML, DUMP_DIR
    DUMP_HTML = bool(args.dump_html)
    DUMP_DIR = Path(args.dump_dir) if DUMP_HTML else None

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
