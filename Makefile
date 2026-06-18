# brightwaterbog — photo digitization
#
# Common entry point: `make scan` (lay photos on the bed first).
# Override resolution/color: `make scan DPI=300 COLOR=gray`.

DPI    ?= 600
COLOR  ?= color
PY     := .venv/bin/python
SWIFTC := swiftc

.PHONY: help setup build scan scan-no-tag list camera-monitor eufy-monitor deploy clean

help:
	@echo "make setup        Create venv, install deps, build the scanner CLI"
	@echo "make build        Compile scanner/icascan from Swift"
	@echo "make scan         Scan bed -> crop -> AI tag -> organize  (DPI=$(DPI) COLOR=$(COLOR))"
	@echo "make scan-no-tag  Scan + crop only, skip the AI tagging step"
	@echo "make list         List scanners the Mac can see"
	@echo "make camera-monitor Run the local Home Assistant camera wall"
	@echo "make deploy       Deploy the camera monitor Home Assistant add-on"
	@echo "make clean        Remove staging crops and Python caches"
	@echo ""
	@echo "Options: make scan DPI=300 COLOR=gray   (needs ANTHROPIC_API_KEY for tagging)"

setup:
	test -d .venv || python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -r requirements.txt
	$(MAKE) build

build: scanner/icascan

scanner/icascan: scanner/icascan.swift
	$(SWIFTC) -O scanner/icascan.swift -o scanner/icascan -framework ImageCaptureCore

scan: build
	./digitize.sh --dpi $(DPI) --color $(COLOR)

scan-no-tag: build
	./digitize.sh --dpi $(DPI) --color $(COLOR) --no-tag

list: build
	./scanner/icascan list

camera-monitor:
	python3 tools/camera_monitor.py

eufy-monitor: camera-monitor

deploy:
	./tools/deploy_camera_monitor.sh

clean:
	rm -rf photos/_staging/*
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
