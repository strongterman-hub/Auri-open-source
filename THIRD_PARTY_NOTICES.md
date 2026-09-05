# Licensing and third-party notices

## Scope

This repository aggregates a Python server and an independent Android client communicating over HTTP. Licenses are assigned by component, not by the repository name:

| Component | License |
|---|---|
| Auri server original work; root documentation, scripts and CI | AGPL-3.0-only, see [LICENSE](LICENSE) |
| Original Android client source | MIT, see [android/LICENSE](android/LICENSE) |
| Xiaomi cloud adapter derived from mi-bridge | AGPL-3.0-only, retaining earlier upstream MIT notices |
| QR login port derived from mijia-api | Upstream GPL-3.0 terms retained; GPLv3 section 13 permits the combination with AGPLv3 code |
| External dependencies and vendor SDKs | Their respective upstream licenses/terms; no blanket relicensing |

Copyright (c) 2026 strongterman-hub and Auri contributors, except upstream work credited below. Auri adaptations in this source snapshot were made in 2026. The full server combination is distributed with the network-source obligations applicable under AGPLv3; upstream GPL-covered portions retain their own license.

## Xiaomi integration provenance

`server/app/integrations/xiaomi/__init__.py` identifies the cloud adapter as a port of **shkyyy18/mi-bridge**. Auri modifies the integration for its HTTP service, per-account credentials, normalized storage, source selection, pagination, error handling and tests.

- [shkyyy18/mi-bridge](https://github.com/shkyyy18/mi-bridge): current upstream license AGPL-3.0-only. The complete upstream license notice, including MIT history, is preserved at [LICENSES/mi-bridge-NOTICE.txt](LICENSES/mi-bridge-NOTICE.txt).
- [kubulashvili/mi-fitness-mcp](https://github.com/kubulashvili/mi-fitness-mcp): upstream MIT work, copyright Aleksej Kubulashvili, preserved in that notice.
- [binglua/mi-fitness-mcp-cn](https://github.com/binglua/mi-fitness-mcp-cn): upstream China-region work credited by mi-bridge.
- [Do1e/mijia-api](https://github.com/Do1e/mijia-api): QR login port in `server/app/integrations/xiaomi/qr_login.py`; GPL license text preserved at [LICENSES/mijia-api-GPL-3.0.txt](LICENSES/mijia-api-GPL-3.0.txt).

This publication does not claim Xiaomi endorsement, an official partnership, or permission to commercially access a private platform API. Copyright permissions for code and authorization to access a third-party service are separate matters.

## Dependencies

Python dependencies are declared in `server/pyproject.toml`; Android dependencies and their versions are in `android/app/build.gradle.kts`. Installers fetch those packages from their configured upstream repositories; the source snapshot does not vendor the SDK binaries.

Main dependency families include FastAPI, Uvicorn, Pydantic, HTTPX, cryptography, qrcode, Pillow, pypdf, python-docx, openpyxl, pytest, AndroidX/Compose/Room, Kotlin and Markwon. Preserve dependency notices when redistributing binaries and review transitive dependency licenses for the exact versions you ship.

JPush and Alipay are vendor SDKs, not relicensed under Auri's MIT or AGPL terms. Obtain your own account credentials, follow their integration/privacy terms, and do not describe a resulting APK as entirely free of proprietary dependencies. This snapshot is not an F-Droid-ready distribution.

The existing memory design references Hermes/OpenClaw concepts. This is design attribution, not an assertion that their complete source or trademarks are included.

## Branding, data and services

The source licenses do not grant trademark rights or imply official endorsement. Forks should identify their operator and use distinct application IDs, signing credentials and service configuration. Screenshots already shown on the official website illustrate the UI; they are not a dataset for extraction or training.

No production database, user chat/health/location record, private key, production token, signing keystore, provider account or paid-service entitlement is granted by this source release.

## Commercial use

Both AGPL and MIT permit commercial use. AGPL imposes corresponding-source and notice obligations when applicable, including modified network services. It does not require revenue sharing with the Auri maintainer, and it does not permit withholding the corresponding source merely because the service is paid. User data and deployment secrets should remain private.
