.PHONY: lint test

env: dev_requirements.txt
	test -d env || virtualenv env
	env/bin/pip install -U -r dev_requirements.txt
	touch env

lint: env
	env/bin/flake8 rolex *.py

test: env
	env/bin/nosetests
