# Checks and deploys for the command center. `make check` runs on the workstation
# before a deploy; `make check-configs` runs again on the Pi before anything is
# applied, with the same pinned images that will run the config.

PROM_IMAGE := $(shell grep -oE 'prom/prometheus:v[0-9.]+' compose.yml)
AM_IMAGE   := $(shell grep -oE 'prom/alertmanager:v[0-9.]+' compose.yml)
BB_IMAGE   := $(shell grep -oE 'prom/blackbox-exporter:v[0-9.]+' compose.yml)
AS_ME      := --user $(shell id -u):$(shell id -g)
AM_CONFIG  ?= run/alertmanager.check.yml

.PHONY: check test check-configs check-prom check-blackbox check-am check-dashboards deploy

check: test check-configs

test: .venv/bin/pytest
	.venv/bin/pytest -q

.venv/bin/pytest:
	python3 -m venv .venv
	.venv/bin/pip install -q pytest pyyaml

check-configs: check-prom check-blackbox check-am check-dashboards

check-prom:
	docker run --rm $(AS_ME) -v $(CURDIR)/prometheus:/etc/prometheus:ro --entrypoint promtool $(PROM_IMAGE) check config /etc/prometheus/prometheus.yml
	docker run --rm $(AS_ME) -v $(CURDIR)/prometheus:/etc/prometheus:ro --entrypoint sh $(PROM_IMAGE) -c 'promtool test rules /etc/prometheus/tests/*.yml'

check-blackbox:
	docker run --rm $(AS_ME) -v $(CURDIR)/blackbox:/etc/blackbox:ro $(BB_IMAGE) --config.file=/etc/blackbox/blackbox.yml --config.check

check-am:
	@if [ "$(AM_CONFIG)" = "run/alertmanager.check.yml" ]; then \
		mkdir -p run && python3 scripts/render_config.py alertmanager/alertmanager.yml.tmpl .env.example $(AM_CONFIG); fi
	docker run --rm $(AS_ME) -v $(CURDIR):/w -w /w --entrypoint amtool $(AM_IMAGE) check-config $(AM_CONFIG)
	scripts/check_routes.sh $(AM_IMAGE) $(AM_CONFIG) alertmanager/routes.test

check-dashboards:
	python3 scripts/check_dashboards.py grafana/dashboards

deploy:
	scripts/deploy.sh
