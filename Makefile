# Checks and deploys for the command center. `make check` runs on the workstation
# before a deploy; `make check-configs` runs again on the Pi before anything is
# applied. Where this user can run Docker (the Pi) the checks use the very images
# compose.yml pins; elsewhere they use the same versions' release binaries,
# fetched and checksum-verified into .tools/ by scripts/fetch_tools.sh.

PROM_IMAGE := $(shell grep -oE 'prom/prometheus:v[0-9.]+' compose.yml)
AM_IMAGE   := $(shell grep -oE 'prom/alertmanager:v[0-9.]+' compose.yml)
BB_IMAGE   := $(shell grep -oE 'prom/blackbox-exporter:v[0-9.]+' compose.yml)
AM_CONFIG  ?= run/alertmanager.check.yml

ifeq ($(shell docker info >/dev/null 2>&1 && echo yes),yes)
IN_DOCKER := docker run --rm --user $(shell id -u):$(shell id -g) -v $(CURDIR):/w -w /w
PROMTOOL  := $(IN_DOCKER) --entrypoint promtool $(PROM_IMAGE)
AMTOOL    := $(IN_DOCKER) --entrypoint amtool $(AM_IMAGE)
BLACKBOX  := $(IN_DOCKER) $(BB_IMAGE)
TOOLS     :=
else
PROMTOOL  := .tools/promtool
AMTOOL    := .tools/amtool
BLACKBOX  := .tools/blackbox_exporter
TOOLS     := .tools/promtool
endif

.PHONY: check test check-configs check-prom check-blackbox check-am check-dashboards deploy

check: test check-configs

test: .venv/bin/pytest
	.venv/bin/pytest -q

.venv/bin/pytest:
	python3 -m venv .venv
	.venv/bin/pip install -q pytest pyyaml

.tools/promtool:
	scripts/fetch_tools.sh

check-configs: check-prom check-blackbox check-am check-dashboards

check-prom: $(TOOLS)
	$(PROMTOOL) check config prometheus/prometheus.yml
	$(PROMTOOL) test rules prometheus/tests/*.yml

check-blackbox: $(TOOLS)
	$(BLACKBOX) --config.file=blackbox/blackbox.yml --config.check

check-am: $(TOOLS)
	@if [ "$(AM_CONFIG)" = "run/alertmanager.check.yml" ]; then \
		mkdir -p run && python3 scripts/render_config.py alertmanager/alertmanager.yml.tmpl .env.example $(AM_CONFIG); fi
	$(AMTOOL) check-config $(AM_CONFIG)
	AMTOOL="$(AMTOOL)" scripts/check_routes.sh $(AM_CONFIG) alertmanager/routes.test

check-dashboards:
	python3 scripts/check_dashboards.py grafana/dashboards

deploy:
	scripts/deploy.sh
