all: configure test

clean:
	rm -rf build dist .eggs .DS_Store *.egg-info

distclean: clean
	pyenv virtualenv-delete -f mailmanunblock-3.10.17
	rm -f .python-version

configure: python-virtualenv

python-virtualenv: .python-version

.python-version:
	@if ! pyenv versions --bare | grep -qx "3.10.16"; then \
		pyenv install 3.10.16; \
	fi
	@if ! pyenv virtualenvs --bare | grep -qx "mailmanunblock-3.10.16"; then \
		pyenv virtualenv 3.10.16 mailmanunblock-3.10.16; \
	fi
	pyenv local mailmanunblock-3.10.16
	python -m pip install --upgrade pip
	python -m pip install -r requirements.txt

test:
	python3 ./mailman_unblock.py --dry-run

unblock:
	python3 ./mailman_unblock.py

check:
	ruff check && ruff format --check

fix:
	ruff check --fix

reformat:
	ruff format

.PHONY: clean distclean python-virtualenv configure test unblock check 
