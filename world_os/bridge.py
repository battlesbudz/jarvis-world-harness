from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .runtime import FEASIBLE_REQUEST_ACTIONS, Event, World


BRIDGE_SCHEMA_VERSION = 1
BRIDGE_STATE_SCHEMA_VERSION = 3
ENGINE_AUTHORITY_SCHEMA_VERSION = 4
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")
_BRIDGE_EXTENSION = "h2_bridge"


class BridgeValidationError(ValueError):
    pass


def _is_supported_schema_version(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == BRIDGE_SCHEMA_VERSION


def _is_supported_authority_schema_version(value: Any) -> bool:
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and value == ENGINE_AUTHORITY_SCHEMA_VERSION
    )


def _is_exact_version(value: Any, expected: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == expected


def _counter(value: Any, name: str, *, minimum: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise BridgeValidationError(f"{name} must be a non-boolean integer >= {minimum}")
    return value


def _counter_map(value: Any, name: str, *, minimum: int = 0) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise BridgeValidationError(f"{name} must be an object")
    return {str(key): _counter(item, f"{name}.{key}", minimum=minimum) for key, item in value.items()}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _json_object(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        copied = json.loads(_canonical(dict(value)))
    except (TypeError, ValueError) as error:
        raise BridgeValidationError(f"bridge payload must be a JSON object: {error}") from error
    if not isinstance(copied, dict):
        raise BridgeValidationError("bridge payload must be a JSON object")
    return copied


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class Envelope:
    schema_version: int
    message_id: str
    correlation_id: str
    sequence: int
    actor_id: str
    message_type: str
    payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not _is_supported_schema_version(self.schema_version):
            raise BridgeValidationError(f"unsupported bridge schema version: {self.schema_version!r}")
        for name, value in (
            ("message_id", self.message_id),
            ("correlation_id", self.correlation_id),
            ("actor_id", self.actor_id),
            ("message_type", self.message_type),
        ):
            if not isinstance(value, str) or not _ID_PATTERN.fullmatch(value):
                raise BridgeValidationError(f"invalid {name}: {value!r}")
        if not isinstance(self.sequence, int) or isinstance(self.sequence, bool) or self.sequence < 1:
            raise BridgeValidationError("bridge sequence must be a positive integer")
        if not isinstance(self.payload, Mapping):
            raise BridgeValidationError("bridge payload must be an object")
        object.__setattr__(self, "payload", _freeze(_json_object(self.payload)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "message_id": self.message_id,
            "correlation_id": self.correlation_id,
            "sequence": self.sequence,
            "actor_id": self.actor_id,
            "message_type": self.message_type,
            "payload": _thaw(self.payload),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Envelope:
        if not isinstance(data, Mapping):
            raise BridgeValidationError("bridge envelope must be an object")
        required = {
            "schema_version",
            "message_id",
            "correlation_id",
            "sequence",
            "actor_id",
            "message_type",
            "payload",
        }
        if set(data) != required:
            raise BridgeValidationError("bridge envelope fields do not match the schema")
        return cls(**{key: data[key] for key in required})

    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EngineDecision:
    status: str
    outcome: Envelope
    engine_event: Envelope | None = None

    def __post_init__(self) -> None:
        if self.status not in {"applied", "rejected"}:
            raise BridgeValidationError(f"invalid engine decision status: {self.status!r}")
        if self.outcome.message_type != "engine_proposal_outcome":
            raise BridgeValidationError("engine decision requires an outcome envelope")
        if self.outcome.payload.get("status") != self.status:
            raise BridgeValidationError("engine outcome status does not match the decision")
        if set(self.outcome.payload) != {
            "authority_proof", "engine_event_id", "reason", "state_version", "status"
        }:
            raise BridgeValidationError("engine outcome payload fields do not match the schema")
        if not isinstance(self.outcome.payload.get("authority_proof"), str) or not re.fullmatch(
            r"[0-9a-f]{128}", self.outcome.payload["authority_proof"]
        ):
            raise BridgeValidationError("engine outcome authority proof is malformed")
        if not isinstance(self.outcome.payload.get("reason"), str) or not self.outcome.payload["reason"]:
            raise BridgeValidationError("engine outcome requires a non-empty reason")
        state_version = self.outcome.payload.get("state_version")
        if not isinstance(state_version, int) or isinstance(state_version, bool) or state_version < 0:
            raise BridgeValidationError("engine outcome requires a non-negative state version")
        if self.status == "applied":
            if self.engine_event is None or self.engine_event.message_type != "engine_action_applied":
                raise BridgeValidationError("applied decision requires an authoritative engine event")
            if self.engine_event.correlation_id != self.outcome.correlation_id:
                raise BridgeValidationError("engine event and outcome correlation do not match")
            if self.outcome.payload.get("engine_event_id") != self.engine_event.message_id:
                raise BridgeValidationError("engine outcome does not reference its authoritative event")
            if self.engine_event.actor_id != self.outcome.actor_id:
                raise BridgeValidationError("engine event and outcome actors do not match")
            engine_state_version = _counter(
                self.engine_event.payload.get("state_version"),
                "authoritative engine event state_version",
                minimum=1,
            )
            if engine_state_version != state_version:
                raise BridgeValidationError("engine event and outcome state versions do not match")
            if set(self.engine_event.payload) != {
                "action_type", "command", "destination", "prior_event_digests", "state_version"
            }:
                raise BridgeValidationError("authoritative engine event payload fields do not match the schema")
            if not all(
                isinstance(self.engine_event.payload.get(key), str) and self.engine_event.payload[key]
                for key in ("action_type", "command")
            ):
                raise BridgeValidationError("authoritative engine event action fields are malformed")
            destination = self.engine_event.payload.get("destination")
            if destination is not None and not isinstance(destination, str):
                raise BridgeValidationError("authoritative engine event destination is malformed")
            lineage = self.engine_event.payload.get("prior_event_digests")
            if (
                not isinstance(lineage, tuple)
                or len(lineage) != state_version - 1
                or any(not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in lineage)
            ):
                raise BridgeValidationError("authoritative engine event lineage is malformed")
        elif self.engine_event is not None or self.outcome.payload.get("engine_event_id") is not None:
            raise BridgeValidationError("rejected decision cannot reference an engine event")

    @property
    def reason(self) -> str:
        return str(self.outcome.payload["reason"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "outcome": self.outcome.to_dict(),
            "engine_event": self.engine_event.to_dict() if self.engine_event else None,
        }

    def digest(self) -> str:
        return hashlib.sha256(_canonical(self.to_dict()).encode("utf-8")).hexdigest()

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> EngineDecision:
        if not isinstance(data, Mapping) or set(data) != {"status", "outcome", "engine_event"}:
            raise BridgeValidationError("engine decision fields do not match the schema")
        engine_event = data["engine_event"]
        return cls(
            str(data["status"]),
            Envelope.from_dict(data["outcome"]),
            Envelope.from_dict(engine_event) if engine_event is not None else None,
        )


def _state_advance_is_possible(
    earlier_sequence: int,
    earlier_state_version: int,
    later: EngineDecision,
) -> bool:
    sequence_delta = later.outcome.sequence - earlier_sequence
    state_delta = int(later.outcome.payload["state_version"]) - earlier_state_version
    minimum_delta = 1 if later.status == "applied" else 0
    maximum_delta = sequence_delta if later.status == "applied" else sequence_delta - 1
    return minimum_delta <= state_delta <= maximum_delta


def _origin_material(proposal: Envelope) -> bytes:
    data = proposal.to_dict()
    data["payload"].pop("origin_proof", None)
    return _canonical(data).encode("utf-8")


def _origin_proof(proposal: Envelope, key: bytes) -> str:
    return hmac.new(key, _origin_material(proposal), hashlib.sha256).hexdigest()


def _proposal_key(value: bytes | str) -> bytes:
    key = value.encode("utf-8") if isinstance(value, str) else value
    if not isinstance(key, bytes) or len(key) < 16:
        raise BridgeValidationError("proposal origin key must contain at least 16 bytes")
    return key


def _authority_material(outcome: Envelope, engine_event: Envelope | None) -> bytes:
    outcome_data = outcome.to_dict()
    outcome_data["payload"].pop("authority_proof", None)
    material = {
        "outcome": outcome_data,
        "engine_event": engine_event.to_dict() if engine_event is not None else None,
    }
    return _canonical(material).encode("utf-8")


def _authority_private_key(value: bytes | Ed25519PrivateKey) -> Ed25519PrivateKey:
    if isinstance(value, Ed25519PrivateKey):
        return value
    if not isinstance(value, bytes) or len(value) != 32:
        raise BridgeValidationError("engine authority private key must be 32 raw Ed25519 bytes")
    return Ed25519PrivateKey.from_private_bytes(value)


def _authority_public_key(value: bytes | Ed25519PublicKey) -> Ed25519PublicKey:
    if isinstance(value, Ed25519PublicKey):
        return value
    if not isinstance(value, bytes) or len(value) != 32:
        raise BridgeValidationError("engine authority public key must be 32 raw Ed25519 bytes")
    return Ed25519PublicKey.from_public_bytes(value)


def _authority_proof(
    outcome: Envelope,
    engine_event: Envelope | None,
    private_key: Ed25519PrivateKey,
) -> str:
    return private_key.sign(_authority_material(outcome, engine_event)).hex()


def _has_valid_authority_proof(
    outcome: Envelope,
    engine_event: Envelope | None,
    proof: Any,
    public_key: Ed25519PublicKey,
) -> bool:
    if not isinstance(proof, str) or not re.fullmatch(r"[0-9a-f]{128}", proof):
        return False
    try:
        public_key.verify(bytes.fromhex(proof), _authority_material(outcome, engine_event))
    except (InvalidSignature, ValueError):
        return False
    return True


class WorldOSBridge:
    """Idempotent adapter from authoritative engine observations to World OS proposals."""

    def __init__(
        self,
        world: World,
        role_stations: Mapping[str, str],
        proposal_origin_key: bytes | str,
        engine_authority_public_key: bytes | Ed25519PublicKey,
    ):
        self.world = world
        self.role_stations = dict(role_stations)
        self._proposal_origin_key = _proposal_key(proposal_origin_key)
        self._engine_authority_public_key = _authority_public_key(engine_authority_public_key)
        missing_roles = {
            actor.role
            for actor in world.actors.values()
            if actor.category == "non_thinker" and actor.role not in self.role_stations
        }
        if missing_roles:
            raise BridgeValidationError(f"missing role stations: {sorted(missing_roles)}")
        self._observations: dict[str, tuple[Envelope, tuple[Envelope, ...]]] = {}
        self._last_engine_sequence: dict[str, int] = {}
        self._proposal_sequence: dict[str, int] = {}
        self._pending: dict[str, Envelope] = {}
        self._decisions: dict[str, EngineDecision] = {}
        self._buffered_decisions: dict[str, EngineDecision] = {}
        self._engine_events: dict[str, str] = {}
        self._engine_versions: dict[int, str] = {}
        self._buffered_observations: dict[str, Envelope] = {}
        self._delivery_results: dict[str, tuple[Envelope, ...]] = {}
        self._delivery_observations: dict[str, tuple[str, ...]] = {}
        saved = world.extension_state(_BRIDGE_EXTENSION)
        if saved is not None:
            self._restore(saved)
        else:
            self._persist()

    def _state(self) -> dict[str, Any]:
        return {
            "schema_version": BRIDGE_STATE_SCHEMA_VERSION,
            "role_stations": dict(sorted(self.role_stations.items())),
            "observations": [
                {
                    "envelope": observation.to_dict(),
                    "proposals": [proposal.to_dict() for proposal in proposals],
                }
                for _message_id, (observation, proposals) in sorted(self._observations.items())
            ],
            "last_engine_sequence": dict(sorted(self._last_engine_sequence.items())),
            "proposal_sequence": dict(sorted(self._proposal_sequence.items())),
            "pending": [proposal.to_dict() for proposal in sorted(self._pending.values(), key=lambda item: item.message_id)],
            "decisions": [decision.to_dict() for decision in sorted(self._decisions.values(), key=lambda item: item.outcome.message_id)],
            "buffered_decisions": [
                decision.to_dict()
                for decision in sorted(
                    self._buffered_decisions.values(), key=lambda item: item.outcome.message_id
                )
            ],
            "engine_events": dict(sorted(self._engine_events.items())),
            "engine_versions": {str(version): digest for version, digest in sorted(self._engine_versions.items())},
            "buffered_observations": [
                item.to_dict() for item in sorted(self._buffered_observations.values(), key=lambda item: item.message_id)
            ],
            "delivery_results": {
                message_id: [proposal.to_dict() for proposal in proposals]
                for message_id, proposals in sorted(self._delivery_results.items())
            },
            "delivery_observations": {
                message_id: list(observation_ids)
                for message_id, observation_ids in sorted(self._delivery_observations.items())
            },
        }

    def _persist(self) -> None:
        self.world.set_extension_state(_BRIDGE_EXTENSION, self._state())

    def _restore(self, state: Mapping[str, Any]) -> None:
        migrate_noncontiguous_decisions = (
            _is_exact_version(state.get("schema_version"), 1)
            or _is_exact_version(state.get("schema_version"), 2)
        )
        required = {
            "schema_version", "role_stations", "observations", "last_engine_sequence",
            "proposal_sequence", "pending", "decisions", "engine_events",
            "engine_versions", "buffered_observations", "delivery_results",
            "delivery_observations", "buffered_decisions",
        }
        pre_decision_buffer_required = required - {"buffered_decisions"}
        legacy_required = pre_decision_buffer_required - {
            "buffered_observations", "delivery_results", "delivery_observations"
        }
        current_legacy_required = required - {
            "buffered_observations", "delivery_results", "delivery_observations"
        }
        previous_required = pre_decision_buffer_required - {"delivery_observations"}
        current_previous_required = required - {"delivery_observations"}
        state_fields = set(state)
        legacy_migration = (
            state_fields == legacy_required or state_fields == current_legacy_required
        )
        previous_migration = (
            state_fields == previous_required or state_fields == current_previous_required
        )
        migrated = legacy_migration or previous_migration
        migrate_delivery_observations = migrated
        if set(state) == legacy_required or set(state) == current_legacy_required:
            state = {**dict(state), "buffered_observations": [], "delivery_results": {}}
        if set(state) == previous_required or set(state) == current_previous_required:
            state = {**dict(state), "delivery_observations": {}}
        if set(state) == pre_decision_buffer_required:
            state = {**dict(state), "buffered_decisions": []}
            migrated = True
        if set(state) != required or not (
            _is_exact_version(state.get("schema_version"), 1)
            or _is_exact_version(state.get("schema_version"), 2)
            or _is_exact_version(state.get("schema_version"), BRIDGE_STATE_SCHEMA_VERSION)
        ):
            raise BridgeValidationError("persisted bridge state is incompatible")
        if state["schema_version"] == 1:
            try:
                proofs = [
                    item["outcome"]["payload"]["authority_proof"]
                    for item in state["decisions"]
                ]
            except (KeyError, TypeError) as error:
                raise BridgeValidationError(
                    f"persisted bridge state is malformed: {error}"
                ) from error
            if any(
                isinstance(proof, str) and re.fullmatch(r"[0-9a-f]{64}", proof)
                for proof in proofs
            ):
                raise BridgeValidationError(
                    "persisted bridge authority signature format is incompatible"
                )
            state = {**dict(state), "schema_version": BRIDGE_STATE_SCHEMA_VERSION}
            migrated = True
        elif state["schema_version"] == 2:
            state = {**dict(state), "schema_version": BRIDGE_STATE_SCHEMA_VERSION}
            migrated = True
        if state.get("role_stations") != self.role_stations:
            raise BridgeValidationError("persisted bridge role stations do not match configuration")
        try:
            pending = [Envelope.from_dict(item) for item in state["pending"]]
            decisions = [EngineDecision.from_dict(item) for item in state["decisions"]]
            buffered_decisions = [
                EngineDecision.from_dict(item) for item in state["buffered_decisions"]
            ]
            if migrate_noncontiguous_decisions:
                contiguous: list[EngineDecision] = []
                noncontiguous: list[EngineDecision] = []
                expected_sequence = 1
                for item in sorted(decisions, key=lambda value: value.outcome.sequence):
                    if item.outcome.sequence == expected_sequence:
                        contiguous.append(item)
                        expected_sequence += 1
                    else:
                        noncontiguous.append(item)
                decisions = contiguous
                buffered_decisions.extend(noncontiguous)
            if len({item.message_id for item in pending}) != len(pending):
                raise BridgeValidationError("persisted pending proposal identity is duplicated")
            if len({item.outcome.message_id for item in decisions}) != len(decisions):
                raise BridgeValidationError("persisted engine outcome identity is duplicated")
            if len({item.outcome.message_id for item in buffered_decisions}) != len(buffered_decisions):
                raise BridgeValidationError("persisted buffered engine outcome identity is duplicated")
            if {item.outcome.message_id for item in decisions} & {
                item.outcome.message_id for item in buffered_decisions
            }:
                raise BridgeValidationError(
                    "persisted engine outcome identity is shared across committed and buffered ledgers"
                )
            observations = {}
            for item in state["observations"]:
                observation = Envelope.from_dict(item["envelope"])
                proposals = tuple(Envelope.from_dict(proposal) for proposal in item["proposals"])
                if observation.message_id in observations:
                    raise BridgeValidationError("persisted engine observation identity is duplicated")
                observations[observation.message_id] = (observation, proposals)
            self._last_engine_sequence = _counter_map(state["last_engine_sequence"], "last_engine_sequence")
            self._proposal_sequence = _counter_map(state["proposal_sequence"], "proposal_sequence")
            self._pending = {item.message_id: item for item in pending}
            self._decisions = {item.outcome.message_id: item for item in decisions}
            self._buffered_decisions = {
                item.outcome.message_id: item for item in buffered_decisions
            }
            self._engine_events = {str(key): str(value) for key, value in state["engine_events"].items()}
            engine_versions = [(int(key), str(value)) for key, value in state["engine_versions"].items()]
            if len({version for version, _digest in engine_versions}) != len(engine_versions):
                raise BridgeValidationError("persisted engine version identity is duplicated")
            self._engine_versions = dict(engine_versions)
            if migrate_noncontiguous_decisions and buffered_decisions:
                self._engine_events = {
                    item.engine_event.message_id: item.engine_event.digest()
                    for item in decisions
                    if item.engine_event is not None
                }
                rebuilt_versions: dict[int, str] = {}
                for item in decisions:
                    if item.engine_event is None:
                        continue
                    for version, digest in enumerate(
                        item.engine_event.payload["prior_event_digests"], start=1
                    ):
                        rebuilt_versions[version] = digest
                    rebuilt_versions[
                        int(item.engine_event.payload["state_version"])
                    ] = item.engine_event.digest()
                self._engine_versions = rebuilt_versions
            buffered = [Envelope.from_dict(item) for item in state["buffered_observations"]]
            if len({item.message_id for item in buffered}) != len(buffered):
                raise BridgeValidationError("persisted buffered observation identity is duplicated")
            if any(item.message_id in observations for item in buffered):
                raise BridgeValidationError("persisted observation identity is shared across applied and buffered ledgers")
            self._buffered_observations = {item.message_id: item for item in buffered}
            self._delivery_results = {
                str(message_id): tuple(Envelope.from_dict(item) for item in proposals)
                for message_id, proposals in state["delivery_results"].items()
            }
            self._observations = observations
            if migrate_delivery_observations:
                delivery_observations: dict[str, tuple[str, ...]] = {}
                covered_observations: set[str] = set()
                for message_id, proposals in self._delivery_results.items():
                    trigger = observations.get(message_id)
                    if trigger is None:
                        delivery_observations[message_id] = (message_id,)
                        continue
                    correlated = {
                        proposal.correlation_id for proposal in proposals
                        if proposal.correlation_id in observations
                    }
                    last_sequence = max(
                        [trigger[0].sequence]
                        + [observations[correlation_id][0].sequence for correlation_id in correlated]
                    )
                    observation_ids = tuple(
                        observation.message_id
                        for observation, _proposals in sorted(
                            observations.values(), key=lambda item: item[0].sequence
                        )
                        if observation.actor_id == trigger[0].actor_id
                        and trigger[0].sequence <= observation.sequence <= last_sequence
                    )
                    delivery_observations[message_id] = observation_ids
                    covered_observations.update(observation_ids)
                for message_id, (observation, proposals) in observations.items():
                    if message_id not in covered_observations:
                        if previous_migration and proposals:
                            continue
                        delivery_observations[message_id] = (message_id,)
                        self._delivery_results[message_id] = proposals
                self._delivery_observations = delivery_observations
            else:
                if not isinstance(state["delivery_observations"], Mapping):
                    raise BridgeValidationError("persisted delivery observations must be an object")
                self._delivery_observations = {
                    str(message_id): tuple(observation_ids)
                    for message_id, observation_ids in state["delivery_observations"].items()
                }
                if any(
                    not isinstance(observation_ids, (list, tuple))
                    or any(not isinstance(item, str) for item in observation_ids)
                    for observation_ids in state["delivery_observations"].values()
                ):
                    raise BridgeValidationError("persisted delivery observation identities are malformed")
        except (BridgeValidationError, KeyError, TypeError, ValueError, AttributeError) as error:
            raise BridgeValidationError(f"persisted bridge state is malformed: {error}") from error
        restored_observation_sequences: dict[str, list[int]] = {}
        for observation, _proposals in self._observations.values():
            restored_observation_sequences.setdefault(observation.actor_id, []).append(observation.sequence)
        if any(
            sorted(sequences) != list(range(1, len(sequences) + 1))
            for sequences in restored_observation_sequences.values()
        ):
            raise BridgeValidationError("persisted engine observation ordering is not unique and contiguous")
        if any(proposal.message_id not in self._pending for _observation, proposals in self._observations.values() for proposal in proposals):
            raise BridgeValidationError("persisted observation references an unknown proposal")
        for observation, proposals in self._observations.values():
            self._validate_observation(observation)
            for proposal in proposals:
                canonical = self._pending.get(proposal.message_id)
                if canonical != proposal or proposal.correlation_id != observation.message_id:
                    raise BridgeValidationError("persisted observation proposal does not match the canonical pending proposal")
        delivered_proposal_ids: list[str] = []
        delivered_observation_ids: list[str] = []
        if set(self._delivery_results) != set(self._delivery_observations):
            raise BridgeValidationError("persisted delivery result and observation groups do not match")
        for message_id, observation_ids in self._delivery_observations.items():
            if not observation_ids or observation_ids[0] != message_id or len(set(observation_ids)) != len(observation_ids):
                raise BridgeValidationError("persisted delivery observation group identity is invalid")
            delivered = [self._observations.get(observation_id) for observation_id in observation_ids]
            if any(item is None for item in delivered):
                raise BridgeValidationError("persisted delivery group references an unknown observation")
            trigger = delivered[0][0]
            if any(
                item[0].actor_id != trigger.actor_id or item[0].sequence != trigger.sequence + offset
                for offset, item in enumerate(delivered)
            ):
                raise BridgeValidationError("persisted delivery observation ordering is invalid")
            expected_proposals = tuple(
                proposal for item in delivered for proposal in item[1]
            )
            if self._delivery_results[message_id] != expected_proposals:
                raise BridgeValidationError("persisted delivery result does not match its applied observations")
            delivered_proposal_ids.extend(proposal.message_id for proposal in expected_proposals)
            delivered_observation_ids.extend(observation_ids)
        if sorted(delivered_observation_ids) != sorted(self._observations):
            raise BridgeValidationError("persisted delivery groups do not cover applied observations exactly once")
        if sorted(delivered_proposal_ids) != sorted(self._pending):
            raise BridgeValidationError("persisted delivery results do not cover the canonical pending proposals exactly once")
        decision_sequences = [decision.outcome.sequence for decision in self._decisions.values()]
        if len(set(decision_sequences)) != len(decision_sequences):
            raise BridgeValidationError("persisted engine outcome ordering slot is duplicated")
        ordered_decisions = sorted(self._decisions.values(), key=lambda item: item.outcome.sequence)
        if [item.outcome.sequence for item in ordered_decisions] != list(
            range(1, len(ordered_decisions) + 1)
        ):
            raise BridgeValidationError("persisted engine outcome ordering is not contiguous")
        if any(
            not _state_advance_is_possible(
                earlier.outcome.sequence,
                int(earlier.outcome.payload["state_version"]),
                later,
            )
            for earlier, later in zip(ordered_decisions, ordered_decisions[1:])
        ):
            raise BridgeValidationError(
                "persisted engine outcome state versions conflict with decision ordering"
            )
        if ordered_decisions and not _state_advance_is_possible(0, 0, ordered_decisions[0]):
            raise BridgeValidationError(
                "persisted engine outcome initial state version conflicts with decision ordering"
            )
        decision_correlations: set[str] = set()
        expected_engine_events: dict[str, str] = {}
        expected_engine_versions: dict[int, str] = {}
        for decision in self._decisions.values():
            if not _has_valid_authority_proof(
                decision.outcome,
                decision.engine_event,
                decision.outcome.payload["authority_proof"],
                self._engine_authority_public_key,
            ):
                raise BridgeValidationError("persisted engine decision authority proof is invalid")
            proposal_id = decision.outcome.correlation_id
            proposal = self._pending.get(proposal_id)
            if proposal is None or decision.outcome.actor_id != proposal.actor_id:
                raise BridgeValidationError("persisted engine decision does not match a pending proposal")
            if proposal_id in decision_correlations:
                raise BridgeValidationError("persisted World OS proposal has multiple engine outcomes")
            decision_correlations.add(proposal_id)
            if decision.engine_event is None:
                continue
            expected_action = {
                key: proposal.payload.get(key)
                for key in ("action_type", "command", "destination")
            }
            actual_action = {
                key: decision.engine_event.payload.get(key)
                for key in expected_action
            }
            if actual_action != expected_action:
                raise BridgeValidationError("persisted authoritative event does not match its proposal")
            event_digest = decision.engine_event.digest()
            event_id = decision.engine_event.message_id
            existing_event = expected_engine_events.get(event_id)
            if existing_event is not None and existing_event != event_digest:
                raise BridgeValidationError("persisted authoritative engine event identity is duplicated")
            expected_engine_events[event_id] = event_digest
            for prior_version, prior_digest in enumerate(
                decision.engine_event.payload["prior_event_digests"], start=1
            ):
                existing_version = expected_engine_versions.get(prior_version)
                if existing_version is not None and existing_version != prior_digest:
                    raise BridgeValidationError("persisted authoritative engine lineage conflicts")
                expected_engine_versions[prior_version] = prior_digest
            state_version = int(decision.engine_event.payload["state_version"])
            existing_version = expected_engine_versions.get(state_version)
            if existing_version is not None and existing_version != event_digest:
                raise BridgeValidationError("persisted authoritative engine state version is duplicated")
            expected_engine_versions[state_version] = event_digest
        if self._engine_events != expected_engine_events or self._engine_versions != expected_engine_versions:
            raise BridgeValidationError("persisted engine identity maps do not match applied decisions")

        buffered_decision_sequences: set[int] = set()
        buffered_decision_correlations: set[str] = set()
        for decision in self._buffered_decisions.values():
            if not _has_valid_authority_proof(
                decision.outcome,
                decision.engine_event,
                decision.outcome.payload["authority_proof"],
                self._engine_authority_public_key,
            ):
                raise BridgeValidationError("persisted buffered engine decision proof is invalid")
            proposal = self._pending.get(decision.outcome.correlation_id)
            if proposal is None or proposal.actor_id != decision.outcome.actor_id:
                raise BridgeValidationError(
                    "persisted buffered engine decision does not match a pending proposal"
                )
            if decision.status == "applied":
                expected_action = {
                    key: proposal.payload.get(key)
                    for key in ("action_type", "command", "destination")
                }
                actual_action = {
                    key: decision.engine_event.payload.get(key) for key in expected_action
                }
                if actual_action != expected_action:
                    raise BridgeValidationError(
                        "persisted buffered authoritative event does not match its proposal"
                    )
            sequence = decision.outcome.sequence
            correlation = decision.outcome.correlation_id
            if (
                sequence <= len(ordered_decisions) + 1
                or sequence in buffered_decision_sequences
                or correlation in decision_correlations
                or correlation in buffered_decision_correlations
            ):
                raise BridgeValidationError("persisted buffered engine decision ordering is invalid")
            buffered_decision_sequences.add(sequence)
            buffered_decision_correlations.add(correlation)

        observation_sequences: dict[str, list[int]] = {}
        for observation, _proposals in self._observations.values():
            observation_sequences.setdefault(observation.actor_id, []).append(observation.sequence)
        expected_last_sequence = {
            actor: max(sequences) for actor, sequences in observation_sequences.items()
        }
        if any(sorted(sequences) != list(range(1, len(sequences) + 1)) for sequences in observation_sequences.values()):
            raise BridgeValidationError("persisted engine observation ordering is not unique and contiguous")
        if self._last_engine_sequence != expected_last_sequence:
            raise BridgeValidationError("persisted engine observation counters do not match the ledger")
        proposal_sequences: dict[str, list[int]] = {}
        time_observation_ticks: dict[str, set[int]] = {}
        for proposal in self._pending.values():
            proposal_sequences.setdefault(proposal.actor_id, []).append(proposal.sequence)
        expected_proposal_sequence = {
            actor: max(sequences) for actor, sequences in proposal_sequences.items()
        }
        if any(sorted(sequences) != list(range(1, len(sequences) + 1)) for sequences in proposal_sequences.values()):
            raise BridgeValidationError("persisted proposal ordering is not unique and contiguous")
        if self._proposal_sequence != expected_proposal_sequence:
            raise BridgeValidationError("persisted proposal counters do not match the ledger")
        buffered_slots: set[tuple[str, int]] = set()
        for observation in self._buffered_observations.values():
            self._validate_observation(observation)
            slot = (observation.actor_id, observation.sequence)
            if (
                slot in buffered_slots
                or observation.sequence <= self._last_engine_sequence.get(observation.actor_id, 0) + 1
            ):
                raise BridgeValidationError("persisted buffered observation ordering is invalid")
            buffered_slots.add(slot)
        for proposal in self._pending.values():
            if not hmac.compare_digest(str(proposal.payload.get("origin_proof", "")), _origin_proof(proposal, self._proposal_origin_key)):
                raise BridgeValidationError("persisted proposal origin proof is invalid")
            try:
                causal_event_id = proposal.payload["causal_event_id"]
                causal_trace = self.world.trace(str(causal_event_id))
                causal_event = causal_trace["event"]
                observation_record = self._observations.get(proposal.correlation_id)
                if observation_record is None:
                    raise BridgeValidationError(
                        "persisted proposal does not match a recorded observation"
                    )
                observation = observation_record[0]
                expected_message_id = (
                    f"world-proposal:{proposal.correlation_id}:{causal_event['id']}"
                )
                if (
                    proposal.message_id != expected_message_id
                    or proposal.actor_id != causal_event["actor"]
                ):
                    raise BridgeValidationError(
                        "persisted proposal does not match its causal event identity"
                    )
                if causal_event["event_type"] == "routine_action":
                    expected_action = {
                        "action_type": "routine_move",
                        "command": causal_event["payload"]["action"],
                        "destination": self.role_stations[causal_event["payload"]["role"]],
                    }
                else:
                    expected_action = {
                        "action_type": causal_event["event_type"],
                        "command": causal_event["payload"]["action"],
                    }
                if any(
                    proposal.payload.get(key) != value
                    for key, value in expected_action.items()
                ):
                    raise BridgeValidationError(
                        "persisted proposal action does not match its causal event"
                    )
                if observation.message_type == "time_advance":
                    time_observation_ticks.setdefault(observation.message_id, set()).add(
                        int(causal_event["tick"])
                    )
                else:
                    expected_root = f"bridge:{observation.message_id}"

                    def has_expected_root(trace: Mapping[str, Any]) -> bool:
                        return trace["event"].get("root_input") == expected_root or any(
                            has_expected_root(cause) for cause in trace.get("causes", ())
                        )

                    if not has_expected_root(causal_trace):
                        raise BridgeValidationError(
                            "persisted proposal causal event does not originate from its observation"
                        )
            except (KeyError, TypeError, ValueError) as error:
                if isinstance(error, BridgeValidationError):
                    raise
                raise BridgeValidationError(
                    "persisted proposal causal event reference is invalid"
                ) from error
        time_observations = sorted(
            (
                observation
                for observation, _proposals in self._observations.values()
                if observation.message_type == "time_advance"
            ),
            key=lambda item: item.sequence,
        )
        time_actors = {item.actor_id for item in time_observations}
        if len(time_actors) > 1:
            raise BridgeValidationError(
                "persisted time observations have an ambiguous cross-actor order"
            )
        initial_tick = self.world.tick - len(time_observations)
        if initial_tick < 0:
            raise BridgeValidationError("persisted time observation count exceeds world tick")
        for offset, observation in enumerate(time_observations, start=1):
            ticks = time_observation_ticks.get(observation.message_id, set())
            expected_tick = initial_tick + offset
            if len(ticks) > 1 or (ticks and ticks != {expected_tick}):
                raise BridgeValidationError(
                    "persisted proposal causal tick does not match its exact observation"
                )
        if migrated:
            self._persist()

    def _proposal(self, event: Event, correlation_id: str, payload: Mapping[str, Any]) -> Envelope:
        sequence = self._proposal_sequence.get(event.actor, 0) + 1
        self._proposal_sequence[event.actor] = sequence
        unsigned = Envelope(
            BRIDGE_SCHEMA_VERSION,
            f"world-proposal:{correlation_id}:{event.id}",
            correlation_id,
            sequence,
            event.actor,
            "world_action_proposed",
            {**dict(payload), "causal_event_id": event.id},
        )
        proposal = Envelope.from_dict(
            {**unsigned.to_dict(), "payload": {**unsigned.to_dict()["payload"], "origin_proof": _origin_proof(unsigned, self._proposal_origin_key)}}
        )
        self._pending[proposal.message_id] = proposal
        return proposal

    def _validate_observation(self, observation: Envelope) -> None:
        if observation.message_type not in {"time_advance", "npc_request"}:
            raise BridgeValidationError(f"unsupported engine observation: {observation.message_type}")
        if observation.actor_id not in self.world.actors:
            raise BridgeValidationError(f"unknown engine actor: {observation.actor_id}")
        if observation.message_type == "time_advance":
            if self.world.actors[observation.actor_id].category != "bio" or observation.payload != {"ticks": 1}:
                raise BridgeValidationError("time advance requires one tick from a Bio observation")
        else:
            target_id = observation.payload.get("target_id")
            action = observation.payload.get("action")
            if (
                self.world.actors[observation.actor_id].category != "bio"
                or not isinstance(target_id, str)
                or target_id not in self.world.actors
                or self.world.actors[target_id].category == "bio"
                or not isinstance(action, str)
                or action not in FEASIBLE_REQUEST_ACTIONS
            ):
                raise BridgeValidationError("npc request has invalid Bio, target, or action")

    def _apply_observation(self, observation: Envelope) -> tuple[Envelope, ...]:
        proposals: list[Envelope] = []
        if observation.message_type == "time_advance":
            events = self.world.advance(1)
            for event in events:
                if event.event_type == "routine_action":
                    proposals.append(
                        self._proposal(
                            event,
                            observation.message_id,
                            {
                                "action_type": "routine_move",
                                "command": event.payload["action"],
                                "destination": self.role_stations[event.payload["role"]],
                            },
                        )
                    )
        else:
            target_id = observation.payload.get("target_id")
            action = observation.payload.get("action")
            decision = self.world.decide_request(
                observation.actor_id,
                target_id,
                action,
                root_input=f"bridge:{observation.message_id}",
            )
            if decision.event_type == "proposal_rejected":
                raise BridgeValidationError(f"World OS rejected npc request: {decision.payload['reason']}")
            proposals.append(
                self._proposal(
                    decision,
                    observation.message_id,
                    {
                        "action_type": decision.event_type,
                        "command": action,
                    },
                )
            )
        return tuple(proposals)

    def ingest_engine_observation(self, observation: Envelope) -> tuple[Envelope, ...]:
        existing = self._observations.get(observation.message_id)
        if existing:
            if existing[0].digest() != observation.digest():
                raise BridgeValidationError("engine message id was reused with different content")
            return self._delivery_results.get(observation.message_id, existing[1])
        buffered = self._buffered_observations.get(observation.message_id)
        if buffered:
            if buffered.digest() != observation.digest():
                raise BridgeValidationError("engine message id was reused with different content")
            return ()
        self._validate_observation(observation)
        expected_sequence = self._last_engine_sequence.get(observation.actor_id, 0) + 1
        if observation.sequence < expected_sequence:
            raise BridgeValidationError("stale engine observation")
        if observation.sequence > expected_sequence:
            if any(
                item.actor_id == observation.actor_id and item.sequence == observation.sequence
                for item in self._buffered_observations.values()
            ):
                raise BridgeValidationError("engine observation sequence was reused by a different message")
            self._buffered_observations[observation.message_id] = observation
            self._persist()
            return ()

        ordered = [observation]
        next_sequence = observation.sequence + 1
        while True:
            next_observation = next(
                (
                    item
                    for item in self._buffered_observations.values()
                    if item.actor_id == observation.actor_id and item.sequence == next_sequence
                ),
                None,
            )
            if next_observation is None:
                break
            ordered.append(next_observation)
            next_sequence += 1

        delivered: list[Envelope] = []
        for item in ordered:
            proposals = self._apply_observation(item)
            self._last_engine_sequence[item.actor_id] = item.sequence
            self._observations[item.message_id] = (item, proposals)
            self._buffered_observations.pop(item.message_id, None)
            delivered.extend(proposals)
        result = tuple(delivered)
        self._delivery_results[observation.message_id] = result
        self._delivery_observations[observation.message_id] = tuple(item.message_id for item in ordered)
        self._persist()
        return result

    def _validate_engine_decision_envelope(self, decision: EngineDecision) -> Envelope:
        if not _has_valid_authority_proof(
            decision.outcome,
            decision.engine_event,
            decision.outcome.payload["authority_proof"],
            self._engine_authority_public_key,
        ):
            raise BridgeValidationError("engine decision does not have a valid authority proof")
        proposal_id = decision.outcome.correlation_id
        proposal = self._pending.get(proposal_id)
        if proposal is None:
            raise BridgeValidationError("engine outcome does not correlate to a pending World OS proposal")
        if decision.outcome.actor_id != proposal.actor_id:
            raise BridgeValidationError("engine outcome actor does not match its proposal")
        if decision.status == "applied":
            engine_event = decision.engine_event
            expected = {
                key: proposal.payload.get(key)
                for key in ("action_type", "command", "destination")
            }
            actual = {key: engine_event.payload.get(key) for key in expected}
            if actual != expected:
                raise BridgeValidationError("authoritative engine event does not match its World OS proposal")
            existing_event_digest = self._engine_events.get(engine_event.message_id)
            if existing_event_digest is not None and existing_event_digest != engine_event.digest():
                raise BridgeValidationError("authoritative engine event id was reused with different content")
            state_version = int(engine_event.payload["state_version"])
            for prior_version, prior_digest in enumerate(
                engine_event.payload["prior_event_digests"], start=1
            ):
                existing_prior_digest = self._engine_versions.get(prior_version)
                if existing_prior_digest is not None and existing_prior_digest != prior_digest:
                    raise BridgeValidationError(
                        "authoritative engine event lineage conflicts with recorded history"
                    )
            existing_version_digest = self._engine_versions.get(state_version)
            if existing_version_digest is not None and existing_version_digest != engine_event.digest():
                raise BridgeValidationError(
                    "authoritative engine state version was reused by a different event"
                )
        return proposal

    def _record_engine_decision(self, decision: EngineDecision) -> None:
        proposal = self._validate_engine_decision_envelope(decision)
        expected_sequence = len(self._decisions) + 1
        if decision.outcome.sequence != expected_sequence:
            raise BridgeValidationError("engine outcome ordering is not contiguous")
        if any(
            item.outcome.correlation_id == proposal.message_id
            for item in self._decisions.values()
        ):
            raise BridgeValidationError("World OS proposal already has an engine outcome")
        if self._decisions:
            prior = max(self._decisions.values(), key=lambda item: item.outcome.sequence)
            state_is_possible = _state_advance_is_possible(
                prior.outcome.sequence,
                int(prior.outcome.payload["state_version"]),
                decision,
            )
        else:
            state_is_possible = _state_advance_is_possible(0, 0, decision)
        if not state_is_possible:
            raise BridgeValidationError(
                "engine outcome state version conflicts with decision ordering"
            )
        if decision.status == "applied":
            engine_event = decision.engine_event
            existing_event_digest = self._engine_events.get(engine_event.message_id)
            if existing_event_digest is not None and existing_event_digest != engine_event.digest():
                raise BridgeValidationError("authoritative engine event id was reused with different content")
            state_version = int(engine_event.payload["state_version"])
            for prior_version, prior_digest in enumerate(engine_event.payload["prior_event_digests"], start=1):
                existing_prior_digest = self._engine_versions.get(prior_version)
                if existing_prior_digest is not None and existing_prior_digest != prior_digest:
                    raise BridgeValidationError("authoritative engine event lineage conflicts with recorded history")
            existing_version_digest = self._engine_versions.get(state_version)
            if existing_version_digest is not None and existing_version_digest != engine_event.digest():
                raise BridgeValidationError("authoritative engine state version was reused by a different event")
        self._decisions[decision.outcome.message_id] = decision
        if decision.engine_event is not None:
            for prior_version, prior_digest in enumerate(
                decision.engine_event.payload["prior_event_digests"],
                start=1,
            ):
                self._engine_versions[prior_version] = prior_digest
            self._engine_events[decision.engine_event.message_id] = decision.engine_event.digest()
            self._engine_versions[int(decision.engine_event.payload["state_version"])] = decision.engine_event.digest()

    def receive_engine_decision(self, decision: EngineDecision) -> None:
        proposal = self._validate_engine_decision_envelope(decision)
        existing = self._decisions.get(decision.outcome.message_id)
        buffered_existing = self._buffered_decisions.get(decision.outcome.message_id)
        if existing is not None or buffered_existing is not None:
            recorded = existing if existing is not None else buffered_existing
            if recorded.digest() != decision.digest():
                raise BridgeValidationError("engine outcome id was reused with different content")
            return

        all_decisions = (*self._decisions.values(), *self._buffered_decisions.values())
        if any(item.outcome.sequence == decision.outcome.sequence for item in all_decisions):
            raise BridgeValidationError("engine outcome sequence was reused by a different decision")
        if any(
            item.outcome.correlation_id == proposal.message_id
            for item in self._decisions.values()
        ):
            raise BridgeValidationError("World OS proposal already has an engine outcome")
        superseded_buffered_ids: list[str] = []
        for message_id, buffered in tuple(self._buffered_decisions.items()):
            if buffered.outcome.correlation_id != proposal.message_id:
                continue
            if buffered.outcome.sequence < decision.outcome.sequence:
                raise BridgeValidationError("World OS proposal already has a buffered engine outcome")
            superseded_buffered_ids.append(message_id)
        expected_sequence = len(self._decisions) + 1
        if decision.outcome.sequence < expected_sequence:
            raise BridgeValidationError("engine outcome ordering slot is already committed")
        if decision.outcome.sequence > expected_sequence:
            if superseded_buffered_ids:
                raise BridgeValidationError("World OS proposal already has a later buffered engine outcome")
            self._buffered_decisions[decision.outcome.message_id] = decision
            self._persist()
            return

        self._record_engine_decision(decision)
        for message_id in superseded_buffered_ids:
            self._buffered_decisions.pop(message_id)
        while True:
            next_sequence = len(self._decisions) + 1
            buffered = next(
                (
                    item
                    for item in self._buffered_decisions.values()
                    if item.outcome.sequence == next_sequence
                ),
                None,
            )
            if buffered is None:
                break
            self._buffered_decisions.pop(buffered.outcome.message_id)
            try:
                self._record_engine_decision(buffered)
            except BridgeValidationError:
                break
        self._persist()

    def evidence(self) -> dict[str, Any]:
        return {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "observations": [
                {
                    "envelope": observation.to_dict(),
                    "digest": observation.digest(),
                    "proposal_ids": [item.message_id for item in proposals],
                }
                for _message_id, (observation, proposals) in sorted(self._observations.items())
            ],
            "proposals": [item.to_dict() for item in sorted(self._pending.values(), key=lambda item: item.message_id)],
            "decisions": [
                item.to_dict() for item in sorted(self._decisions.values(), key=lambda item: item.outcome.message_id)
            ],
            "buffered_decisions": [
                item.to_dict()
                for item in sorted(
                    self._buffered_decisions.values(), key=lambda item: item.outcome.message_id
                )
            ],
            "causal_traces": {
                item.message_id: self.world.trace(str(item.payload["causal_event_id"]))
                for item in sorted(self._pending.values(), key=lambda item: item.message_id)
            },
            "world_digest": self.world.state_digest(),
        }


class EngineAuthority:
    """Reference engine-side validator; Unreal must preserve these contract semantics."""

    def __init__(
        self,
        actor_positions: Mapping[str, str],
        permissions: Mapping[str, Iterable[str]],
        destinations: Iterable[str],
        proposal_origin_key: bytes | str,
        authority_signing_key: bytes | Ed25519PrivateKey,
        blocked_paths: Iterable[tuple[str, str]] = (),
    ):
        self._initial_positions = dict(actor_positions)
        self._positions = dict(self._initial_positions)
        self._permissions = {actor: frozenset(actions) for actor, actions in permissions.items()}
        if set(self._positions) != set(self._permissions):
            raise BridgeValidationError("every engine actor requires an explicit permission set")
        self._destinations = frozenset(destinations)
        self._blocked_paths = frozenset(blocked_paths)
        self._proposal_origin_key = _proposal_key(proposal_origin_key)
        self._authority_signing_key = _authority_private_key(authority_signing_key)
        self._authority_verification_key = self._authority_signing_key.public_key()
        self._last_action: dict[str, dict[str, Any]] = {}
        self._last_sequence: dict[str, int] = {}
        self._processed: dict[str, tuple[str, EngineDecision, Envelope]] = {}
        self._buffered_proposals: dict[str, Envelope] = {}
        self._response_batches: dict[str, tuple[str, ...]] = {}
        self._message_conflicts: list[dict[str, str]] = []
        self._event_history: list[str] = []
        self._decision_sequence = 0
        self._state_version = 0

    def state(self) -> dict[str, Any]:
        return {
            "state_version": self._state_version,
            "positions": dict(sorted(self._positions.items())),
            "last_action": {actor: dict(value) for actor, value in sorted(self._last_action.items())},
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "schema_version": ENGINE_AUTHORITY_SCHEMA_VERSION,
            "initial_positions": dict(sorted(self._initial_positions.items())),
            "positions": dict(sorted(self._positions.items())),
            "permissions": {actor: sorted(actions) for actor, actions in sorted(self._permissions.items())},
            "destinations": sorted(self._destinations),
            "blocked_paths": [list(item) for item in sorted(self._blocked_paths)],
            "last_action": {actor: dict(value) for actor, value in sorted(self._last_action.items())},
            "last_sequence": dict(sorted(self._last_sequence.items())),
            "processed": [
                {
                    "message_id": message_id,
                    "proposal_digest": proposal_digest,
                    "proposal": proposal.to_dict(),
                    "decision": decision.to_dict(),
                }
                for message_id, (proposal_digest, decision, proposal) in sorted(self._processed.items())
            ],
            "buffered_proposals": [
                proposal.to_dict()
                for proposal in sorted(self._buffered_proposals.values(), key=lambda item: item.message_id)
            ],
            "response_batches": [
                {"message_id": message_id, "decision_ids": list(decision_ids)}
                for message_id, decision_ids in sorted(self._response_batches.items())
            ],
            "message_conflicts": [dict(item) for item in self._message_conflicts],
            "event_history": list(self._event_history),
            "decision_sequence": self._decision_sequence,
            "state_version": self._state_version,
        }

    def snapshot_digest(self) -> str:
        return hashlib.sha256(_canonical(self.snapshot()).encode("utf-8")).hexdigest()

    def save(self, path: Path) -> None:
        snapshot = self.snapshot()
        envelope = {
            "format": "jarvis-world-h2-engine",
            "state": snapshot,
            "digest": hashlib.sha256(_canonical(snapshot).encode("utf-8")).hexdigest(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_canonical(envelope) + "\n", encoding="utf-8")

    @classmethod
    def load(
        cls,
        path: Path,
        proposal_origin_key: bytes | str,
        authority_signing_key: bytes | Ed25519PrivateKey,
        *,
        expected_snapshot_digest: str,
    ) -> EngineAuthority:
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            state = envelope["state"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise BridgeValidationError(f"invalid persisted engine authority: {error}") from error
        expected_fields = {
            "schema_version", "initial_positions", "positions", "permissions", "destinations", "blocked_paths",
            "last_action", "last_sequence", "processed", "message_conflicts",
            "event_history", "decision_sequence", "state_version", "buffered_proposals", "response_batches",
        }
        digest = hashlib.sha256(_canonical(state).encode("utf-8")).hexdigest()
        version_one_fields = expected_fields - {"buffered_proposals", "response_batches"}
        version_two_fields = expected_fields - {"response_batches"}
        previous_shape = (
            isinstance(state, dict)
            and (
                (set(state) == version_one_fields and _is_supported_schema_version(state.get("schema_version")))
                or (
                    set(state) == version_two_fields
                    and _is_exact_version(state.get("schema_version"), 2)
                )
                or (set(state) == expected_fields and _is_exact_version(state.get("schema_version"), 3))
            )
        )
        if (
            envelope.get("format") != "jarvis-world-h2-engine"
            or not isinstance(state, dict)
            or not (
                previous_shape
                or (
                    set(state) == expected_fields
                    and _is_supported_authority_schema_version(state.get("schema_version"))
                )
            )
        ):
            raise BridgeValidationError("incompatible persisted engine authority")
        if not expected_snapshot_digest or digest != expected_snapshot_digest or envelope.get("digest") != digest:
            raise BridgeValidationError("persisted engine authority does not match the trusted snapshot boundary")
        replay_legacy_v1 = previous_shape and state["schema_version"] == BRIDGE_SCHEMA_VERSION
        if previous_shape:
            try:
                legacy_version = state["schema_version"]
                proposal_key = _proposal_key(proposal_origin_key)
                signing_key = _authority_private_key(authority_signing_key)
                for item in state["processed"]:
                    proof = item["decision"]["outcome"]["payload"]["authority_proof"]
                    if isinstance(proof, str) and re.fullmatch(r"[0-9a-f]{64}", proof):
                        item["decision"]["outcome"]["payload"]["authority_proof"] = "0" * 128
                        decision = EngineDecision.from_dict(item["decision"])
                        expected_legacy_proof = hmac.new(
                            proposal_key,
                            _authority_material(decision.outcome, decision.engine_event),
                            hashlib.sha256,
                        ).hexdigest()
                        if not hmac.compare_digest(proof, expected_legacy_proof):
                            raise BridgeValidationError(
                                "persisted legacy engine authority proof is invalid"
                            )
                        item["decision"]["outcome"]["payload"]["authority_proof"] = _authority_proof(
                            decision.outcome,
                            decision.engine_event,
                            signing_key,
                        )
                    elif isinstance(proof, str) and re.fullmatch(r"[0-9a-f]{128}", proof):
                        decision = EngineDecision.from_dict(item["decision"])
                        if not _has_valid_authority_proof(
                            decision.outcome,
                            decision.engine_event,
                            proof,
                            signing_key.public_key(),
                        ):
                            raise BridgeValidationError(
                                "persisted engine decision authority proof is invalid"
                            )
                    else:
                        raise BridgeValidationError(
                            "persisted legacy engine authority proof is malformed"
                        )
                if state["schema_version"] == 2:
                    processed_actors = [item["proposal"]["actor_id"] for item in state["processed"]]
                    if len(processed_actors) != len(set(processed_actors)):
                        raise BridgeValidationError(
                            "persisted v2 engine authority has ambiguous response-batch history"
                        )
                if legacy_version < 3:
                    response_batches = [
                        {"message_id": item["message_id"], "decision_ids": [item["message_id"]]}
                        for item in state["processed"]
                    ]
                    response_batches.sort(key=lambda item: item["message_id"])
                else:
                    response_batches = state["response_batches"]
            except (BridgeValidationError, KeyError, TypeError, ValueError, AttributeError) as error:
                raise BridgeValidationError(
                    f"persisted engine authority is malformed: {error}"
                ) from error
            state = {
                **state,
                "schema_version": ENGINE_AUTHORITY_SCHEMA_VERSION,
                "buffered_proposals": state.get("buffered_proposals", []),
                "response_batches": response_batches,
            }
        try:
            authority = cls(
                state["initial_positions"],
                state["permissions"],
                state["destinations"],
                proposal_origin_key,
                authority_signing_key,
                (tuple(item) for item in state["blocked_paths"]),
            )
            authority._positions = {str(actor): str(position) for actor, position in state["positions"].items()}
            if set(authority._positions) != set(authority._initial_positions):
                raise BridgeValidationError("persisted engine position identities do not match initial positions")
            authority._last_action = {str(actor): dict(action) for actor, action in state["last_action"].items()}
            authority._last_sequence = _counter_map(state["last_sequence"], "last_sequence", minimum=1)
            authority._processed = {}
            for item in state["processed"]:
                decision = EngineDecision.from_dict(item["decision"])
                message_id = str(item["message_id"])
                if message_id in authority._processed:
                    raise BridgeValidationError("persisted engine proposal identity is duplicated")
                if decision.outcome.correlation_id != message_id:
                    raise BridgeValidationError("persisted engine decision correlation is invalid")
                if not _has_valid_authority_proof(
                    decision.outcome,
                    decision.engine_event,
                    decision.outcome.payload["authority_proof"],
                    authority._authority_verification_key,
                ):
                    raise BridgeValidationError("persisted engine decision authority proof is invalid")
                proposal = Envelope.from_dict(item["proposal"])
                proposal_digest = str(item["proposal_digest"])
                if proposal.message_id != message_id or proposal.digest() != proposal_digest:
                    raise BridgeValidationError("persisted engine proposal identity or digest is invalid")
                if not authority._has_valid_origin(proposal):
                    raise BridgeValidationError("persisted engine proposal origin proof is invalid")
                if decision.outcome.actor_id != proposal.actor_id:
                    raise BridgeValidationError("persisted engine decision actor is invalid")
                authority._processed[message_id] = (
                    proposal_digest,
                    decision,
                    proposal,
                )
            authority._buffered_proposals = {}
            for item in state["buffered_proposals"]:
                proposal = Envelope.from_dict(item)
                if (
                    proposal.message_id in authority._buffered_proposals
                    or proposal.message_id in authority._processed
                ):
                    raise BridgeValidationError("persisted buffered engine proposal identity is duplicated")
                if not authority._has_valid_origin(proposal):
                    raise BridgeValidationError("persisted buffered engine proposal origin proof is invalid")
                authority._buffered_proposals[proposal.message_id] = proposal
            authority._response_batches = {}
            for item in state["response_batches"]:
                message_id = str(item["message_id"])
                decision_ids = tuple(str(value) for value in item["decision_ids"])
                if message_id in authority._response_batches:
                    raise BridgeValidationError("persisted engine response batch identity is duplicated")
                authority._response_batches[message_id] = decision_ids
            authority._message_conflicts = [dict(item) for item in state["message_conflicts"]]
            authority._event_history = [str(digest) for digest in state["event_history"]]
            authority._decision_sequence = _counter(state["decision_sequence"], "decision_sequence")
            authority._state_version = _counter(state["state_version"], "state_version")
        except (BridgeValidationError, KeyError, TypeError, ValueError, AttributeError) as error:
            raise BridgeValidationError(f"persisted engine authority is malformed: {error}") from error
        if authority.snapshot() != state:
            raise BridgeValidationError("persisted engine authority does not round-trip exactly")
        if any(not re.fullmatch(r"[0-9a-f]{64}", digest) for digest in authority._event_history):
            raise BridgeValidationError("persisted engine event history contains an invalid digest")
        applied_decisions = [
            (decision, proposal)
            for _proposal_digest, decision, proposal in authority._processed.values()
            if decision.engine_event is not None
        ]
        for decision, proposal in applied_decisions:
            expected_action = {
                key: proposal.payload.get(key)
                for key in ("action_type", "command", "destination")
            }
            actual_action = {
                key: decision.engine_event.payload.get(key)
                for key in expected_action
            }
            if actual_action != expected_action:
                raise BridgeValidationError("persisted applied decision does not match its authenticated proposal")
        applied_versions = [
            int(decision.engine_event.payload["state_version"])
            for decision, _proposal in applied_decisions
        ]
        if len(applied_versions) != len(set(applied_versions)):
            raise BridgeValidationError("persisted engine state version is duplicated")
        applied_by_version = {
            int(decision.engine_event.payload["state_version"]): decision.engine_event.digest()
            for decision, _proposal in applied_decisions
        }
        expected_history = [applied_by_version.get(version) for version in range(1, authority._state_version + 1)]
        if authority._state_version != len(authority._event_history) or expected_history != authority._event_history:
            raise BridgeValidationError("persisted engine state version does not match its event history")
        decision_sequences = sorted(
            decision.outcome.sequence
            for _digest, decision, _proposal in authority._processed.values()
        )
        if decision_sequences != list(range(1, authority._decision_sequence + 1)):
            raise BridgeValidationError("persisted engine decision ordering is not unique and contiguous")
        processed_sequences: dict[str, list[int]] = {}
        for _digest, _decision, proposal in authority._processed.values():
            if proposal.actor_id in authority._positions:
                processed_sequences.setdefault(proposal.actor_id, []).append(proposal.sequence)
        expected_last_sequence = {
            actor_id: max(sequences)
            for actor_id, sequences in processed_sequences.items()
        }
        if authority._last_sequence != expected_last_sequence:
            raise BridgeValidationError("persisted engine proposal high-water marks do not match processed decisions")
        if set(authority._response_batches) != set(authority._processed):
            raise BridgeValidationError("persisted engine response batches do not cover processed proposals")
        for message_id, decision_ids in authority._response_batches.items():
            if not decision_ids or decision_ids[0] != message_id or any(
                decision_id not in authority._processed for decision_id in decision_ids
            ):
                raise BridgeValidationError("persisted engine response batch is invalid")
            decisions = [authority._processed[decision_id][1] for decision_id in decision_ids]
            proposals = [authority._processed[decision_id][2] for decision_id in decision_ids]
            if any(
                decisions[index].outcome.sequence != decisions[index - 1].outcome.sequence + 1
                or proposals[index].actor_id != proposals[index - 1].actor_id
                or proposals[index].sequence != proposals[index - 1].sequence + 1
                for index in range(1, len(decisions))
            ):
                raise BridgeValidationError("persisted engine response batch ordering is invalid")
        buffered_slots: set[tuple[str, int]] = set()
        for proposal in authority._buffered_proposals.values():
            if proposal.actor_id not in authority._positions:
                raise BridgeValidationError("persisted buffered engine proposal has an unknown actor")
            if proposal.sequence <= authority._last_sequence.get(proposal.actor_id, 0) + 1:
                raise BridgeValidationError("persisted buffered engine proposal does not follow a real gap")
            slot = (proposal.actor_id, proposal.sequence)
            if slot in buffered_slots:
                raise BridgeValidationError("persisted buffered engine proposal ordering slot is duplicated")
            buffered_slots.add(slot)
        expected_last_action: dict[str, dict[str, Any]] = {}
        expected_positions = dict(authority._initial_positions)
        for decision, _proposal in sorted(
            applied_decisions,
            key=lambda item: int(item[0].engine_event.payload["state_version"]),
        ):
            event = decision.engine_event
            expected_last_action[event.actor_id] = {
                "action_type": event.payload["action_type"],
                "command": event.payload["command"],
            }
            if isinstance(event.payload["destination"], str):
                expected_positions[event.actor_id] = event.payload["destination"]
        if authority._last_action != expected_last_action:
            raise BridgeValidationError("persisted engine last actions do not match applied event history")
        if authority._positions != expected_positions:
            raise BridgeValidationError("persisted engine positions do not match applied event history")
        replay = cls(
            authority._initial_positions,
            authority._permissions,
            authority._destinations,
            proposal_origin_key,
            authority_signing_key,
            authority._blocked_paths,
        )
        for _proposal_digest, decision, proposal in sorted(
            authority._processed.values(),
            key=lambda item: item[1].outcome.sequence,
        ):
            replayed = (
                (replay._process_proposal(proposal),)
                if replay_legacy_v1
                else replay.validate_and_apply(proposal)
            )
            if replayed != (decision,):
                raise BridgeValidationError("persisted engine decisions do not match replayed policy")
        if (
            replay._positions != authority._positions
            or replay._last_action != authority._last_action
            or replay._last_sequence != authority._last_sequence
            or replay._event_history != authority._event_history
            or replay._decision_sequence != authority._decision_sequence
            or replay._state_version != authority._state_version
        ):
            raise BridgeValidationError("persisted engine state does not match replayed policy")
        return authority

    def conflicts(self) -> tuple[dict[str, str], ...]:
        return tuple(dict(item) for item in self._message_conflicts)

    def _decision(
        self,
        proposal: Envelope,
        status: str,
        reason: str,
        engine_event: Envelope | None = None,
    ) -> EngineDecision:
        self._decision_sequence += 1
        unsigned_outcome = Envelope(
            BRIDGE_SCHEMA_VERSION,
            f"engine-outcome:{proposal.message_id}",
            proposal.message_id,
            self._decision_sequence,
            proposal.actor_id,
            "engine_proposal_outcome",
            {
                "engine_event_id": engine_event.message_id if engine_event else None,
                "reason": reason,
                "state_version": self._state_version,
                "status": status,
            },
        )
        outcome = Envelope.from_dict(
            {
                **unsigned_outcome.to_dict(),
                "payload": {
                    **unsigned_outcome.to_dict()["payload"],
                    "authority_proof": _authority_proof(
                        unsigned_outcome,
                        engine_event,
                        self._authority_signing_key,
                    ),
                },
            }
        )
        return EngineDecision(status, outcome, engine_event)

    def validate_and_apply(self, proposal: Envelope) -> tuple[EngineDecision, ...]:
        authenticated = self._has_valid_origin(proposal)
        existing = self._processed.get(proposal.message_id)
        if not authenticated:
            if existing:
                conflict = {
                    "message_id": proposal.message_id,
                    "canonical_digest": existing[0],
                    "received_digest": proposal.digest(),
                }
                if conflict not in self._message_conflicts:
                    self._message_conflicts.append(conflict)
                return self._response_batch(proposal.message_id)
            raise BridgeValidationError("proposal does not have a valid World OS origin proof")
        if existing:
            if existing[0] == proposal.digest():
                return self._response_batch(proposal.message_id)
            conflict = {
                "message_id": proposal.message_id,
                "canonical_digest": existing[0],
                "received_digest": proposal.digest(),
            }
            if conflict not in self._message_conflicts:
                self._message_conflicts.append(conflict)
            return self._response_batch(proposal.message_id)

        buffered = self._buffered_proposals.get(proposal.message_id)
        if buffered:
            if buffered.digest() == proposal.digest():
                return ()
            raise BridgeValidationError("buffered engine proposal id was reused with different content")

        if proposal.actor_id in self._positions:
            expected_sequence = self._last_sequence.get(proposal.actor_id, 0) + 1
            if proposal.sequence > expected_sequence:
                if any(
                    item.actor_id == proposal.actor_id and item.sequence == proposal.sequence
                    for item in self._buffered_proposals.values()
                ):
                    raise BridgeValidationError("buffered engine proposal sequence was reused")
                self._buffered_proposals[proposal.message_id] = proposal
                return ()

        decision = self._process_proposal(proposal)
        drained: tuple[EngineDecision, ...] = ()
        if proposal.actor_id in self._positions:
            drained = self._drain_buffer(proposal.actor_id)
        decisions = (decision, *drained)
        self._response_batches[proposal.message_id] = tuple(
            item.outcome.correlation_id for item in decisions
        )
        for item in drained:
            self._response_batches.setdefault(
                item.outcome.correlation_id,
                (item.outcome.correlation_id,),
            )
        return decisions

    def _response_batch(self, message_id: str) -> tuple[EngineDecision, ...]:
        return tuple(self._processed[decision_id][1] for decision_id in self._response_batches[message_id])

    def _drain_buffer(self, actor_id: str) -> tuple[EngineDecision, ...]:
        decisions: list[EngineDecision] = []
        while True:
            expected_sequence = self._last_sequence.get(actor_id, 0) + 1
            pending = next(
                (
                    item
                    for item in self._buffered_proposals.values()
                    if item.actor_id == actor_id and item.sequence == expected_sequence
                ),
                None,
            )
            if pending is None:
                return tuple(decisions)
            del self._buffered_proposals[pending.message_id]
            decisions.append(self._process_proposal(pending))

    def _process_proposal(self, proposal: Envelope) -> EngineDecision:
        reason = self._validate(proposal)
        if reason:
            if proposal.actor_id in self._positions and proposal.sequence > self._last_sequence.get(proposal.actor_id, 0):
                self._last_sequence[proposal.actor_id] = proposal.sequence
            decision = self._decision(proposal, "rejected", reason)
            self._processed[proposal.message_id] = (
                proposal.digest(), decision, proposal
            )
            return decision

        action_type = str(proposal.payload["action_type"])
        command = str(proposal.payload["command"])
        destination = proposal.payload.get("destination")
        self._state_version += 1
        if isinstance(destination, str):
            self._positions[proposal.actor_id] = destination
        self._last_action[proposal.actor_id] = {"action_type": action_type, "command": command}
        self._last_sequence[proposal.actor_id] = proposal.sequence
        engine_event = Envelope(
            BRIDGE_SCHEMA_VERSION,
            f"engine-event:{proposal.message_id}",
            proposal.message_id,
            self._state_version,
            proposal.actor_id,
            "engine_action_applied",
            {
                "action_type": action_type,
                "command": command,
                "destination": destination,
                "prior_event_digests": list(self._event_history),
                "state_version": self._state_version,
            },
        )
        self._event_history.append(engine_event.digest())
        decision = self._decision(proposal, "applied", "applied", engine_event)
        self._processed[proposal.message_id] = (
            proposal.digest(), decision, proposal
        )
        return decision

    def _validate(self, proposal: Envelope) -> str | None:
        if proposal.message_type != "world_action_proposed":
            return "unsupported_message_type"
        if proposal.actor_id not in self._positions:
            return "unknown_identity"
        if proposal.sequence <= self._last_sequence.get(proposal.actor_id, 0):
            return "stale_sequence"
        action_type = proposal.payload.get("action_type")
        command = proposal.payload.get("command")
        causal_event_id = proposal.payload.get("causal_event_id")
        origin_proof = proposal.payload.get("origin_proof")
        if not all(isinstance(value, str) and value for value in (action_type, command, causal_event_id, origin_proof)):
            return "malformed_payload"
        if not hmac.compare_digest(origin_proof, _origin_proof(proposal, self._proposal_origin_key)):
            return "untrusted_world_os_origin"
        if action_type not in self._permissions[proposal.actor_id]:
            return "permission_denied"
        destination = proposal.payload.get("destination")
        if action_type == "routine_move":
            if not isinstance(destination, str) or destination not in self._destinations:
                return "physically_impossible"
            if (proposal.actor_id, destination) in self._blocked_paths:
                return "physically_impossible"
        elif destination is not None:
            return "malformed_payload"
        return None

    def _has_valid_origin(self, proposal: Envelope) -> bool:
        proof = proposal.payload.get("origin_proof")
        return isinstance(proof, str) and hmac.compare_digest(
            proof,
            _origin_proof(proposal, self._proposal_origin_key),
        )
