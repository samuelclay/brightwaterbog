# brightwaterbog — photo digitization
#
# Common entry point: `make scan` (lay photos on the bed first).
# Override resolution/color: `make scan DPI=300 COLOR=gray`.

DPI    ?= 600
COLOR  ?= color
HOST   ?= 127.0.0.1
PORT   ?= 8766
LOG    ?= logs/scanned_gallery.log
SERVER_LABEL ?= brightwaterbog.scanned_gallery
DOCKER ?= docker
CAMERA_MONITOR_COMPOSE := docker-compose.camera-monitor.yml
PY     := .venv/bin/python
SWIFTC := swiftc
CLANG  := clang

.PHONY: help setup build scan scan-no-tag server capture list camera-monitor eufy-monitor camera-monitor-docker camera-monitor-docker-stop camera-monitor-docker-logs deploy clean

help:
	@echo "make setup        Create venv, install deps, build the scanner CLI"
	@echo "make build        Compile scanner CLIs"
	@echo "make scan         Scan bed -> crop -> AI tag -> organize  (DPI=$(DPI) COLOR=$(COLOR))"
	@echo "make scan-no-tag  Scan + crop only, skip the AI tagging step"
	@echo "make server       Run Capture with colored startup logs (HOST=$(HOST) PORT=$(PORT) LOG=$(LOG))"
	@echo "make capture      Alias for make server"
	@echo "make list         List scanners the Mac can see"
	@echo "make camera-monitor Run the local Home Assistant camera wall"
	@echo "make camera-monitor-docker Build and run the portable camera monitor container"
	@echo "make camera-monitor-docker-stop Stop the portable camera monitor container"
	@echo "make camera-monitor-docker-logs Follow portable camera monitor logs"
	@echo "make deploy       Deploy the camera monitor Home Assistant add-on"
	@echo "make clean        Remove staging crops and Python caches"
	@echo ""
	@echo "Options: make scan DPI=300 COLOR=gray   (needs ANTHROPIC_API_KEY for tagging)"

setup:
	test -d .venv || python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -r requirements.txt
	$(MAKE) build

build: scanner/icascan scanner/epsonscan2

scanner/icascan: scanner/icascan.swift
	$(SWIFTC) -O scanner/icascan.swift -o scanner/icascan -framework ImageCaptureCore

scanner/epsonscan2: scanner/epsonscan2.m
	$(CLANG) -fobjc-arc -framework Foundation -o scanner/epsonscan2 scanner/epsonscan2.m

scan: build
	./digitize.sh --dpi $(DPI) --color $(COLOR)

scan-no-tag: build
	./digitize.sh --dpi $(DPI) --color $(COLOR) --no-tag

server: build
	@mkdir -p "$$(dirname "$(LOG)")"
	@ts=$$(date '+%Y-%m-%d %H:%M:%S %Z'); \
	  printf "  \033[2m%s\033[0m  \033[38;5;179m[make]\033[0m Capture server requested (host=$(HOST) port=$(PORT) log=$(LOG))\n" "$$ts" | tee -a "$(LOG)"
	@if launchctl print gui/$$(id -u)/$(SERVER_LABEL) >/dev/null 2>&1; then \
	  ts=$$(date '+%Y-%m-%d %H:%M:%S %Z'); \
	  printf "  \033[2m%s\033[0m  \033[38;5;179m[make]\033[0m stopping launchd job $(SERVER_LABEL)\n" "$$ts" | tee -a "$(LOG)"; \
	  launchctl bootout gui/$$(id -u)/$(SERVER_LABEL) >/dev/null 2>&1 || launchctl remove $(SERVER_LABEL) >/dev/null 2>&1 || true; \
	  sleep 1; \
	fi
	@pids=$$(lsof -nP -iTCP:$(PORT) -sTCP:LISTEN -t 2>/dev/null); \
	  if [ -n "$$pids" ]; then ts=$$(date '+%Y-%m-%d %H:%M:%S %Z'); \
	    printf "  \033[2m%s\033[0m  \033[38;5;179m[make]\033[0m freeing :$(PORT) (killing %s)\n" "$$ts" "$$pids" | tee -a "$(LOG)"; \
	    kill $$pids 2>/dev/null || true; sleep 1; \
	    pids=$$(lsof -nP -iTCP:$(PORT) -sTCP:LISTEN -t 2>/dev/null); \
	    if [ -n "$$pids" ]; then ts=$$(date '+%Y-%m-%d %H:%M:%S %Z'); \
	      printf "  \033[2m%s\033[0m  \033[38;5;131m[make]\033[0m :$(PORT) still bound; force killing %s\n" "$$ts" "$$pids" | tee -a "$(LOG)"; \
	      kill -9 $$pids 2>/dev/null || true; fi; sleep 1; fi
	@$(PY) tools/scanned_gallery.py --host $(HOST) --port $(PORT) --log "$(LOG)" --color always

capture: server

list: build
	./scanner/icascan list

camera-monitor:
	python3 tools/camera_monitor.py

eufy-monitor: camera-monitor

camera-monitor-docker:
	$(DOCKER) compose -f $(CAMERA_MONITOR_COMPOSE) up -d --build

camera-monitor-docker-stop:
	$(DOCKER) compose -f $(CAMERA_MONITOR_COMPOSE) down

camera-monitor-docker-logs:
	$(DOCKER) compose -f $(CAMERA_MONITOR_COMPOSE) logs -f camera-monitor

deploy:
	./tools/deploy_camera_monitor.sh

clean:
	rm -rf photos/_staging/*
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
