PYTHON ?= python3

.PHONY: all test test-gui appimage coverage coverage_report

all: test

test:
	$(PYTHON) -m unittest discover -s tests -p '*_test.py' -v

test-gui:
	REBALANCE_GUI_TESTS=1 xvfb-run -a $(PYTHON) -m unittest discover -s tests -p '*_test.py' -v

appimage:
	$(PYTHON) tools/build_appimage.py

coverage:
	$(PYTHON) -m coverage run -m unittest discover -s tests -p '*_test.py' -v

coverage_report:
	$(PYTHON) -m coverage html
