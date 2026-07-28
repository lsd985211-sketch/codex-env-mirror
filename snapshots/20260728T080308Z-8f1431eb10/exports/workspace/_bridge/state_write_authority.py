#!/usr/bin/env python3
"""Govern high-risk state domains and coordinate authorized writers.

Ownership: one machine-readable authority contract, cross-platform writer
leases, monotonic generations, stability probes, and the mirror pre-publish
state-convergence gate.
Non-goals: choosing business desired state, replacing domain reconcilers,
copying host state into Work Git, or publishing the recovery mirror.
State behavior: read-only by default. Authorized writer leases create only
short-lived coordination metadata beside the target state.
Caller context: config projection/repair writers, environment selection,
system-state information entry, membership validation, and mirror publish.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import time
import tomllib
import uuid
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
BRIDGE = ROOT / "_bridge"
CONTRACT_PATH = BRIDGE / "contracts" / "state_write_authorities.json"
DEFAULT_LEASE_SECONDS = 600.0
DEFAULT_STABILITY_SECONDS = 12.0
PUBLICATION_BARRIER_LEASE_SECONDS = 3600.0


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_contract(path: Path = CONTRACT_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"schema": "state_write_authority.contract.v1", "ok": False, "reason": "contract_unreadable", "error": type(exc).__name__}
    return payload if isinstance(payload, dict) else {"schema": "state_write_authority.contract.v1", "ok": False, "reason": "contract_not_object"}


def contract_domains(path: Path = CONTRACT_PATH) -> list[dict[str, Any]]:
    return [dict(item) for item in read_contract(path).get("domains", []) if isinstance(item, dict)]


def domain_contract(domain_id: str, path: Path = CONTRACT_PATH) -> dict[str, Any]:
    return next((item for item in contract_domains(path) if item.get("domain_id") == domain_id), {})


@dataclass(frozen=True)
class AuthorityBinding:
    """A field/path-level writer assignment inside a state authority domain."""

    domain_id: str
    binding_id: str
    target: str
    path: tuple[str, ...]
    active_writer: str
    shadow_writer: str
    transfer_mode: str

    @property
    def key(self) -> str:
        return f"{self.domain_id}:{self.target}:{'.'.join(self.path)}"


def authority_bindings(contract_path: Path = CONTRACT_PATH) -> list[AuthorityBinding]:
    bindings: list[AuthorityBinding] = []
    for domain in contract_domains(contract_path):
        domain_id = str(domain.get("domain_id") or "")
        transfer_mode = str(domain.get("binding_transfer_mode") or "shadow_only")
        for item in domain.get("authority_bindings", []):
            if not isinstance(item, dict):
                continue
            path = item.get("path")
            bindings.append(
                AuthorityBinding(
                    domain_id=domain_id,
                    binding_id=str(item.get("binding_id") or ""),
                    target=str(item.get("target") or ""),
                    path=tuple(str(part) for part in path) if isinstance(path, list) else (),
                    active_writer=str(item.get("active_writer") or ""),
                    shadow_writer=str(item.get("shadow_writer") or ""),
                    transfer_mode=transfer_mode,
                )
            )
    return bindings


def authority_binding(domain_id: str, binding_id: str, contract_path: Path = CONTRACT_PATH) -> AuthorityBinding | None:
    return next(
        (
            binding
            for binding in authority_bindings(contract_path)
            if binding.domain_id == domain_id and binding.binding_id == binding_id
        ),
        None,
    )


def coordination_root(domain_id: str, *, state_root: Path | None = None) -> Path:
    if state_root is not None:
        return state_root / "state" / "runtime" / "state_write_authority"
    if domain_id == "codex_config":
        return Path.home() / ".codex" / "state" / "runtime" / "state_write_authority"
    return BRIDGE / "runtime" / "state_write_authority"


def codex_config_coordination_root(host_config: Path | None = None) -> Path:
    """Return the single host-visible root shared by Windows and WSL writers."""

    if host_config is not None:
        return host_config.parent
    if os.name == "nt":
        return Path.home() / ".codex"
    return Path("/mnt/c/Users/45543/.codex")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _binding_state_path(root: Path, binding: AuthorityBinding) -> Path:
    digest = sha256_bytes(binding.key.encode("utf-8"))[:20]
    return root / "bindings" / f"{digest}.json"


def _binding_receipt_path(root: Path, binding: AuthorityBinding, action: str, generation: int) -> Path:
    digest = sha256_bytes(binding.key.encode("utf-8"))[:20]
    return root / "binding-receipts" / f"{digest}-{generation}-{action}.json"


def _default_binding_state(binding: AuthorityBinding) -> dict[str, Any]:
    return {
        "schema": "state_write_authority.binding_state.v1",
        "binding_key": binding.key,
        "binding_id": binding.binding_id,
        "domain_id": binding.domain_id,
        "target": binding.target,
        "path": list(binding.path),
        "active_writer": binding.active_writer,
        "frozen_writers": [],
        "binding_generation": 0,
        "status": "shadow",
        "shadow": {},
    }


def binding_state(
    domain_id: str,
    binding_id: str,
    *,
    state_root: Path | None = None,
    contract_path: Path = CONTRACT_PATH,
) -> dict[str, Any]:
    binding = authority_binding(domain_id, binding_id, contract_path)
    if binding is None:
        return {"schema": "state_write_authority.binding_state.v1", "ok": False, "reason": "binding_not_declared"}
    root = coordination_root(domain_id, state_root=state_root)
    state = _read_json(_binding_state_path(root, binding)) or _default_binding_state(binding)
    return {"ok": True, **state}


@dataclass
class StateWriteLease(AbstractContextManager["StateWriteLease"]):
    domain_id: str
    writer_id: str
    root: Path
    lock_dir: Path
    token: str
    generation: int
    expires_at_epoch: float
    released: bool = False
    generation_advanced: bool = True

    def owner_path(self) -> Path:
        return self.lock_dir / "owner.json"

    @staticmethod
    def _owner_generation(owner: dict[str, Any]) -> int:
        value = owner.get("generation")
        return int(value) if value is not None else -1

    def assert_current(self) -> None:
        owner = _read_json(self.owner_path())
        if owner.get("token") != self.token or self._owner_generation(owner) != self.generation or str(owner.get("writer_id") or "") != self.writer_id:
            raise RuntimeError(f"state_write_fenced:{self.domain_id}:{self.writer_id}")

    def advance_generation(self) -> int:
        """Advance the durable generation exactly once before the first write."""

        if self.generation_advanced:
            return self.generation
        owner = _read_json(self.owner_path())
        if owner.get("token") != self.token or str(owner.get("writer_id") or "") != self.writer_id:
            raise RuntimeError(f"state_write_fenced:{self.domain_id}:{self.writer_id}")
        generation_path = self.root / f"{self.domain_id}.generation.json"
        generation = int(_read_json(generation_path).get("generation") or 0) + 1
        _atomic_json(generation_path, {"schema": "state_write_generation.v1", "domain_id": self.domain_id, "generation": generation, "updated_at": now_iso()})
        self.generation = generation
        owner["generation"] = generation
        _atomic_json(self.owner_path(), owner)
        self.generation_advanced = True
        return generation

    def release(self) -> None:
        if self.released:
            return
        owner = _read_json(self.owner_path())
        if owner.get("token") == self.token and self._owner_generation(owner) == self.generation:
            shutil.rmtree(self.lock_dir, ignore_errors=True)
        self.released = True

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.release()

    def __del__(self) -> None:
        self.release()


def try_acquire_state_write_lease(domain_id: str, writer_id: str, *, state_root: Path | None = None, timeout_seconds: float = 10.0, lease_seconds: float = DEFAULT_LEASE_SECONDS, contract_path: Path = CONTRACT_PATH, advance_generation_on_acquire: bool = True) -> StateWriteLease | None:
    domain = domain_contract(domain_id, contract_path)
    if writer_id not in [str(item) for item in domain.get("allowed_writers", [])]:
        raise PermissionError(f"state_writer_not_authorized:{domain_id}:{writer_id}")
    root = coordination_root(domain_id, state_root=state_root)
    root.mkdir(parents=True, exist_ok=True)
    lock_dir = root / f"{domain_id}.lock"
    generation_path = root / f"{domain_id}.generation.json"
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        try:
            lock_dir.mkdir()
        except FileExistsError:
            owner = _read_json(lock_dir / "owner.json")
            if float(owner.get("expires_at_epoch") or 0.0) < time.time():
                stale = root / f".{domain_id}.stale-{uuid.uuid4().hex}"
                try:
                    os.replace(lock_dir, stale)
                except OSError:
                    pass
                else:
                    shutil.rmtree(stale, ignore_errors=True)
                    continue
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)
            continue
        current_generation = int(_read_json(generation_path).get("generation") or 0)
        generation = current_generation + 1 if advance_generation_on_acquire else current_generation
        token = uuid.uuid4().hex
        expires_at = time.time() + max(30.0, lease_seconds)
        if advance_generation_on_acquire:
            _atomic_json(generation_path, {"schema": "state_write_generation.v1", "domain_id": domain_id, "generation": generation, "updated_at": now_iso()})
        _atomic_json(lock_dir / "owner.json", {"schema": "state_write_lease.v1", "domain_id": domain_id, "writer_id": writer_id, "token": token, "generation": generation, "pid": os.getpid(), "acquired_at": now_iso(), "expires_at_epoch": expires_at})
        return StateWriteLease(domain_id, writer_id, root, lock_dir, token, generation, expires_at, generation_advanced=advance_generation_on_acquire)


def _require_current_binding(
    lease: StateWriteLease,
    binding_id: str,
    *,
    contract_path: Path,
) -> AuthorityBinding:
    lease.assert_current()
    binding = authority_binding(lease.domain_id, binding_id, contract_path)
    if binding is None:
        raise KeyError(f"binding_not_declared:{lease.domain_id}:{binding_id}")
    return binding


def record_binding_shadow_equivalence(
    lease: StateWriteLease,
    binding_id: str,
    *,
    shadow_writer: str,
    expected_signature: str,
    observed_signature: str,
    state_root: Path | None = None,
    contract_path: Path = CONTRACT_PATH,
) -> dict[str, Any]:
    """Record one shadow comparison under the existing domain lease.

    The shadow writer does not gain write authority from this operation.  The
    result is a small owner receipt that a subsequent transfer must consume.
    """

    binding = _require_current_binding(lease, binding_id, contract_path=contract_path)
    if binding.shadow_writer != shadow_writer:
        raise PermissionError(f"binding_shadow_writer_not_authorized:{binding.key}:{shadow_writer}")
    root = coordination_root(lease.domain_id, state_root=state_root)
    state = _read_json(_binding_state_path(root, binding)) or _default_binding_state(binding)
    if state.get("active_writer") != lease.writer_id or lease.writer_id in state.get("frozen_writers", []):
        raise PermissionError(f"binding_shadow_not_owned:{binding.key}:{lease.writer_id}")
    equivalent = bool(expected_signature) and expected_signature == observed_signature
    shadow = {
        "writer_id": shadow_writer,
        "expected_signature": expected_signature,
        "observed_signature": observed_signature,
        "equivalent": equivalent,
        "lease_generation": lease.generation,
        "recorded_at": now_iso(),
    }
    state["shadow"] = shadow
    state["status"] = "shadow"
    _atomic_json(_binding_state_path(root, binding), state)
    return {"schema": "state_write_authority.binding_shadow.v1", "ok": equivalent, "binding": state, "shadow": shadow}


def transfer_authority_binding(
    lease: StateWriteLease,
    binding_id: str,
    *,
    expected_binding_generation: int,
    state_root: Path | None = None,
    contract_path: Path = CONTRACT_PATH,
) -> dict[str, Any]:
    """Atomically transfer one eligible binding after an equivalent shadow run."""

    binding = _require_current_binding(lease, binding_id, contract_path=contract_path)
    if binding.transfer_mode != "eligible":
        raise PermissionError(f"binding_transfer_not_enabled:{binding.key}")
    root = coordination_root(lease.domain_id, state_root=state_root)
    state = _read_json(_binding_state_path(root, binding)) or _default_binding_state(binding)
    if state.get("binding_generation") != expected_binding_generation:
        raise RuntimeError(f"binding_generation_fenced:{binding.key}")
    if state.get("status") != "shadow":
        raise RuntimeError(f"binding_transfer_requires_fresh_shadow:{binding.key}")
    if state.get("active_writer") != lease.writer_id:
        raise PermissionError(f"binding_transfer_not_owned:{binding.key}:{lease.writer_id}")
    shadow = state.get("shadow") if isinstance(state.get("shadow"), dict) else {}
    if shadow.get("writer_id") != binding.shadow_writer or shadow.get("equivalent") is not True:
        raise RuntimeError(f"binding_shadow_not_equivalent:{binding.key}")
    lease.advance_generation()
    previous_writer = str(state["active_writer"])
    state.update(
        {
            "active_writer": binding.shadow_writer,
            "previous_active_writer": previous_writer,
            "frozen_writers": sorted({*map(str, state.get("frozen_writers", [])), previous_writer}),
            "binding_generation": expected_binding_generation + 1,
            "lease_generation": lease.generation,
            "status": "transferred",
            "transferred_at": now_iso(),
        }
    )
    _atomic_json(_binding_state_path(root, binding), state)
    receipt = {"schema": "state_write_authority.binding_transfer_receipt.v1", "ok": True, "action": "transfer", "binding": state, "lease_writer": lease.writer_id, "lease_generation": lease.generation}
    _atomic_json(_binding_receipt_path(root, binding, "transfer", state["binding_generation"]), receipt)
    return receipt


def rollback_authority_binding(
    lease: StateWriteLease,
    binding_id: str,
    *,
    expected_binding_generation: int,
    state_root: Path | None = None,
    contract_path: Path = CONTRACT_PATH,
) -> dict[str, Any]:
    """Roll back a transferred binding only from the current fenced generation."""

    binding = _require_current_binding(lease, binding_id, contract_path=contract_path)
    root = coordination_root(lease.domain_id, state_root=state_root)
    state = _read_json(_binding_state_path(root, binding)) or _default_binding_state(binding)
    if state.get("binding_generation") != expected_binding_generation:
        raise RuntimeError(f"binding_generation_fenced:{binding.key}")
    previous_writer = str(state.get("previous_active_writer") or "")
    if state.get("status") != "transferred" or state.get("active_writer") != lease.writer_id or not previous_writer:
        raise PermissionError(f"binding_rollback_not_owned:{binding.key}:{lease.writer_id}")
    lease.advance_generation()
    current_writer = str(state["active_writer"])
    state.update(
        {
            "active_writer": previous_writer,
            "previous_active_writer": current_writer,
            "frozen_writers": [current_writer],
            "binding_generation": expected_binding_generation + 1,
            "lease_generation": lease.generation,
            "status": "rolled_back",
            "rolled_back_at": now_iso(),
        }
    )
    _atomic_json(_binding_state_path(root, binding), state)
    receipt = {"schema": "state_write_authority.binding_transfer_receipt.v1", "ok": True, "action": "rollback", "binding": state, "lease_writer": lease.writer_id, "lease_generation": lease.generation}
    _atomic_json(_binding_receipt_path(root, binding, "rollback", state["binding_generation"]), receipt)
    return receipt


def authorize_binding_write(
    lease: StateWriteLease,
    binding_id: str,
    *,
    state_root: Path | None = None,
    contract_path: Path = CONTRACT_PATH,
) -> dict[str, Any]:
    """Fail closed unless the leased writer is the single active binding writer."""

    binding = _require_current_binding(lease, binding_id, contract_path=contract_path)
    root = coordination_root(lease.domain_id, state_root=state_root)
    state = _read_json(_binding_state_path(root, binding)) or _default_binding_state(binding)
    if lease.writer_id != state.get("active_writer") or lease.writer_id in state.get("frozen_writers", []):
        raise PermissionError(f"binding_write_not_authorized:{binding.key}:{lease.writer_id}")
    return {"schema": "state_write_authority.binding_write_authorization.v1", "ok": True, "binding": state, "lease_generation": lease.generation}


def acquire_publication_barrier(*, timeout_seconds: float = 15.0) -> StateWriteLease | None:
    """Acquire the config-domain barrier held through terminal publication."""

    return try_acquire_state_write_lease(
        "codex_config",
        "codex_environment_mirror",
        state_root=codex_config_coordination_root(),
        timeout_seconds=timeout_seconds,
        lease_seconds=PUBLICATION_BARRIER_LEASE_SECONDS,
        advance_generation_on_acquire=False,
    )


def _git_head(path: Path) -> str:
    try:
        process = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return process.stdout.strip() if process.returncode == 0 else ""


def _path_digest(path: Path) -> str:
    try:
        return sha256_bytes(path.read_bytes()) if path.is_file() and not path.is_symlink() else "missing"
    except OSError:
        return "unreadable"


def _external_runtime_field_paths(target: str) -> list[tuple[str, ...]]:
    try:
        domain = domain_contract("codex_config")
    except (KeyError, OSError, ValueError, json.JSONDecodeError):
        return []
    return [
        tuple(str(part) for part in item.get("path", []))
        for item in domain.get("external_runtime_fields", [])
        if isinstance(item, dict)
        and str(item.get("target") or "") == target
        and isinstance(item.get("path"), list)
        and item.get("path")
    ]


def _remove_semantic_path(root: dict[str, Any], path: tuple[str, ...]) -> None:
    current: dict[str, Any] = root
    parents: list[tuple[dict[str, Any], str]] = []
    for part in path[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            return
        parents.append((current, part))
        current = child
    if not path or path[-1] not in current:
        return
    del current[path[-1]]
    for parent, key in reversed(parents):
        child = parent.get(key)
        if isinstance(child, dict) and not child:
            del parent[key]


def _semantic_config_digest(path: Path, *, target: str) -> str:
    """Hash governed config semantics while preserving raw hash as diagnostics."""

    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
        for field_path in _external_runtime_field_paths(target):
            _remove_semantic_path(payload, field_path)
        return sha256_bytes(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
    except (OSError, UnicodeError, tomllib.TOMLDecodeError):
        return _path_digest(path)


def coordination_state(domain_id: str) -> dict[str, Any]:
    """Read fencing state without acquiring or renewing a writer lease."""

    domain = domain_contract(domain_id)
    mode = str(domain.get("coordination_mode") or "")
    if mode != "shared_lease_generation":
        return {"domain_id": domain_id, "coordination_mode": mode, "generation": None, "active_lease": False}
    root = coordination_root(
        domain_id,
        state_root=codex_config_coordination_root() if domain_id == "codex_config" else None,
    )
    generation = int(_read_json(root / f"{domain_id}.generation.json").get("generation") or 0)
    owner = _read_json(root / f"{domain_id}.lock" / "owner.json")
    expires_at = float(owner.get("expires_at_epoch") or 0.0)
    active = bool(owner.get("token")) and expires_at >= time.time()
    return {
        "domain_id": domain_id,
        "coordination_mode": mode,
        "generation": generation,
        "active_lease": active,
        "writer_id": str(owner.get("writer_id") or "") if active else "",
        "expires_at_epoch": expires_at if active else 0.0,
    }


def observed_state_signature() -> dict[str, Any]:
    windows_home = Path("/mnt/c/Users/45543/.codex")
    wsl_home = Path.home() / ".codex-app"
    paths = {"windows_config": windows_home / "config.toml", "windows_managed_projection": windows_home / "state" / "managed-config-projection.json", "wsl_config": wsl_home / "config.toml", "wsl_managed_projection": wsl_home / "state" / "managed-config-projection.json", "startup_baseline": BRIDGE / "codex_startup_baseline.json", "drift_review": BRIDGE / "runtime" / "codex_environment_mirror" / "drift-review.json"}
    raw_digests = {name: _path_digest(path) for name, path in paths.items()}
    digests = dict(raw_digests)
    digests["windows_config"] = _semantic_config_digest(
        paths["windows_config"], target="windows_codex_config"
    )
    work_git_head = _git_head(ROOT.parent)
    bare_head = _git_head(Path("/mnt/c/WSL/Codex-Wsl-Lab/git/codex-workspace.git"))
    coordination = {
        str(domain.get("domain_id")): coordination_state(str(domain.get("domain_id")))
        for domain in contract_domains()
        if domain.get("coordination_mode") == "shared_lease_generation"
    }
    payload = {"digests": digests, "coordination": coordination, "work_git_head": work_git_head, "windows_bare_head": bare_head}
    return {
        **payload,
        "raw_digests": raw_digests,
        "signature": sha256_bytes(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ),
    }


def _retired_plugin_state() -> dict[str, Any]:
    retired = sorted(str(item) for item in _read_json(BRIDGE / "codex_startup_baseline.json").get("decommissioned_plugins", []) if str(item))
    findings: list[dict[str, str]] = []
    for scope, path in (("windows_config", Path("/mnt/c/Users/45543/.codex/config.toml")), ("windows_managed_projection", Path("/mnt/c/Users/45543/.codex/state/managed-config-projection.json")), ("wsl_config", Path.home() / ".codex-app" / "config.toml"), ("wsl_managed_projection", Path.home() / ".codex-app" / "state" / "managed-config-projection.json")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        findings.extend({"scope": scope, "path": str(path), "plugin": plugin} for plugin in retired if plugin in text)
    return {"ok": not findings, "retired_plugins": retired, "findings": findings}


def binding_snapshot(contract_path: Path = CONTRACT_PATH) -> dict[str, Any]:
    domains = {str(domain.get("domain_id") or ""): domain for domain in contract_domains(contract_path)}
    bindings = authority_bindings(contract_path)
    seen_ids: set[tuple[str, str]] = set()
    seen_keys: set[str] = set()
    seen_scopes: list[AuthorityBinding] = []
    issues: list[dict[str, str]] = []
    for domain in contract_domains(contract_path):
        if not isinstance(domain.get("authority_bindings", []), list):
            issues.append({"binding": str(domain.get("domain_id") or ""), "reason": "authority_bindings_not_list"})
    for binding in bindings:
        domain = domains.get(binding.domain_id, {})
        marker = (binding.domain_id, binding.binding_id)
        if not binding.binding_id or marker in seen_ids:
            issues.append({"binding": binding.key, "reason": "binding_id_not_unique"})
        seen_ids.add(marker)
        if binding.key in seen_keys:
            issues.append({"binding": binding.key, "reason": "field_path_has_multiple_authorities"})
        seen_keys.add(binding.key)
        for existing in seen_scopes:
            same_target = binding.domain_id == existing.domain_id and binding.target == existing.target
            overlaps = binding.path[: len(existing.path)] == existing.path or existing.path[: len(binding.path)] == binding.path
            if same_target and overlaps:
                issues.append({"binding": binding.key, "reason": "field_path_scope_overlap"})
        seen_scopes.append(binding)
        writers = {str(writer) for writer in domain.get("allowed_writers", [])}
        if binding.target not in {str(target) for target in domain.get("targets", [])}:
            issues.append({"binding": binding.key, "reason": "target_not_in_domain"})
        if not binding.path or any(not part for part in binding.path):
            issues.append({"binding": binding.key, "reason": "path_required"})
        if not binding.active_writer:
            issues.append({"binding": binding.key, "reason": "active_writer_required"})
        if binding.shadow_writer and binding.shadow_writer not in writers:
            issues.append({"binding": binding.key, "reason": "shadow_writer_not_authorized"})
        if binding.transfer_mode not in {"shadow_only", "eligible"}:
            issues.append({"binding": binding.key, "reason": "binding_transfer_mode_invalid"})
        if binding.transfer_mode == "eligible" and (
            binding.active_writer not in writers
            or binding.shadow_writer not in writers
            or binding.active_writer == binding.shadow_writer
        ):
            issues.append({"binding": binding.key, "reason": "eligible_transfer_requires_two_declared_writers"})
    return {
        "schema": "state_write_authority.binding_snapshot.v1",
        "ok": not issues,
        "binding_count": len(bindings),
        "bindings": [
            {
                "binding_id": binding.binding_id,
                "binding_key": binding.key,
                "domain_id": binding.domain_id,
                "target": binding.target,
                "path": list(binding.path),
                "active_writer": binding.active_writer,
                "shadow_writer": binding.shadow_writer,
                "transfer_mode": binding.transfer_mode,
            }
            for binding in bindings
        ],
        "issues": issues,
    }


def snapshot(contract_path: Path = CONTRACT_PATH) -> dict[str, Any]:
    contract = read_contract(contract_path)
    domains = contract_domains(contract_path)
    ids = [str(item.get("domain_id") or "") for item in domains]
    targets: dict[str, str] = {}
    duplicate_targets: list[dict[str, str]] = []
    missing_writers: list[dict[str, str]] = []
    for domain in domains:
        domain_id = str(domain.get("domain_id") or "")
        for target in [str(item) for item in domain.get("targets", []) if str(item)]:
            if target in targets and targets[target] != domain_id:
                duplicate_targets.append({"target": target, "first": targets[target], "second": domain_id})
            targets[target] = domain_id
        for writer in [str(item) for item in domain.get("allowed_writers", []) if str(item)]:
            module = BRIDGE / f"{writer}.py"
            if not module.is_file():
                missing_writers.append({"domain_id": domain_id, "writer": writer, "path": str(module)})
            elif domain.get("coordination_mode") == "shared_lease_generation":
                source = module.read_text(encoding="utf-8")
                barrier_writer = str(domain.get("publication_barrier_writer") or "")
                wired = (
                    "acquire_publication_barrier" in source
                    if writer == barrier_writer
                    else "state_write_authority" in source and '"codex_config"' in source
                )
                if not wired:
                    missing_writers.append({
                        "domain_id": domain_id,
                        "writer": writer,
                        "path": str(module),
                        "reason": "shared_lease_not_consumed",
                    })
    retired = _retired_plugin_state()
    external_fields = [
        item
        for domain in domains
        for item in domain.get("external_runtime_fields", [])
        if isinstance(item, dict)
    ]
    external_fields_valid = all(
        str(item.get("target") or "") in targets
        and isinstance(item.get("path"), list)
        and bool(item.get("path"))
        and all(isinstance(part, str) and part for part in item.get("path", []))
        and bool(str(item.get("authority") or ""))
        and item.get("policy")
        == "preserve_live_exclude_from_recovery_ledger_and_publication_signature"
        for item in external_fields
    )
    external_field_keys = [
        (str(item.get("target") or ""), tuple(str(part) for part in item.get("path", [])))
        for item in external_fields
    ]
    codex_config_domain = next(
        (domain for domain in domains if domain.get("domain_id") == "codex_config"),
        {},
    )
    external_fields_complete = bool(codex_config_domain.get("external_runtime_fields"))
    external_fields_unique = len(external_field_keys) == len(set(external_field_keys))
    bindings = binding_snapshot(contract_path)
    checks = [{"name": "contract_schema", "ok": contract.get("schema") == "state_write_authorities.v1"}, {"name": "domain_ids_unique", "ok": bool(ids) and len(ids) == len(set(ids)) and all(ids)}, {"name": "state_targets_single_authority", "ok": not duplicate_targets}, {"name": "authorized_writer_modules_exist", "ok": not missing_writers}, {"name": "external_runtime_fields_explicit", "ok": external_fields_complete and external_fields_valid and external_fields_unique}, {"name": "field_path_binding_single_authority", "ok": bindings.get("ok") is True}, {"name": "retired_negative_state_converged", "ok": retired.get("ok") is True}]
    return {"schema": "state_write_authority.snapshot.v1", "ok": all(item["ok"] for item in checks), "generated_at": now_iso(), "contract_path": str(contract_path), "contract_digest": _path_digest(contract_path), "domain_count": len(domains), "domains": domains, "checks": checks, "duplicate_targets": duplicate_targets, "missing_writers": missing_writers, "binding_authority": bindings, "retired_state": retired, "observed_state": observed_state_signature(), "principles": ["one_authority_per_state_fact", "one_active_writer_per_field_or_path", "authorized_writers_share_one_cross_platform_lease", "monotonic_generation_fences_stale_writers", "shadow_equivalence_precedes_binding_transfer", "desired_state_includes_negative_and_retired_state", "publish_requires_stable_observed_generation", "post_publish_acceptance_is_read_only"]}


def pre_publish_gate(*, stability_seconds: float = DEFAULT_STABILITY_SECONDS, held_barrier: StateWriteLease | None = None) -> dict[str, Any]:
    owned_barrier = held_barrier is None
    barrier = held_barrier or acquire_publication_barrier()
    if barrier is None:
        return {"schema": "state_write_authority.pre_publish_gate.v1", "ok": False, "reason": "state_writer_busy", "next_action": "wait for the declared writer; do not publish or create a second snapshot"}
    try:
        barrier.assert_current()
        return _pre_publish_gate_under_barrier(barrier, stability_seconds=max(0.0, stability_seconds))
    finally:
        if owned_barrier:
            barrier.release()


def _pre_publish_gate_under_barrier(barrier: StateWriteLease, *, stability_seconds: float) -> dict[str, Any]:
    before = snapshot()
    if not before.get("ok"):
        return {"schema": "state_write_authority.pre_publish_gate.v1", "ok": False, "reason": "authority_validation_failed", "snapshot": before}
    first = observed_state_signature()
    if stability_seconds > 0:
        time.sleep(stability_seconds)
    barrier.assert_current()
    second = observed_state_signature()
    stable = first.get("signature") == second.get("signature")
    active_leases = [
        {"domain_id": domain_id, **dict(state)}
        for domain_id, state in dict(second.get("coordination") or {}).items()
        if isinstance(state, dict) and state.get("active_lease") and state.get("writer_id") != barrier.writer_id
    ]
    heads_match = bool(second.get("work_git_head")) and second.get("work_git_head") == second.get("windows_bare_head")
    ok = stable and heads_match and not active_leases
    reason = "stable" if ok else "active_state_writer" if active_leases else "state_changed_during_publish_preflight" if not stable else "work_git_bare_mismatch"
    return {"schema": "state_write_authority.pre_publish_gate.v1", "ok": ok, "generated_at": now_iso(), "stability_seconds": stability_seconds, "stable": stable, "barrier_generation": barrier.generation, "barrier_writer": barrier.writer_id, "active_leases": active_leases, "first_signature": first.get("signature"), "second_signature": second.get("signature"), "work_git_bare_match": heads_match, "state": second, "reason": reason, "next_action": "publish once while retaining this barrier, then use read-only status/readback" if ok else "identify the active writer, reconcile, and restart the stability window before publish"}


def validate(contract_path: Path = CONTRACT_PATH) -> dict[str, Any]:
    snap = snapshot(contract_path)
    return {"schema": "state_write_authority.validate.v1", "ok": bool(snap.get("ok")), "generated_at": now_iso(), "snapshot": snap}


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="State-domain writer authority, lease, and pre-publish stability owner")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("snapshot")
    sub.add_parser("validate")
    gate = sub.add_parser("pre-publish")
    gate.add_argument("--stability-seconds", type=float, default=DEFAULT_STABILITY_SECONDS)
    args = parser.parse_args(list(argv) if argv is not None else None)
    payload = snapshot() if args.command == "snapshot" else validate() if args.command == "validate" else pre_publish_gate(stability_seconds=max(0.0, args.stability_seconds))
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
