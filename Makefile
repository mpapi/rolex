.PHONY: lint test

ifneq ($(ENV),ci)
export PATH := env/bin:$(PATH)
endif

env: dev_requirements.txt
	test -d env || virtualenv env
	pip install -U -r dev_requirements.txt
	touch env

lint: env
	flake8 rolex *.py

test: env
	nosetests
