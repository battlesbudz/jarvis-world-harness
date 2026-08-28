from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .runtime import FEASIBLE_REQUEST_ACTIONS, Event, World


BRIDGE_SCHEMA_VERSION = 1
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*$")


class BridgeValidationError(ValueError):
    pass


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
        if self.schema_version != BRIDGE_SCHEMA_VERSION:
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
        if self.status == "applied":
            if self.engine_event is None or self.engine_event.message_type != "engine_action_applied":
                raise BridgeValidationError("applied decision requires an authoritative engine event")
            if self.engine_event.correlation_id != self.outcome.correlation_id:
                raise BridgeValidationError("engine event and outcome correlation do not match")
            if self.outcome.payload.get("engine_event_id") != self.engine_event.message_id:
                raise BridgeValidationError("engine outcome does not reference its authoritative event")
            if self.engine_event.actor_id != self.outcome.actor_id:
                raise BridgeValidationError("engine event and outcome actors do not match")
            if self.engine_event.payload.get("state_version") != self.outcome.payload.get("state_version"):
                raise BridgeValidationError("engine event and outcome state versions do not match")
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


class WorldOSBridge:
    """Idempotent adapter from authoritative engine observations to World OS proposals."""

    def __init__(self, world: World, role_stations: Mapping[str, str]):
        self.world = world
        self.role_stations = dict(role_stations)
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

    def _proposal(self, event: Event, correlation_id: str, payload: Mapping[str, Any]) -> Envelope:
        sequence = self._proposal_sequence.get(event.actor, 0) + 1
        self._proposal_sequence[event.actor] = sequence
        proposal = Envelope(
            BRIDGE_SCHEMA_VERSION,
            f"world-proposal:{correlation_id}:{event.id}",
            correlation_id,
            sequence,
            event.actor,
            "world_action_proposed",
            {**dict(payload), "causal_event_id": event.id},
        )
        self._pending[proposal.message_id] = proposal
        return proposal

    def ingest_engine_observation(self, observation: Envelope) -> tuple[Envelope, ...]:
        existing = self._observations.get(observation.message_id)
        if existing:
            if existing[0].digest() != observation.digest():
                raise BridgeValidationError("engine message id was reused with different content")
            return existing[1]
        if observation.message_type not in {"time_advance", "npc_request"}:
            raise BridgeValidationError(f"unsupported engine observation: {observation.message_type}")
        if observation.actor_id not in self.world.actors:
            raise BridgeValidationError(f"unknown engine actor: {observation.actor_id}")
        previous_sequence = self._last_engine_sequence.get(observation.actor_id, 0)
        if observation.sequence <= previous_sequence:
            raise BridgeValidationError("stale engine observation")

        proposals: list[Envelope] = []
        if observation.message_type == "time_advance":
            if self.world.actors[observation.actor_id].category != "bio" or observation.payload != {"ticks": 1}:
                raise BridgeValidationError("time advance requires one tick from a Bio observation")
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
            if (
                self.world.actors[observation.actor_id].category != "bio"
                or not isinstance(target_id, str)
                or target_id not in self.world.actors
                or self.world.actors[target_id].category == "bio"
                or not isinstance(action, str)
                or action not in FEASIBLE_REQUEST_ACTIONS
            ):
                raise BridgeValidationError("npc request has invalid Bio, target, or action")
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

        result = tuple(proposals)
        self._last_engine_sequence[observation.actor_id] = observation.sequence
        self._observations[observation.message_id] = (observation, result)
        return result

    def receive_engine_decision(self, decision: EngineDecision) -> None:
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
        existing = self._decisions.get(decision.outcome.message_id)
        if existing:
            if existing.digest() != decision.digest():
                raise BridgeValidationError("engine outcome id was reused with different content")
            return
        if any(item.outcome.correlation_id == proposal_id for item in self._decisions.values()):
            raise BridgeValidationError("World OS proposal already has an engine outcome")
        self._decisions[decision.outcome.message_id] = decision

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
        blocked_paths: Iterable[tuple[str, str]] = (),
    ):
        self._positions = dict(actor_positions)
        self._permissions = {actor: frozenset(actions) for actor, actions in permissions.items()}
        if set(self._positions) != set(self._permissions):
            raise BridgeValidationError("every engine actor requires an explicit permission set")
        self._destinations = frozenset(destinations)
        self._blocked_paths = frozenset(blocked_paths)
        self._last_action: dict[str, dict[str, Any]] = {}
        self._last_sequence: dict[str, int] = {}
        self._processed: dict[str, tuple[str, EngineDecision]] = {}
        self._decision_sequence = 0
        self._state_version = 0

    def state(self) -> dict[str, Any]:
        return {
            "state_version": self._state_version,
            "positions": dict(sorted(self._positions.items())),
            "last_action": {actor: dict(value) for actor, value in sorted(self._last_action.items())},
        }

    def _decision(
        self,
        proposal: Envelope,
        status: str,
        reason: str,
        engine_event: Envelope | None = None,
        *,
        conflict: bool = False,
    ) -> EngineDecision:
        self._decision_sequence += 1
        suffix = f":conflict:{self._decision_sequence}" if conflict else ""
        outcome = Envelope(
            BRIDGE_SCHEMA_VERSION,
            f"engine-outcome:{proposal.message_id}{suffix}",
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
        return EngineDecision(status, outcome, engine_event)

    def validate_and_apply(self, proposal: Envelope) -> EngineDecision:
        existing = self._processed.get(proposal.message_id)
        if existing:
            if existing[0] == proposal.digest():
                return existing[1]
            return self._decision(proposal, "rejected", "message_id_conflict", conflict=True)

        reason = self._validate(proposal)
        if reason:
            if proposal.actor_id in self._positions and proposal.sequence > self._last_sequence.get(proposal.actor_id, 0):
                self._last_sequence[proposal.actor_id] = proposal.sequence
            decision = self._decision(proposal, "rejected", reason)
            self._processed[proposal.message_id] = (proposal.digest(), decision)
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
                "state_version": self._state_version,
            },
        )
        decision = self._decision(proposal, "applied", "applied", engine_event)
        self._processed[proposal.message_id] = (proposal.digest(), decision)
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
        if not all(isinstance(value, str) and value for value in (action_type, command, causal_event_id)):
            return "malformed_payload"
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
