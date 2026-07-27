#!/usr/bin/env python3
"""Validation profiles for resource-layer tests and smoke scenarios.

Ownership: define bounded validation modes for resource acquisition checks.
Non-goals: executing resource requests, calling network tools, or changing
global proxy/package-manager behavior.
State behavior: pure profile lookup; no filesystem or network side effects.
Caller context: resource broker, scheduler, scenario smoke, and tests use this
module to keep quick checks deterministic while preserving live/full coverage.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


VALIDATION_PROFILES = ("quick", "smoke", "full", "live")


@dataclass(frozen=True)
class ResourceValidationProfile:
    name: str
    live_network: bool
    live_owner_mcp: bool
    live_package_index: bool
    use_fixture_package_metadata: bool
    network_plan_cache_ttl_seconds: int
    package_metadata_cache_ttl_seconds: int
    owner_result_cache_ttl_seconds: int
    max_owner_timeout_seconds: int
    default_retry_budget: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PROFILES: dict[str, ResourceValidationProfile] = {
    "quick": ResourceValidationProfile(
        name="quick",
        live_network=False,
        live_owner_mcp=False,
        live_package_index=False,
        use_fixture_package_metadata=True,
        network_plan_cache_ttl_seconds=600,
        package_metadata_cache_ttl_seconds=86_400,
        owner_result_cache_ttl_seconds=600,
        max_owner_timeout_seconds=8,
        default_retry_budget=0,
    ),
    "smoke": ResourceValidationProfile(
        name="smoke",
        live_network=True,
        live_owner_mcp=True,
        live_package_index=False,
        use_fixture_package_metadata=True,
        network_plan_cache_ttl_seconds=600,
        package_metadata_cache_ttl_seconds=21_600,
        owner_result_cache_ttl_seconds=600,
        max_owner_timeout_seconds=30,
        default_retry_budget=1,
    ),
    "full": ResourceValidationProfile(
        name="full",
        live_network=True,
        live_owner_mcp=True,
        live_package_index=True,
        use_fixture_package_metadata=False,
        network_plan_cache_ttl_seconds=300,
        package_metadata_cache_ttl_seconds=21_600,
        owner_result_cache_ttl_seconds=300,
        max_owner_timeout_seconds=45,
        default_retry_budget=2,
    ),
    "live": ResourceValidationProfile(
        name="live",
        live_network=True,
        live_owner_mcp=True,
        live_package_index=True,
        use_fixture_package_metadata=False,
        network_plan_cache_ttl_seconds=60,
        package_metadata_cache_ttl_seconds=3_600,
        owner_result_cache_ttl_seconds=60,
        max_owner_timeout_seconds=45,
        default_retry_budget=1,
    ),
}


def normalize_profile(value: str | None) -> str:
    name = str(value or "").strip().lower()
    return name if name in PROFILES else "full"


def profile_from(value: str | None) -> ResourceValidationProfile:
    return PROFILES[normalize_profile(value)]


def metadata_profile(metadata: dict[str, Any] | None) -> ResourceValidationProfile:
    data = metadata if isinstance(metadata, dict) else {}
    return profile_from(str(data.get("validation_profile") or data.get("resource_validation_profile") or "full"))


def validate() -> dict[str, Any]:
    return {
        "schema": "resource_validation_profile.validate.v1",
        "ok": set(PROFILES) == set(VALIDATION_PROFILES)
        and not PROFILES["quick"].live_package_index
        and PROFILES["full"].live_package_index,
        "profiles": {key: value.to_dict() for key, value in PROFILES.items()},
        "writes_files": False,
        "writes_remote_state": False,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(validate(), ensure_ascii=False, indent=2, sort_keys=True))
