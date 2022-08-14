CONTAINER_REGISTRY_URL=eu.gcr.io
PROJECT_ID=ithil-goerli-bots
SERVICE=liquidation-bot-py
IMAGE=$(CONTAINER_REGISTRY_URL)/$(PROJECT_ID)/$(SERVICE):latest

.PHONY: upgrade-dependencies
upgrade-dependencies:
	poetry update

.PHONY: build-docker-image
build-docker-image:
	poetry export --without-hashes >> requirements.txt && \
	    docker build . \
	    --iidfile .dockeriid \
	    --tag $(IMAGE)

.PHONY: push-image-to-container-registry
push-image-to-container-registry: build-docker-image
	docker push $(IMAGE)

.PHONY: start
start: build-docker-image
	docker run -it -p 8080:8080 $$(cat .dockeriid)

.PHONY: monitor
monitor:
	watch -n 5 curl -s https://liquidation-bot-py-27a7uwzraq-lz.a.run.app
