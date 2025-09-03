PY_VERSION := 3.10.16
VENV := mailmanunblock-$(PY_VERSION)

all: configure test

clean:
	rm -rf build dist .eggs .DS_Store *.egg-info

distclean: clean
	pyenv virtualenv-delete -f $(VENV)
	rm -f .python-version

configure: python-virtualenv

python-virtualenv: .python-version

.python-version:
	@if ! pyenv versions --bare | grep -qx "$(PY_VERSION)"; then \
		pyenv install $(PY_VERSION); \
	fi
	@if ! pyenv virtualenvs --bare | grep -qx "$(VENV)"; then \
		pyenv virtualenv $(PY_VERSION) $(VENV); \
	fi
	pyenv local $(VENV)
	python -m pip install --upgrade pip
	python -m pip install -r requirements.txt

test:
	. ./env.sh >/dev/null 2>&1 || true; python3 ./mailman_unblock.py --dry-run

unblock:
	. ./env.sh >/dev/null 2>&1 || true; python3 ./mailman_unblock.py

# Unblock a specific letter: make LETTER=c unblock-letter
unblock-letter:
	@if [ -z "$(LETTER)" ]; then \
		echo "Error: provide LETTER, e.g. 'make LETTER=c unblock-letter'"; \
		exit 1; \
	fi
	. ./env.sh >/dev/null 2>&1 || true; python3 ./mailman_unblock.py --letter $(LETTER)

# Dry-run for a specific letter: make LETTER=c test-letter
test-letter:
	@if [ -z "$(LETTER)" ]; then \
		echo "Error: provide LETTER, e.g. 'make LETTER=c test-letter'"; \
		exit 1; \
	fi
	. ./env.sh >/dev/null 2>&1 || true; python3 ./mailman_unblock.py --dry-run --letter $(LETTER)

check:
	ruff check && ruff format --check

fix:
	ruff check --fix

reformat:
	ruff format

.PHONY: all clean distclean python-virtualenv configure test unblock unblock-letter test-letter check fix reformat
