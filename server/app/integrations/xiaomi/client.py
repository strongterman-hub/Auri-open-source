# Adapted from shkyyy18/mi-bridge (AGPL-3.0-only); modified for Auri, 2026.
# Upstream MIT notices are preserved in LICENSES/mi-bridge-NOTICE.txt.
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import random
import struct
from collections import defaultdict
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode, urlparse

import httpx

from app.integrations.xiaomi.models import (
    AbnormalHeartBeatEvent,
    BodyMeasurement,
    DailyActivity,
    HeartRateSample,
    SleepSession,
    SleepStage,
    SpO2Sample,
    StressSample,
    Workout,
)

LOGIN_PREFIX = b"&&&START&&&"
KNOWN_REGIONS = ["ru", "cn", "de", "i2", "sg", "us"]
AUTH_ERROR_MARKERS = (
    "authentication failed",
    "invalid credential",
    "invalid pass token",
    "invalid passtoken",
    "login required",
    "not logged in",
    "session expired",
    "unauthorized",
)
logger = logging.getLogger(__name__)


class XiaomiCloudAuthenticationError(RuntimeError):
    """The Xiaomi cloud session is no longer authenticated."""


def _is_authentication_error(code: Any, message: str) -> bool:
    normalized = message.casefold()
    return code in {401, 403, -6, -10001} or any(
        marker in normalized for marker in AUTH_ERROR_MARKERS
    )


def _read_login_payload(text: str) -> dict:
    payload = text.encode()
    if not payload.startswith(LOGIN_PREFIX):
        raise RuntimeError("unexpected Xiaomi login response")
    return json.loads(payload[len(LOGIN_PREFIX) :].decode())


# The login response carries a redirect ``location`` chosen by the server. Only
# follow it to Xiaomi-owned HTTPS hosts; anything else is an SSRF/credential-leak
# vector.
_LOGIN_REDIRECT_HOSTS = ("xiaomi.com", "mi.com")


def _is_allowed_login_redirect(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return host in _LOGIN_REDIRECT_HOSTS or host.endswith(
        tuple(f".{domain}" for domain in _LOGIN_REDIRECT_HOSTS)
    )


def _rc4_crypt(key: bytes, payload: bytes) -> bytes:
    s = list(range(256))
    j = 0
    key_len = len(key)
    for i in range(256):
        j = (j + s[i] + key[i % key_len]) % 256
        s[i], s[j] = s[j], s[i]
    i = 0
    j = 0

    def next_byte() -> int:
        nonlocal i, j
        i = (i + 1) % 256
        j = (j + s[i]) % 256
        s[i], s[j] = s[j], s[i]
        return s[(s[i] + s[j]) % 256]

    for _ in range(1024):
        next_byte()

    output = bytearray()
    for value in payload:
        output.append(value ^ next_byte())
    return bytes(output)


def _gen_nonce() -> bytes:
    raw = bytearray(os.urandom(8))
    raw.extend(struct.pack(">I", int(datetime.now().timestamp() // 60)))
    return bytes(raw)


def _gen_signed_nonce(ssecurity: bytes, nonce: bytes) -> bytes:
    return hashlib.sha256(ssecurity + nonce).digest()


def _gen_signature(method: str, path: str, values: dict[str, str], signed_nonce: bytes) -> str:
    base = method + "&" + path + "&data=" + values["data"]
    if "rc4_hash__" in values:
        base += "&rc4_hash__=" + values["rc4_hash__"]
    base += "&" + base64.b64encode(signed_nonce).decode()
    return base64.b64encode(hashlib.sha1(base.encode()).digest()).decode()


class XiaomiCloudClient:
    """Async client for the Xiaomi Health Cloud (``hlth.io.mi.com``)."""

    def __init__(
        self, user_id: str | None = None, pass_token: str | None = None, region: str = "cn"
    ) -> None:
        self.user_id = user_id
        self.pass_token = pass_token
        self.region = region
        self._cookies = ""
        self._ssecurity = b""
        self._client: httpx.AsyncClient | None = None
        self._connected = False
        self._available_types: list[str] = []
        self._connect_lock = asyncio.Lock()
        self.last_error: str | None = None
        self.max_pages = 200
        self.request_retries = 3
        self.http_timeout = 20.0

    async def connect(self) -> bool:
        async with self._connect_lock:
            if not self.user_id or not self.pass_token:
                self.last_error = "Missing Mi Fitness credentials"
                self._connected = False
                return False
            await self._close_client()
            # The Xiaomi cloud is a domestic service: always connect directly.
            # trust_env=False makes httpx ignore the system proxy, which can hang
            # when a proxy process exits but its settings remain.
            self._client = httpx.AsyncClient(
                timeout=self.http_timeout, follow_redirects=False, trust_env=False
            )
            try:
                await self._login_with_token(self.user_id, self.pass_token)
                if not self.region:
                    self.region = await self._discover_region("cn")
                self._available_types = await self._discover_data_types()
                self._connected = True
                self.last_error = None
                return True
            except Exception as exc:
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("Xiaomi cloud connection failed: %s", self.last_error)
                self._connected = False
                await self._close_client()
                return False

    async def close(self) -> None:
        await self._close_client()
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    def get_available_data_types(self) -> list[str]:
        return self._available_types.copy()

    async def _close_client(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    async def _login_with_token(self, user_id: str, pass_token: str) -> None:
        if not self._client:
            raise RuntimeError("client not initialized")

        response = await self._client.get(
            "https://account.xiaomi.com/pass/serviceLogin?_json=true&sid=miothealth",
            headers={"Cookie": f"userId={user_id}; passToken={pass_token}"},
        )
        response.raise_for_status()
        payload = _read_login_payload(response.text)
        self.pass_token = payload["passToken"]
        self.user_id = str(payload["userId"])
        self._ssecurity = base64.b64decode(payload["ssecurity"])

        location = payload["location"]
        if not _is_allowed_login_redirect(location):
            raise RuntimeError(f"Refusing untrusted login redirect location: {location!r}")
        redirect = await self._client.get(location)
        redirect.raise_for_status()
        cookie_parts = [value.split(";", 1)[0] for value in redirect.headers.get_list("set-cookie")]
        self._cookies = "; ".join(cookie_parts)

    async def _request(self, base_url: str, api_path: str, payload: dict) -> dict:
        if not self._client:
            raise RuntimeError("client not initialized")

        last_error: Exception | None = None
        for attempt in range(self.request_retries):
            try:
                form = {"data": json.dumps(payload, separators=(",", ":"))}
                nonce = _gen_nonce()
                signed_nonce = _gen_signed_nonce(self._ssecurity, nonce)
                form["rc4_hash__"] = _gen_signature("POST", api_path, form, signed_nonce)

                encrypted: dict[str, str] = {}
                for key, value in form.items():
                    encrypted[key] = base64.b64encode(
                        _rc4_crypt(signed_nonce, value.encode())
                    ).decode()

                encrypted["signature"] = _gen_signature("POST", api_path, encrypted, signed_nonce)
                encrypted["_nonce"] = base64.b64encode(nonce).decode()

                response = await self._client.post(
                    base_url + api_path,
                    headers={
                        "Cookie": self._cookies,
                        "Content-Type": "application/x-www-form-urlencoded",
                    },
                    content=urlencode(encrypted),
                )
                response.raise_for_status()
                plaintext = _rc4_crypt(signed_nonce, base64.b64decode(response.text))
                body = json.loads(plaintext)
                if body.get("code") != 0:
                    message = str(body.get("message", "unknown mi fitness error"))
                    if _is_authentication_error(body.get("code"), message):
                        raise XiaomiCloudAuthenticationError(message)
                    raise RuntimeError(message)
                return body.get("result", {})
            except Exception as exc:
                last_error = exc
                retryable = isinstance(exc, (httpx.TimeoutException, httpx.NetworkError))
                authentication_error = isinstance(exc, XiaomiCloudAuthenticationError)
                if isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code
                    authentication_error = status in (401, 403)
                    if not authentication_error:
                        retryable = status == 429 or status >= 500
                if authentication_error:
                    self._connected = False
                    if attempt < self.request_retries - 1 and self.user_id and self.pass_token:
                        try:
                            await self._login_with_token(self.user_id, self.pass_token)
                            self._connected = True
                            retryable = True
                        except Exception as login_exc:
                            self.last_error = f"Authentication refresh failed: {login_exc}"
                            retryable = False
                    else:
                        retryable = False
                if attempt == self.request_retries - 1 or not retryable:
                    break
                await asyncio.sleep(min(4.0, 0.5 * (2**attempt)) + random.random() * 0.1)
        if isinstance(last_error, XiaomiCloudAuthenticationError):
            raise last_error
        raise RuntimeError(f"Mi Fitness request failed: {last_error}") from last_error

    def _request_timezone(self, region: str | None = None) -> timezone:
        region_name = self.region if region is None else region
        if region_name in ("", "cn"):
            return timezone(timedelta(hours=8))
        return UTC

    def _date_range_to_timestamps(
        self, start_date: str, end_date: str, region: str | None = None
    ) -> tuple[int, int]:
        tz = self._request_timezone(region)
        start_dt = datetime.fromisoformat(start_date).replace(tzinfo=tz)
        end_dt = datetime.fromisoformat(end_date + "T23:59:59").replace(tzinfo=tz)
        return int(start_dt.timestamp()), int(end_dt.timestamp())

    async def _fetch_key(
        self, key: str, start_date: str, end_date: str, region: str | None = None
    ) -> list[dict]:
        region_name = region or self.region
        base_url = (
            "https://hlth.io.mi.com"
            if region_name in ("", "cn")
            else f"https://{region_name}.hlth.io.mi.com"
        )
        start_time, end_time = self._date_range_to_timestamps(start_date, end_date, region_name)
        next_key = None
        items: list[dict] = []

        seen_keys: set[str] = set()
        page = 0
        while True:
            page += 1
            if page > self.max_pages:
                raise RuntimeError("Mi Fitness pagination exceeded safety limit")
            payload = {
                "start_time": start_time,
                "end_time": end_time,
                "key": key,
            }
            if next_key:
                payload["next_key"] = next_key

            result = await self._request(base_url, "/app/v1/data/get_fitness_data_by_time", payload)
            items.extend(result.get("data_list", []))
            if not result.get("has_more") or not result.get("next_key"):
                break
            candidate = str(result.get("next_key"))
            if candidate in seen_keys:
                raise RuntimeError("Mi Fitness pagination cursor loop detected")
            seen_keys.add(candidate)
            next_key = candidate

        return items

    async def _discover_region(self, preferred_region: str) -> str:
        candidates = [preferred_region] + [
            region for region in KNOWN_REGIONS if region != preferred_region
        ]
        for region in candidates:
            for key in ("weight", "steps", "heart_rate"):
                try:
                    result = await self._fetch_key(key, "2025-04-01", "2025-05-31", region=region)
                    if result:
                        return region
                except Exception:
                    continue
        return preferred_region

    async def _discover_data_types(self) -> list[str]:
        # The cloud has no reliable capability-discovery endpoint. Return every
        # type this adapter supports; a type with no records simply syncs zero rows.
        return [
            "daily_activity",
            "heart_rate",
            "body_measurements",
            "sleep",
            "workouts",
            "spo2",
            "stress",
            "abnormal_heart_beat",
        ]

    def _record_datetime(self, item: dict) -> datetime:
        timestamp = int(item.get("time", 0))
        zone_offset = int(item.get("zone_offset", 0) or 0)
        tz = timezone(timedelta(seconds=zone_offset))
        return datetime.fromtimestamp(timestamp, tz=tz)

    def _parse_value(self, item: dict) -> dict[str, Any]:
        raw = item.get("value", "{}")
        if isinstance(raw, dict):
            return raw
        return json.loads(raw)

    def _timestamp_to_datetime(self, timestamp: Any, zone_offset: int = 0) -> datetime:
        tz = timezone(timedelta(seconds=int(zone_offset or 0)))
        return datetime.fromtimestamp(int(timestamp), tz=tz)

    async def _fetch_sport_records_by_time(
        self, start_date: str, end_date: str, region: str | None = None
    ) -> list[dict]:
        region_name = region or self.region
        base_url = (
            "https://hlth.io.mi.com"
            if region_name in ("", "cn")
            else f"https://{region_name}.hlth.io.mi.com"
        )
        start_time, end_time = self._date_range_to_timestamps(start_date, end_date, region_name)
        next_key = None
        items: list[dict] = []

        seen_keys: set[str] = set()
        page = 0
        while True:
            page += 1
            if page > self.max_pages:
                raise RuntimeError("Mi Fitness sport pagination exceeded safety limit")
            payload: dict[str, Any] = {
                "start_time": start_time,
                "end_time": end_time,
                "limit": 50,
            }
            if next_key:
                payload["next_key"] = next_key

            result = await self._request(
                base_url, "/app/v1/data/get_sport_records_by_time", payload
            )
            items.extend(result.get("sport_records", []))
            if not result.get("has_more") or not result.get("next_key"):
                break
            candidate = str(result.get("next_key"))
            if candidate in seen_keys:
                raise RuntimeError("Mi Fitness sport pagination cursor loop detected")
            seen_keys.add(candidate)
            next_key = candidate

        return items

    def _sleep_stage_name(self, state: Any) -> str:
        # Verified against the official sleep_*_duration fields in real Mi
        # Fitness cloud payloads: 2 = deep, 3 = light, 4 = rem, 5 = awake.
        # State 1 is retained as a legacy fallback for older firmware.
        mapping = {
            1: "awake",
            2: "deep",
            3: "light",
            4: "rem",
            5: "awake",
        }
        try:
            return mapping.get(int(state), "light")
        except Exception:
            return "light"

    def _optional_float(self, value: Any) -> float | None:
        if value is None:
            return None
        parsed = float(value)
        return None if parsed == 0 else parsed

    def _optional_int(self, value: Any) -> int | None:
        if value is None:
            return None
        parsed = int(float(value))
        return None if parsed == 0 else parsed

    def _sleep_hrv_rmssd(self, payload: dict[str, Any]) -> tuple[float | None, str | None]:
        """Read only fields that explicitly describe sleep HRV/RMSSD.

        Xiaomi payloads vary by device and firmware. A generic heart-rate value
        is never accepted here because averaged BPM cannot be converted to HRV.
        """
        for key in (
            "sleep_hrv_rmssd",
            "sleep_hrv",
            "avg_sleep_hrv",
            "avg_hrv",
            "hrv_rmssd",
            "rmssd",
        ):
            raw = payload.get(key)
            if isinstance(raw, dict):
                raw = raw.get("rmssd") or raw.get("avg") or raw.get("value")
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if 5.0 <= value <= 300.0:
                return value, key
        return None, None

    def _select_primary_sid(
        self,
        records: list[dict],
        *,
        prefer_generated: bool = False,
    ) -> str | None:
        """Pick one data source when the cloud returns the same key from
        multiple ``sid`` values (for example a wearable device plus a
        server-generated ``hlth.gen_*`` source). Summing both double-counts
        the same activity.

        Xiaomi's generated source is the app-facing aggregate for steps and
        distance, while device calories currently match the wearable's active
        calorie total more closely. Callers therefore choose the preferred
        source class per metric; the most recently updated source wins within
        that class.
        """
        by_sid: dict[str, list[dict]] = defaultdict(list)
        for item in records:
            by_sid[str(item.get("sid", "") or "")].append(item)
        if not by_sid:
            return None

        def rank(sid: str) -> tuple[int, int]:
            generated = sid.startswith("hlth.gen_")
            preferred = generated if prefer_generated else not generated
            latest = max(
                (int(item.get("update_time", 0) or 0) for item in by_sid[sid]),
                default=0,
            )
            return (0 if preferred else 1, -latest)

        return min(by_sid, key=rank)

    def _source_kind(self, sid: str | None) -> str | None:
        if not sid:
            return None
        return "xiaomi_aggregate" if sid.startswith("hlth.gen_") else "xiaomi_device"

    def _source_updated_at(self, records: list[dict], sid: str | None) -> int:
        """Return the selected source's freshest cloud update as epoch millis."""
        if not sid:
            return 0
        latest_seconds = max(
            (
                int(item.get("update_time", 0) or 0)
                for item in records
                if str(item.get("sid", "") or "") == sid
            ),
            default=0,
        )
        return latest_seconds * 1000

    async def iter_daily_activity(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> AsyncIterator[DailyActivity]:
        if not self.is_connected() or not start_date or not end_date:
            return
            yield  # pragma: no cover - keeps this an async generator

        records = await self._fetch_key("steps", start_date, end_date)
        daily: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "steps": 0,
                "distance_m": 0.0,
                "active_kcal": 0.0,
                "steps_source": None,
                "distance_source": None,
                "calories_source": None,
                "steps_source_updated_at": 0,
                "distance_source_updated_at": 0,
                "calories_source_updated_at": 0,
            }
        )
        steps_by_day: dict[str, list[dict]] = defaultdict(list)
        for item in records:
            date_str = self._record_datetime(item).strftime("%Y-%m-%d")
            steps_by_day[date_str].append(item)

        for date_str, day_items in steps_by_day.items():
            primary_sid = self._select_primary_sid(day_items, prefer_generated=True)
            source_kind = self._source_kind(primary_sid)
            source_updated_at = self._source_updated_at(day_items, primary_sid)
            daily[date_str]["steps_source"] = source_kind
            daily[date_str]["distance_source"] = source_kind
            daily[date_str]["calories_source"] = source_kind
            daily[date_str]["steps_source_updated_at"] = source_updated_at
            daily[date_str]["distance_source_updated_at"] = source_updated_at
            daily[date_str]["calories_source_updated_at"] = source_updated_at
            for item in day_items:
                if str(item.get("sid", "") or "") != primary_sid:
                    continue
                payload = self._parse_value(item)
                daily[date_str]["steps"] += int(payload.get("steps", 0))
                daily[date_str]["distance_m"] += float(payload.get("distance", 0))
                daily[date_str]["active_kcal"] += float(payload.get("calories", 0))

        calorie_records = await self._fetch_key("calories", start_date, end_date)
        calories_by_day: dict[str, list[dict]] = defaultdict(list)
        for item in calorie_records:
            date_str = self._record_datetime(item).strftime("%Y-%m-%d")
            calories_by_day[date_str].append(item)

        calorie_totals: dict[str, float] = {}
        for date_str, day_items in calories_by_day.items():
            primary_sid = self._select_primary_sid(day_items)
            daily[date_str]["calories_source"] = self._source_kind(primary_sid)
            daily[date_str]["calories_source_updated_at"] = self._source_updated_at(
                day_items, primary_sid
            )
            total = 0.0
            for item in day_items:
                if str(item.get("sid", "") or "") != primary_sid:
                    continue
                payload = self._parse_value(item)
                total += float(payload.get("calories", 0))
            calorie_totals[date_str] = total

        for date_str, total in calorie_totals.items():
            daily[date_str]["active_kcal"] = total

        for date_str, values in sorted(daily.items()):
            yield DailyActivity(
                date=date_str,
                steps=int(values["steps"]),
                distance_m=float(values["distance_m"]),
                active_kcal=float(values["active_kcal"]),
                steps_source=values["steps_source"],
                distance_source=values["distance_source"],
                calories_source=values["calories_source"],
                steps_source_updated_at=int(values["steps_source_updated_at"]),
                distance_source_updated_at=int(values["distance_source_updated_at"]),
                calories_source_updated_at=int(values["calories_source_updated_at"]),
            )

    async def iter_sleep_sessions(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> AsyncIterator[SleepSession]:
        if not self.is_connected() or not start_date or not end_date:
            return
            yield  # pragma: no cover

        records = await self._fetch_key("sleep", start_date, end_date)
        for item in records:
            payload = self._parse_value(item)
            zone_offset = int(item.get("zone_offset", 0) or 0)

            stages: list[SleepStage] = []
            awake_minutes = 0
            for segment in payload.get("items", []) or []:
                try:
                    seg_start = int(segment.get("start_time", 0))
                    seg_end = int(segment.get("end_time", 0))
                    minutes = max(0, (seg_end - seg_start) // 60)
                    if minutes:
                        stage_name = self._sleep_stage_name(segment.get("state"))
                        stages.append(
                            SleepStage(
                                stage=stage_name,
                                start_time=self._timestamp_to_datetime(seg_start, zone_offset),
                                end_time=self._timestamp_to_datetime(seg_end, zone_offset),
                                minutes=minutes,
                            )
                        )
                        if stage_name == "awake":
                            awake_minutes += minutes
                except Exception:
                    continue

            # Prefer the cloud's own awake total so the session matches what
            # the Mi Fitness app displays, even when minute-bucket rounding in
            # ``items`` would undercount a short awake segment.
            official_awake = payload.get("sleep_awake_duration")
            if official_awake is not None:
                awake_minutes = int(float(official_awake))

            start_times = [s.start_time for s in stages if s.start_time is not None]
            end_times = [s.end_time for s in stages if s.end_time is not None]
            if start_times and end_times:
                start_at = min(start_times)
                end_at = max(end_times)
                duration_minutes = max(0, int((end_at - start_at).total_seconds() // 60))
            else:
                sleep_start = (
                    payload.get("bedtime")
                    or payload.get("device_bedtime")
                    or payload.get("bed_timestamp")
                )
                sleep_end = (
                    payload.get("wake_up_time")
                    or payload.get("device_wake_up_time")
                    or payload.get("out_bed_timestamp")
                    or item.get("time")
                )
                if not sleep_start or not sleep_end:
                    continue
                start_at = self._timestamp_to_datetime(sleep_start, zone_offset)
                end_at = self._timestamp_to_datetime(sleep_end, zone_offset)
                duration_minutes = max(0, (int(sleep_end) - int(sleep_start)) // 60)

            asleep_minutes = max(0, duration_minutes - awake_minutes)

            hrv_rmssd, hrv_source = self._sleep_hrv_rmssd(payload)
            yield SleepSession(
                start_at=start_at,
                end_at=end_at,
                duration_minutes=duration_minutes,
                time_asleep_minutes=asleep_minutes,
                time_awake_minutes=awake_minutes,
                sleep_score=self._optional_int(payload.get("score") or payload.get("sleep_score")),
                sleep_hrv_rmssd_ms=hrv_rmssd,
                sleep_hrv_source=hrv_source,
                is_nap=bool(payload.get("is_nap", False)),
                stages=stages,
            )

    async def iter_workouts(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> AsyncIterator[Workout]:
        if not self.is_connected() or not start_date or not end_date:
            return
            yield  # pragma: no cover

        records = await self._fetch_sport_records_by_time(start_date, end_date)
        for item in records:
            payload = self._parse_value(item)
            zone_offset = int(item.get("zone_offset", 0) or 0)
            start_ts = payload.get("start_time") or item.get("time")
            end_ts = payload.get("end_time")
            duration_seconds = int(payload.get("duration", 0) or 0)
            if not end_ts and start_ts:
                end_ts = int(start_ts) + duration_seconds
            if not start_ts or not end_ts:
                continue

            start_at = self._timestamp_to_datetime(start_ts, zone_offset)
            end_at = self._timestamp_to_datetime(end_ts, zone_offset)
            duration_minutes = max(0, int(duration_seconds // 60))
            if duration_minutes == 0:
                duration_minutes = max(0, (int(end_ts) - int(start_ts)) // 60)

            yield Workout(
                start_at=start_at,
                end_at=end_at,
                duration_minutes=duration_minutes,
                activity_type=str(
                    item.get("category")
                    or item.get("key")
                    or payload.get("sport_type")
                    or "workout"
                ),
                distance_m=self._optional_float(payload.get("distance")),
                calories_kcal=self._optional_float(
                    payload.get("calories") or payload.get("total_cal")
                ),
                avg_heart_rate_bpm=self._optional_int(payload.get("avg_hrm")),
                max_heart_rate_bpm=self._optional_int(payload.get("max_hrm")),
                avg_pace_sec_per_km=self._optional_float(payload.get("avg_pace")),
                max_pace_sec_per_km=self._optional_float(payload.get("max_pace")),
                total_steps=self._optional_int(payload.get("steps") or payload.get("total_steps")),
            )

    async def iter_body_measurements(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> AsyncIterator[BodyMeasurement]:
        if not self.is_connected() or not start_date or not end_date:
            return
            yield  # pragma: no cover

        records = await self._fetch_key("weight", start_date, end_date)
        for item in records:
            payload = self._parse_value(item)
            measured_at = self._record_datetime(item)
            weight = payload.get("weight", 0)
            if not weight:
                continue
            yield BodyMeasurement(
                timestamp=measured_at,
                weight_kg=float(weight),
                bmi=self._optional_float(payload.get("bmi")),
                body_fat_pct=self._optional_float(payload.get("body_fat_rate")),
                muscle_mass_kg=self._optional_float(payload.get("muscle_rate")),
                water_pct=self._optional_float(payload.get("moisture_rate")),
                bone_mass_kg=self._optional_float(payload.get("bone_mass")),
                visceral_fat_score=self._optional_int(payload.get("visceral_fat")),
                basal_metabolism_kcal=self._optional_int(payload.get("basal_metabolism")),
                metabolic_age=self._optional_int(payload.get("body_age")),
            )

    async def iter_heart_rate(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> AsyncIterator[HeartRateSample]:
        if not self.is_connected() or not start_date or not end_date:
            return
            yield  # pragma: no cover

        records = await self._fetch_key("heart_rate", start_date, end_date)
        for item in records:
            payload = self._parse_value(item)
            sample_type = "passive" if int(payload.get("type", 0)) == 0 else "active"
            yield HeartRateSample(
                timestamp=self._record_datetime(item),
                bpm=int(payload.get("bpm", 0)),
                sample_type=sample_type,
            )

        resting_records = await self._fetch_key("resting_heart_rate", start_date, end_date)
        for item in resting_records:
            payload = self._parse_value(item)
            timestamp = payload.get("date_time") or item.get("time")
            yield HeartRateSample(
                timestamp=self._timestamp_to_datetime(
                    timestamp or item.get("time"), int(item.get("zone_offset", 0) or 0)
                ),
                bpm=int(payload.get("bpm", 0)),
                sample_type="resting",
            )

    def _stress_level(self, score: int) -> str:
        if score < 30:
            return "low"
        if score < 60:
            return "medium"
        return "high"

    async def iter_spo2(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> AsyncIterator[SpO2Sample]:
        if not self.is_connected() or not start_date or not end_date:
            return
            yield  # pragma: no cover

        records = await self._fetch_key("spo2", start_date, end_date)
        for item in records:
            payload = self._parse_value(item)
            timestamp = payload.get("time") or item.get("time")
            spo2 = payload.get("spo2") or payload.get("value")
            if timestamp is None or spo2 is None:
                continue
            yield SpO2Sample(
                timestamp=self._timestamp_to_datetime(
                    timestamp, int(item.get("zone_offset", 0) or 0)
                ),
                spo2_pct=int(float(spo2)),
            )

    async def iter_stress(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> AsyncIterator[StressSample]:
        if not self.is_connected() or not start_date or not end_date:
            return
            yield  # pragma: no cover

        records = await self._fetch_key("stress", start_date, end_date)
        for item in records:
            payload = self._parse_value(item)
            timestamp = payload.get("time") or item.get("time")
            stress = payload.get("stress") or payload.get("score") or payload.get("value")
            if timestamp is None or stress is None:
                continue
            score = int(float(stress))
            yield StressSample(
                timestamp=self._timestamp_to_datetime(
                    timestamp, int(item.get("zone_offset", 0) or 0)
                ),
                stress_score=score,
                level=self._stress_level(score),
            )

    async def iter_abnormal_heart_beat(
        self, start_date: str | None = None, end_date: str | None = None
    ) -> AsyncIterator[AbnormalHeartBeatEvent]:
        if not self.is_connected() or not start_date or not end_date:
            return
            yield  # pragma: no cover

        records = await self._fetch_key("abnormal_heart_beat", start_date, end_date)
        for item in records:
            payload = self._parse_value(item)
            zone_offset = int(item.get("zone_offset", 0) or 0)
            start_ts = payload.get("start_time") or item.get("time")
            end_ts = payload.get("end_time") or start_ts
            if start_ts is None:
                continue
            start_at = self._timestamp_to_datetime(start_ts, zone_offset)
            end_at = self._timestamp_to_datetime(end_ts, zone_offset)
            yield AbnormalHeartBeatEvent(
                start_at=start_at,
                end_at=end_at,
                duration_seconds=max(0, int(end_ts) - int(start_ts)),
            )
