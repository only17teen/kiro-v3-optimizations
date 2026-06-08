.PHONY: help install test lint format build docker-up docker-down clean docs benchmark

PYTHON := python3
PIP := pip3
PYTEST := pytest
BLACK := black
ISORT := isort
FLAKE8 := flake8
MYPY := mypy

help:
	@echo "Kiro Protocol v3.0 - Development Commands"
	@echo ""
	@echo "  make install       Install dependencies"
	@echo "  make install-dev   Install dev dependencies"
	@echo "  make test          Run all tests"
	@echo "  make test-fast     Run fast unit tests only"
	@echo "  make test-slow     Run slow integration tests"
	@echo "  make lint          Run all linters"
	@echo "  make format        Format code with black and isort"
	@echo "  make type-check    Run mypy type checking"
	@echo "  make build         Build Docker image"
	@echo "  make docker-up     Start local development stack"
	@echo "  make docker-down   Stop local development stack"
	@echo "  make docs          Build documentation"
	@echo "  make benchmark     Run performance benchmarks"
	@echo "  make clean         Clean build artifacts"
	@echo "  make ci            Run full CI pipeline locally"

install:
	$(PIP) install -e .

install-dev:
	$(PIP) install -e ".[dev,test,docs]"
	$(PIP) install pre-commit
	pre-commit install

test:
	$(PYTEST) tests/ -v --tb=short --cov=engine --cov-report=term-missing --cov-report=html:htmlcov

test-fast:
	$(PYTEST) tests/ -v -m "not slow" --tb=short

test-slow:
	$(PYTEST) tests/ -v -m "slow" --tb=short

test-integration:
	$(PYTEST) tests/integration/ -v --tb=short

test-chaos:
	$(PYTEST) tests/chaos/ -v --tb=short

test-load:
	@echo "Running Locust load tests..."
	locust -f tests/load/locustfile.py --host http://localhost:8080 --users 100 --spawn-rate 10 --run-time 5m --headless
	@echo "Running k6 load tests..."
	cd tests/load && ./run_k6.sh

lint:
	$(FLAKE8) engine/ tests/ benchmarks/ examples/ sdk/ --max-line-length=100 --extend-ignore=E203,W503
	$(BLACK) --check engine/ tests/ benchmarks/ examples/ sdk/
	$(ISORT) --check-only engine/ tests/ benchmarks/ examples/ sdk/

format:
	$(BLACK) engine/ tests/ benchmarks/ examples/ sdk/
	$(ISORT) engine/ tests/ benchmarks/ examples/ sdk/

type-check:
	$(MYPY) engine/ --ignore-missing-imports --show-error-codes

build:
	docker build -t kiro-v3:latest .

docker-up:
	docker-compose up -d --build

docker-down:
	docker-compose down -v

docker-logs:
	docker-compose logs -f kiro-v3-engine

docs:
	mkdocs build

docs-serve:
	mkdocs serve

benchmark:
	$(PYTHON) benchmarks/compare.py

benchmark-baseline:
	$(PYTHON) benchmarks/compare.py --save-baseline

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type f -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "build" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "dist" -exec rm -rf {} + 2>/dev/null || true

ci: install-dev lint type-check test benchmark
	@echo "CI pipeline complete"

security-scan:
	@echo "Running security scans..."
	bandit -r engine/ -f json -o security-report.json || true
	safety check || true

k8s-deploy:
	kubectl apply -k k8s/base/
	kubectl apply -k k8s/overlays/production/

k8s-delete:
	kubectl delete -k k8s/overlays/production/
	kubectl delete -k k8s/base/

helm-install:
	helm install kiro-v3 ./helm/kiro-v3 --namespace kiro-v3 --create-namespace

helm-upgrade:
	helm upgrade kiro-v3 ./helm/kiro-v3 --namespace kiro-v3

helm-uninstall:
	helm uninstall kiro-v3 --namespace kiro-v3

tf-plan:
	cd terraform/modules/aws && terraform plan

tf-apply:
	cd terraform/modules/aws && terraform apply

tf-destroy:
	cd terraform/modules/aws && terraform destroy

release-patch:
	bumpversion patch

release-minor:
	bumpversion minor

release-major:
	bumpversion major
