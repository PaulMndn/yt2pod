# Version label from git: v0.1.0, v0.1.0-3-gabc1234, or …-dirty with uncommitted changes
VERSION ?= $(shell git describe --tags --always --dirty)
export YT2POD_VERSION := $(VERSION)

.PHONY: build deploy rollback logs release dev

build:            ## Build the image from the working directory
	docker compose build

deploy: build     ## Build and (re)start the container
	docker compose up -d

rollback:         ## Start a previously built image: make rollback VERSION=v0.1.0
	docker compose up -d --no-build

logs:             ## Follow the container logs
	docker compose logs -f

release:          ## Tag a release: make release VERSION=v0.2.0
ifeq ($(origin VERSION),file)
	$(error Pass the new version explicitly, e.g. make release VERSION=v0.2.0)
endif
	@git diff --quiet && git diff --cached --quiet || (echo "Uncommitted changes, commit first"; exit 1)
	git tag $(VERSION)
	@echo "Tagged $(VERSION). Run 'make deploy' to build and start it."

dev:              ## Run locally without Docker
	DATA_DIR=./data .env/bin/python -m yt2pod
