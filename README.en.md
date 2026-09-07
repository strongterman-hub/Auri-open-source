# Auri

**An AI companion that reaches out and gets to know you over time.**

[中文详细介绍](README.md) · [Website](https://auri.thinktocode.online/) · [Android download](https://auri.thinktocode.online/download)

Auri is an independently developed personal agent with a native Kotlin/Jetpack Compose Android client and a Python/FastAPI backend. Its main interface is one continuous conversation. It combines practical tools with long-term memory and context-aware proactive messages.

This repository contains the complete application source for both sides: chat, memory, proactive scheduling, health integration, authentication, Credits, Alipay integration, administration, website and direct APK updates. Production databases, accounts, credentials, private signing keys and deployment history are not included.

## Highlights

- **Continuous chat:** persistent history, local Room cache, idempotent sends and asynchronous replies. Message acceptance and reply generation are separate states.
- **Memory:** durable user notes, structured events, temporal boundaries, correction relationships, gradual decay of ordinary details and rolling conversation summaries.
- **Proactive contact:** source freshness, do-not-disturb gates, evidence checks, exposure acknowledgments, reply attribution, backoff and recovery probes.
- **Optional health context:** experimental Xiaomi Health Cloud integration, normalized metrics, workout records, sleep and recovery scores. No medical diagnosis claims.
- **Shared calendar:** month view, manual edits, chat-based reads and writes, recurring events, and scheduled reminders.
- **Tools:** calendar, search, web pages, weather, location, calculations, conversions, dates, reminders and todos.
- **Attachments:** images and common document formats; actual model and parser support is required.
- **Accounts and operations:** email verification, password reset, token hashing, account deletion, usage logs, Credits ledger and payment callback validation.
- **Operations center:** responsive overview, full account/Credits view, paginated usage and tool-call filters, release controls, runtime status and pricing reference behind a separate admin session.
- **Android channels:** store builds without self-install updates; direct builds with versioned APK download and integrity checks.

## Quick start

Python 3.12 is recommended:

```bash
git clone https://github.com/strongterman-hub/Auri-open-source.git
cd Auri-open-source/server
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Visit `/v1/health` and `/docs`. The sample environment uses a local **Echo** model and disables billing enforcement, payment, email verification and external background integrations. Echo checks connectivity; it does not demonstrate AI intelligence.

For a real model, configure your own `AURI_LLM_PROVIDER=openai-compatible`, `AURI_LLM_BASE_URL`, `AURI_LLM_API_KEY` and `AURI_LLM_MODEL`. Tool calling and streaming must be supported by the provider. Model names and the project's pricing table are not a guarantee of current provider availability or cost.

From the repository root, `python scripts/smoke_local.py` exercises a running local Echo instance without printing credentials.

## Android

Install JDK 17, Android SDK 35 and Gradle 8.9, then open `android/` in Android Studio or run:

```bash
cd android
gradle :app:testStoreDebugUnitTest :app:assembleStoreDebug
```

Debug defaults to `http://10.0.2.2:8010/v1` for the standard Android emulator and uses the application ID `com.auri.community.debug`. Override `-PapiBaseUrl=...`, `-PapplicationId=...` and optionally `-PjpushAppKey=...`. Release builds require your own API address and signing setup. No official push credentials are included.

## Documentation

The detailed documentation is currently in Chinese:

- [Getting started](docs/GETTING_STARTED.md)
- [Product](docs/PRODUCT.md) and [architecture](docs/ARCHITECTURE.md)
- [Configuration](docs/CONFIGURATION.md)
- [Android build and signing](docs/ANDROID.md)
- [Deployment and backups](docs/DEPLOYMENT.md)
- [Development](docs/DEVELOPMENT.md), [roadmap](docs/ROADMAP.md) and [release notes](docs/RELEASE_NOTES.md)
- [Contributing](CONTRIBUTING.md) and [security reporting](SECURITY.md)

## Boundaries

This is a single-process MVP, not a validated multi-replica service. OEM background notification reliability, public-service compliance and vendor account setup remain the operator's responsibilities. The Xiaomi adapter is unofficial and experimental; source licensing does not grant permission to access third-party services commercially. Intents currently provide CRUD, not a general natural-language trigger engine. iOS and native HarmonyOS clients are not implemented.

## Licenses

The server and root documentation/tools use **AGPL-3.0-only**. Original code in the independent Android client uses **MIT**. Upstream GPL code and third-party SDK terms are retained separately. Both AGPL and MIT allow commercial use; applicable source-offer and notice obligations still apply.

See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for scope and credits to mi-bridge, mi-fitness-mcp, mi-fitness-mcp-cn and mijia-api. Forks should clearly identify their operator and use their own branding, credentials and signing keys.
