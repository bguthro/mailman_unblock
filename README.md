# Mailman 2.1 Unblock Script

This Python tool automates **unblocking** members on a Mailman 2.1.39 list by clearing the `*_nomail` checkboxes (those marked `[B]` in the Membership Management UI).

It logs in with the list admin password, crawls the members pages (`1`–`z`), and either:
- **Dry-runs** to show which addresses would be unblocked
- Actually submits the forms to re-enable delivery

---

## Requirements

- Python 3.9+
- [requests](https://pypi.org/project/requests/)
- [beautifulsoup4](https://pypi.org/project/beautifulsoup4/)

Install dependencies:

```bash
pip install -r requirements.txt
```
or
```
make configure
```


---

## Environment Variables

Set these to point at your Mailman list:

- `MAILMAN_BASE_URL` — Base URL of the Mailman server (e.g. `https://lists.example.com`)
- `MAILMAN_LIST_NAME` — The list short name (e.g. `skilodge`)
- `MAILMAN_ADMIN_PW` — The list’s admin password

Example:

```bash
export MAILMAN_BASE_URL="https://lists.example.com"
export MAILMAN_LIST_NAME="mylist"
export MAILMAN_ADMIN_PW="SuperSecret"
```

---

## Usage

### Dry-run (show changes only)

```bash
python mailman_unblock.py --dry-run
```

### Apply changes to all member pages

```bash
python mailman_unblock.py
```

### Limit to a specific page (e.g., `b`)

```bash
python mailman_unblock.py --dry-run --letter b
```

### Process multiple pages

```bash
python mailman_unblock.py --letters 1,abc
```

### Verbose / debug logging

```bash
python mailman_unblock.py --dry-run --verbose
```

---

## Logging

The script uses Python’s `logging` module:

- **INFO** messages show what’s being unblocked
- **DEBUG** messages (enabled with `--verbose`) show HTTP/login details and pages with no blocked members

---

## Safety

- **Dry-run mode** is the recommended first run — no changes are submitted
- Only the `*_nomail` checkboxes are cleared
- All other member settings and CSRF tokens are preserved
- The script posts to the real Mailman forms, so behavior matches clicking “Submit Your Changes” in the UI

---

## Notes

- Tested with Mailman **2.1.39** templates
- If your Mailman skin differs (button names, etc.), adjust the selectors in `process_letter()`
- You can schedule the script (e.g., with cron) to periodically re-enable blocked members