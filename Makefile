# MTF — common commands. `make help` lists them.
# The framework never builds the application under test: APK/.app paths are inputs.
ENV ?= local
APK ?= artifacts/android/app-debug.apk
DEVICE ?=
IOS_APP ?= artifacts/ios/MobileTestPlayground.app
SIMULATOR ?= iPhone 17
IOS_VERSION ?=
MTF := uv run mtf
ANDROID_RUNNER := framework/android-runner
IOS_RUNNER := framework/ios-runner

.PHONY: help setup lint format typecheck test check validate contracts-check \
	android-runner android-runner-lint ios-runner ios-runner-lint \
	test-api test-android test-ios docker-build docker-test-api clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

setup: ## Create the Python environment (framework + dev tools + optional Anthropic provider)
	uv sync --extra anthropic

lint: ## ruff lint + format check
	uv run ruff check framework
	uv run ruff format --check framework

format: ## Apply ruff formatting
	uv run ruff format framework
	uv run ruff check --fix framework

typecheck: ## mypy (strict)
	uv run mypy

test: ## Unit tests (no device, no backend)
	uv run pytest

contracts-check: ## Committed plan schema matches the DSL models
	$(MTF) contracts schema --check

validate: ## Validate every plan against contracts, catalog and policy
	$(MTF) validate plans --env $(ENV)

check: lint typecheck test contracts-check validate ## Everything that runs without a device

# --- framework runners (never the app) ---
android-runner: ## Build the framework's UIAutomator runner APK (not the app)
	cd $(ANDROID_RUNNER) && ./gradlew --no-daemon :runner:assembleDebug

android-runner-lint: ## ktlint + Android Lint for the runner
	cd $(ANDROID_RUNNER) && ./gradlew --no-daemon :runner:ktlintCheck :runner:lintDebug

ios-runner: ## Build the framework's XCUITest runner products (build-for-testing; not the app)
	cd $(IOS_RUNNER) && xcodegen generate --quiet && xcodebuild build-for-testing \
		-project MTFRunner.xcodeproj -scheme MTFRunner -destination 'generic/platform=iOS Simulator' \
		-derivedDataPath build/DerivedData -quiet

ios-runner-lint: ## SwiftLint (strict) for the framework's XCUITest runner
	cd $(IOS_RUNNER) && swiftlint lint --strict --quiet

# --- test runs against PREBUILT artifacts ---
test-api: ## API plans against the backend of ENV (no simulator)
	$(MTF) run plans/api --platform api --env $(ENV)

test-android: ## Shared + Android plans against APK on DEVICE (or the only connected device)
	$(MTF) run plans/shared plans/android --platform android --env $(ENV) --apk $(APK) $(if $(DEVICE),--device $(DEVICE),)

test-ios: ## Shared + iOS plans against IOS_APP on SIMULATOR (test-without-building)
	$(MTF) run plans/shared plans/ios --platform ios --env $(ENV) --app $(IOS_APP) --simulator "$(SIMULATOR)" $(if $(IOS_VERSION),--ios-version $(IOS_VERSION),)

# --- Docker (CLI + API tests only; no emulator, no Xcode) ---
docker-build: ## Build the MTF image
	docker build -t mtf:local .

docker-test-api: ## Prebuilt backend JAR (artifacts/backend/backend.jar) + API plans in Compose
	docker compose run --rm --build mtf-api; status=$$?; docker compose down; exit $$status

clean: ## Remove reports and caches (keeps artifacts)
	rm -rf reports .pytest_cache .mypy_cache .ruff_cache
