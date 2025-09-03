app: configure dist/mailman_unblock.app/Contents/MacOS/mailman_unblock

dist/mailman_unblock.app/Contents/MacOS/mailman_unblock: mailman_unblock.py requirements.txt
	python setup.py py2app

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

debug: app
	./dist/mailman_unblock.app/Contents/MacOS/mailman_unblock

install: app
	cp -r dist/mailman_unblock.app /Applications/

uninstall:
	rm -rf /Applications/mailman_unblock.app

run: install
	open /Applications/mailman_unblock.app

.PHONY: app clean distclean debug install uninstall install-config python-virtualenv
