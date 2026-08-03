SHELL := /bin/bash
LAB := lab
PY := python

.PHONY: help setup break heal status probe watch watch-once scenarios logs describe teardown

help:             ## show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

setup:            ## create cluster, build+load image, deploy healthy state
	@$(LAB)/scripts/setup.sh

break:            ## break the cluster: make break S=01-oomkilled
	@$(LAB)/scripts/break.sh $(S)

heal:             ## restore the healthy baseline
	@$(LAB)/scripts/heal.sh

status:           ## pods, endpoints, events, container states
	@$(LAB)/scripts/status.sh

probe:            ## curl the service from inside the cluster
	@$(LAB)/scripts/probe.sh

watch:            ## run the synthetic checker (ctrl-c to stop)
	@$(PY) $(LAB)/watchdog/watchdog.py

watch-once:       ## one alert evaluation, then exit
	@$(PY) $(LAB)/watchdog/watchdog.py --once

logs:             ## current + previous logs for all app pods
	@echo "--- current ---"
	@kubectl logs -n shop -l app=orders-api --tail=40 || true
	@echo
	@echo "--- previous ---"
	@kubectl logs -n shop -l app=orders-api --previous --tail=40 || true

describe:         ## container states, exit codes, probe failures
	@kubectl describe pod -n shop -l app=orders-api \
		| grep -E "Name:|State:|Reason:|Exit Code:|Restart Count:|Warning" || true

scenarios:        ## list available scenarios
	@find $(LAB)/scenarios -mindepth 1 -maxdepth 1 -type d -exec basename {} \; | sort

teardown:         ## delete the kind cluster
	@$(LAB)/scripts/teardown.sh
