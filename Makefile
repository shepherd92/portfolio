PYTHON ?= python3

.PHONY: all test test-gui appimage windows coverage coverage_report

all: test

test:
	$(PYTHON) -m unittest discover -s tests -p '*_test.py' -v

# Isolate Tk/X11 focus state between test modules.
test-gui:
	@for test in tests/*_test.py; do \
		REBALANCE_GUI_TESTS=1 xvfb-run -a $(PYTHON) -m unittest discover -s tests -p "$${test##*/}" -v || exit $$?; \
	done

appimage:
	$(PYTHON) tools/build_appimage.py

windows:
	$(PYTHON) tools/build_windows.py

coverage:
	$(PYTHON) -m coverage run -m unittest discover -s tests -p '*_test.py' -v

coverage_report:
	$(PYTHON) -m coverage html
