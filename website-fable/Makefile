# ──────────────────────────────────────────────────────────────
#  Brightwater Bog — dev server harness
#  `make`        (re)start the dev stack and tail the log
#  `make help`   everything else
#
#  Idempotent: every `make` kills whatever is already running
#  (by pidfile process-group AND by port sweep), then starts
#  fresh — safe to run from multiple shells/agents at once.
# ──────────────────────────────────────────────────────────────
SHELL := /bin/bash

LOG     := dev.log
PIDFILE := .dev.pid
ROTPID  := .rotate.pid
PORTS   := 4321 8788
# auto-clear threshold: 10 MB
MAXBYTES := 10485760

# keep astro/vite/concurrently emitting ANSI even though stdout is a file
COLOR_ENV := FORCE_COLOR=1 CLICOLOR_FORCE=1

# ── ANSI palette (256-color bog tones) ──
B    := \033[1m
DIM  := \033[2m
R    := \033[0m
MOSS := \033[38;5;114m
FERN := \033[1;38;5;40m
POND := \033[1;38;5;44m
TEAL := \033[38;5;30m
LILY := \033[1;38;5;213m
SUN  := \033[1;38;5;220m
CLAY := \033[38;5;173m
RUST := \033[1;38;5;203m
MIST := \033[38;5;152m

.PHONY: all start stop restart status logs clean help banner sync-photos

all: banner restart logs

banner:
	@printf "\n"
	@printf "$(FERN)        )\\   $(POND)╔══════════════════════════════════════════════╗$(R)\n"
	@printf "$(MOSS)       (  \\  $(POND)║$(R)   $(SUN)🌿  $(B)B R I G H T W A T E R$(R)   $(FERN)$(B)B O G$(R)  $(SUN)🌿$(R)      $(POND)║$(R)\n"
	@printf "$(FERN)        )  ) $(POND)║$(R)$(MIST)        astro dev  ·  image server            $(R)$(POND)║$(R)\n"
	@printf "$(MOSS)       ( ,(  $(POND)╚══════════════════════════════════════════════╝$(R)\n"
	@printf "$(TEAL)    ~~~~$(LILY)\"\"$(TEAL)~~~~~$(R)  $(MIST)site$(R)  $(POND)http://localhost:4321$(R)\n"
	@printf "$(TEAL)  ~~~~~~~~~~~~~~$(R)  $(MIST)imgs$(R)  $(POND)http://localhost:8788$(R)\n"
	@printf "\n"

# Kill everything: pidfile process group, rotator, then anything
# still listening on our ports (catches instances we didn't start).
stop:
	@if [ -f $(PIDFILE) ]; then \
		PID=$$(cat $(PIDFILE)); \
		if kill -0 $$PID 2>/dev/null; then \
			kill -TERM -- -$$PID 2>/dev/null || kill -TERM $$PID 2>/dev/null; \
			sleep 1; \
			kill -KILL -- -$$PID 2>/dev/null; \
			printf "$(RUST)■$(R) stopped $(DIM)(pid $$PID)$(R)\n"; \
		fi; \
	fi
	@[ -f $(ROTPID) ] && kill $$(cat $(ROTPID)) 2>/dev/null || true
	@rm -f $(PIDFILE) $(ROTPID)
	@STRAYS=$$(lsof -nP $(foreach p,$(PORTS),-iTCP:$(p)) -sTCP:LISTEN -t 2>/dev/null | sort -u); \
	if [ -n "$$STRAYS" ]; then \
		kill -TERM $$STRAYS 2>/dev/null; sleep 1; kill -KILL $$STRAYS 2>/dev/null; \
		printf "$(RUST)■$(R) reclaimed ports $(SUN)$(PORTS)$(R) $(DIM)(pids: $$(echo $$STRAYS | tr '\n' ' '))$(R)\n"; \
	fi

start:
	@if [ ! -d node_modules ]; then \
		printf "$(LILY)◆$(R) installing dependencies…\n"; npm install; \
	fi
	@printf '$(POND)%s$(R) $(FERN)──── ❦ dev server starting ❦ ────$(R)\n' "$$(date '+%F %T')" >> $(LOG)
	@nohup env $(COLOR_ENV) perl -e 'setpgrp(0,0); exec @ARGV' -- npm run dev >> $(LOG) 2>&1 & echo $$! > $(PIDFILE)
	@( while [ -f $(PIDFILE) ] && kill -0 $$(cat $(PIDFILE)) 2>/dev/null; do \
		if [ -f $(LOG) ] && [ $$(wc -c < $(LOG)) -ge $(MAXBYTES) ]; then \
			: > $(LOG); \
			printf '$(POND)%s$(R) $(SUN)──── ✂ log hit 10MB, auto-cleared ✂ ────$(R)\n' "$$(date '+%F %T')" >> $(LOG); \
		fi; \
		sleep 5; \
	done; rm -f $(ROTPID) ) >/dev/null 2>&1 & echo $$! > $(ROTPID)
	@printf "$(FERN)●$(R) started $(DIM)(pid $$(cat $(PIDFILE)), log → $(LOG), auto-cleared @ 10MB)$(R)\n"

restart: stop start

logs:
	@printf "$(MIST)── tailing $(B)$(LOG)$(R)$(MIST) · Ctrl-C detaches (server keeps running; $(R)$(SUN)make stop$(R)$(MIST) to kill) ──$(R)\n"
	@tail -n 40 -F $(LOG)

status:
	@if [ -f $(PIDFILE) ] && kill -0 $$(cat $(PIDFILE)) 2>/dev/null; then \
		printf "$(FERN)●$(R) $(B)running$(R) $(DIM)(pid $$(cat $(PIDFILE)))$(R)\n"; \
	else \
		printf "$(CLAY)○ stopped$(R)\n"; \
	fi
	@for p in $(PORTS); do \
		if lsof -nP -iTCP:$$p -sTCP:LISTEN -t >/dev/null 2>&1; then \
			printf "  $(FERN)✓$(R) port $(POND)$$p$(R) listening\n"; \
		else \
			printf "  $(RUST)✗$(R) $(DIM)port $$p quiet$(R)\n"; \
		fi; \
	done
	@if [ -f $(LOG) ]; then \
		printf "  $(MIST)log $(LOG) · $(SUN)$$(du -h $(LOG) | cut -f1 | tr -d ' ')$(R)$(MIST) of 10M$(R)\n"; \
	fi

# Replace any thumbnail-sized photos with full-size originals from
# Photos.app (downloads from iCloud as needed), then rebuild the catalog.
sync-photos:
	@printf "$(LILY)◆$(R) syncing photos from Photos.app…\n"
	@python3 ../tools/sync_photos.py
	@npm run catalog
	@printf "$(FERN)●$(R) photos synced $(DIM)(full-size originals + fresh catalog)$(R)\n"

clean: stop
	@rm -f $(LOG)
	@rm -rf dist .astro
	@printf "$(LILY)◆$(R) cleaned $(DIM)(log, dist/, .astro/)$(R)\n"

help: banner
	@printf "  $(SUN)make$(R)          $(MIST)(re)start dev stack + tail the log$(R)\n"
	@printf "  $(FERN)make restart$(R)  $(MIST)kill anything running, start fresh$(R)\n"
	@printf "  $(FERN)make start$(R)    $(MIST)start in the background (no kill, no tail)$(R)\n"
	@printf "  $(POND)make logs$(R)     $(MIST)tail the log$(R)\n"
	@printf "  $(POND)make status$(R)   $(MIST)pid, ports, log size$(R)\n"
	@printf "  $(RUST)make stop$(R)     $(MIST)kill the dev stack (pid + port sweep)$(R)\n"
	@printf "  $(RUST)make clean$(R)    $(MIST)stop + remove log, dist/, .astro/$(R)\n"
	@printf "  $(SUN)make sync-photos$(R) $(MIST)re-export any thumbnail-sized photos full-size + rebuild catalog$(R)\n"
	@printf "\n"
