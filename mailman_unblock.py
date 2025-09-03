# pip install requests beautifulsoup4
import os
import sys
import re
import argparse
import requests
from bs4 import BeautifulSoup

BASE   = os.environ.get("MAILMAN_BASE_URL", "https://lists.tmfpond.com")   # e.g. https://lists.example.com
LIST   = os.environ.get("MAILMAN_LIST_NAME", "skilodge")  # e.g. mylist
ADMINPW= os.environ.get("MAILMAN_ADMIN_PW", "N3o6JjKKhblcAjxpg23d")   # list admin password

sess = requests.Session()
sess.headers.update({"User-Agent": "mailman-unblock/1.1"})

def require_env():
    missing = [k for k,v in [("MAILMAN_BASE_URL", BASE), ("MAILMAN_LIST_NAME", LIST), ("MAILMAN_ADMIN_PW", ADMINPW)] if not v]
    if missing:
        print(f"Missing env var(s): {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)

def letters_from_args(args):
    if args.letters:
        # e.g. "--letters 1,abc,xyz" or "--letters b"
        out = []
        for chunk in args.letters.split(","):
            out.extend(list(chunk.strip()))
        return [c.lower() for c in out if c]
    if args.letter:
        return [args.letter.lower()]
    # default: all pages Mailman uses
    return ["1"] + [chr(c) for c in range(ord("a"), ord("z")+1)]

def login():
    url = f"{BASE.rstrip('/')}/mailman/admin/{LIST}/members"
    r = sess.get(url, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form")
    pw_field = soup.find("input", {"name":"adminpw"})
    if form and pw_field:
        data = {}
        for inp in form.find_all("input"):
            n = inp.get("name")
            if not n: 
                continue
            data[n] = inp.get("value", "")
        data["adminpw"] = ADMINPW
        post_url = form.get("action") or url
        r = sess.post(post_url, data=data, timeout=20)
        r.raise_for_status()

def parse_members_form(html):
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form")
    return soup, form

def collect_payload_preserving_browser_behavior(form):
    """
    Build a payload that mirrors what browsers submit:
    - include values for all non-submit inputs
    - include only CHECKED checkboxes
    """
    payload = {}
    for inp in form.find_all("input"):
        name = inp.get("name")
        if not name:
            continue
        typ  = (inp.get("type") or "").lower()
        val  = inp.get("value", "")
        if typ in ("submit", "image"):
            continue
        if typ == "checkbox":
            if inp.has_attr("checked"):
                payload.setdefault(name, val if val != "" else "on")
        else:
            payload.setdefault(name, val)
    return payload

def find_blocked_rows(form):
    """
    Return (blocked_checkbox_names, addresses)
    A row is 'blocked' if it has a checked checkbox with name *_nomail.
    We also try to fetch the member's address from a sibling hidden input named 'user' in the same row.
    """
    blocked_boxes = form.select("input[type='checkbox'][name$='_nomail'][checked]")
    names = []
    addrs = []
    for box in blocked_boxes:
        names.append(box.get("name"))
        # Try to find address in same table row
        tr = box.find_parent("tr")
        user_inp = None
        if tr:
            user_inp = tr.find("input", attrs={"name": "user"})
        if user_inp and user_inp.get("value"):
            addrs.append(user_inp.get("value"))
        else:
            # fallback: look nearby
            user_inp = box.find_next("input", attrs={"name":"user"})
            addrs.append(user_inp.get("value") if user_inp and user_inp.get("value") else "(unknown)")
    return names, addrs

def process_letter(letter, dry_run=False, verbose=False) -> int:
    url = f"{BASE.rstrip('/')}/mailman/admin/{LIST}/members?letter={letter}"
    r = sess.get(url, timeout=25)
    r.raise_for_status()
    soup, form = parse_members_form(r.text)
    if not form:
        if verbose:
            print(f"[{letter}] No members form found (skipping).")
        return 0

    blocked_names, blocked_addrs = find_blocked_rows(form)
    count = len(blocked_names)
    if count == 0:
        if verbose:
            print(f"[{letter}] No blocked members.")
        return 0

    if dry_run:
        print(f"[{letter}] Would unblock {count} member(s):")
        for addr in blocked_addrs:
            print(f"  - {addr}")
        return count

    # Build payload preserving all existing values, then *remove* *_nomail fields
    payload = collect_payload_preserving_browser_behavior(form)
    for name in blocked_names:
        payload.pop(name, None)

    # Emulate the real submit
    payload["setmemberopts_btn"] = "Submit Your Changes"

    post_url = form.get("action") or url
    pr = sess.post(post_url, data=payload, timeout=25)
    pr.raise_for_status()

    print(f"[{letter}] Unblocked {count} member(s).")
    return count

def main():
    parser = argparse.ArgumentParser(description="Unblock Mailman 2.1 members (clear *_nomail).")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be changed but do not submit.")
    parser.add_argument("--letter", help="Process a single letter page (e.g., 'b').")
    parser.add_argument("--letters", help="Process multiple letters (e.g., '1,abc' or 'abc').")
    parser.add_argument("-v", "--verbose", action="store_true", help="More logging.")
    args = parser.parse_args()

    require_env()
    login()

    total = 0
    for L in letters_from_args(args):
        try:
            total += process_letter(L, dry_run=args.dry_run, verbose=args.verbose)
        except requests.HTTPError as e:
            print(f"[{L}] HTTP error: {e}", file=sys.stderr)
        except Exception as e:
            print(f"[{L}] Unexpected error: {e}", file=sys.stderr)

    if args.dry_run:
        print(f"Dry run complete. Would unblock {total} member(s) total.")
    else:
        print(f"Done. Unblocked {total} member(s) total.")

if __name__ == "__main__":
    main()