from __future__ import annotations

import json
from collections import Counter

from app.auth.store import AuthStore
from app.config import Settings
from app.health.sleep_score_service import SleepScoreService
from app.health.sleep_score_store import SleepScoreStore
from app.health.store import HealthStore
from app.services.timezone_store import TimezoneResolver, UserTimezoneStore


def backfill_all(settings: Settings) -> dict:
    """Recompute all stored sleep history for every registered user."""

    auth_store = AuthStore(settings.data_dir / "auth")
    health_store = HealthStore(settings.data_dir / "health")
    score_store = SleepScoreStore(health_store.db_path)
    score_service = SleepScoreService(health_store, score_store)
    timezone_resolver = TimezoneResolver(
        UserTimezoneStore(settings.data_dir / "auth" / "user_timezones.json"),
        default_timezone=settings.default_timezone,
    )

    users: list[dict] = []
    errors: list[dict] = []
    total_scores = 0
    for user_id in auth_store.list_user_ids():
        try:
            timezone = timezone_resolver.get(user_id)
            recomputed = score_service.recompute_all(user_id, timezone)
            scores = score_service.get_scores(user_id, "0001-01-01", "9999-12-31")
            total_scores += len(scores)
            states = Counter(score.calibration.state for score in scores)
            recovery_states = Counter(score.recovery.status for score in scores)
            users.append(
                {
                    "user_id": user_id,
                    "timezone": timezone,
                    "recomputed": recomputed,
                    "stored_scores": len(scores),
                    "calibration_states": dict(sorted(states.items())),
                    "recovery_states": dict(sorted(recovery_states.items())),
                }
            )
        except Exception as exc:
            errors.append({"user_id": user_id, "error": f"{type(exc).__name__}: {exc}"})

    return {
        "users": users,
        "user_count": len(users),
        "total_scores": total_scores,
        "errors": errors,
    }


def main() -> int:
    result = backfill_all(Settings())
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
