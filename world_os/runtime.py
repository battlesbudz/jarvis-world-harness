from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable


SCHEMA_VERSION = 1
ACTOR_CATEGORIES = {"bio", "thinker", "non_thinker"}
PUBLIC_EVENT_TYPES = {"meaningful_interaction", "rumor_shared", "request"}
INTERNAL_PROPOSAL_TYPES = {
    "awakening_transition",
    "crisis_changed",
    "independent_goal_formed",
    "independent_choice",
    "routine_action",
    "routine_response",
    "values_refusal",
}
RELATIONSHIP_DIMENSIONS = ("trust", "fear", "respect", "resentment", "affection")
FACTOR_EFFECTS = {
    "attention": {"trust": 1, "affection": 1},
    "shared_danger": {"trust": 2, "fear": 1, "respect": 2},
    "protection": {"trust": 3, "fear": -1, "respect": 2, "affection": 2},
    "vulnerability": {"trust": 2, "affection": 2},
    "betrayal": {"trust": -4, "resentment": 4},
}
AWAKENING_WEIGHTS = {
    "attention": 1,
    "shared_danger": 3,
    "protection": 4,
    "vulnerability": 3,
    "betrayal": 1,
}
AWAKENING_THRESHOLD = 12
FEASIBLE_REQUEST_ACTIONS = {"abandon_town", "read", "wait"}


class ValidationError(ValueError):
    pass


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


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
class Actor:
    id: str
    name: str
    category: str
    role: str
    values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.id or self.category not in ACTOR_CATEGORIES:
            raise ValidationError(f"invalid actor: {self.id!r}/{self.category!r}")

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["values"] = list(self.values)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Actor:
        return cls(
            id=data["id"],
            name=data["name"],
            category=data["category"],
            role=data["role"],
            values=tuple(data.get("values", ())),
        )


@dataclass(frozen=True)
class Proposal:
    event_type: str
    actor: str
    targets: tuple[str, ...] = ()
    location: str = "unknown"
    witnesses: tuple[str, ...] = ()
    parents: tuple[str, ...] = ()
    root_input: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Event:
    id: str
    schema_version: int
    tick: int
    event_type: str
    actor: str
    targets: tuple[str, ...]
    location: str
    witnesses: tuple[str, ...]
    parents: tuple[str, ...]
    root_input: str | None
    order: int
    payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "tick": self.tick,
            "event_type": self.event_type,
            "actor": self.actor,
            "targets": list(self.targets),
            "location": self.location,
            "witnesses": list(self.witnesses),
            "parents": list(self.parents),
            "root_input": self.root_input,
            "order": self.order,
            "payload": _thaw(self.payload),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        return cls(
            id=data["id"],
            schema_version=data["schema_version"],
            tick=data["tick"],
            event_type=data["event_type"],
            actor=data["actor"],
            targets=tuple(data["targets"]),
            location=data["location"],
            witnesses=tuple(data["witnesses"]),
            parents=tuple(data["parents"]),
            root_input=data.get("root_input"),
            order=data["order"],
            payload=_freeze(data["payload"]),
        )


class World:
    """Append-only deterministic world state with derived cognition and agency."""

    def __init__(self, seed: int, actors: Iterable[Actor], crisis_actor: str):
        self.seed = int(seed)
        self.tick = 0
        actor_list = list(actors)
        actor_map = {actor.id: actor for actor in actor_list}
        if not actor_map or len(actor_map) != len(actor_list):
            raise ValidationError("actors must have unique stable identities")
        if crisis_actor not in actor_map:
            raise ValidationError("crisis actor must exist")
        self.actors = MappingProxyType(actor_map)
        self.crisis_actor = crisis_actor
        self._events: list[Event] = []
        self._extensions: dict[str, dict[str, Any]] = {}
        for actor in sorted(self.actors.values(), key=lambda item: item.id):
            if actor.category == "bio":
                self._record(
                    "earth_memory",
                    actor.id,
                    (),
                    "earth-memory",
                    (),
                    (),
                    f"bio-origin:{actor.id}",
                    {"memory": "Earth", "rule": "bio_remembers_earth_immediately"},
                )
            elif actor.category == "thinker":
                value = actor.values[0] if actor.values else actor.role
                self._apply_internal(
                    Proposal(
                        "independent_goal_formed",
                        actor.id,
                        (),
                        "genesis",
                        (),
                        (),
                        f"thinker-goal:{actor.id}",
                        {
                            "goal": f"uphold {value} while serving as {actor.role}",
                            "values": list(actor.values),
                            "rule": "conscious_thinker_genesis_goal",
                        },
                    )
                )

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def _event(self, event_id: str) -> Event:
        for event in self.events:
            if event.id == event_id:
                return event
        raise ValidationError(f"unknown causal parent: {event_id}")

    def _validate(self, proposal: Proposal, *, internal: bool = False) -> str | None:
        if not isinstance(proposal.event_type, str):
            return "event type must be a string"
        allowed_types = INTERNAL_PROPOSAL_TYPES if internal else PUBLIC_EVENT_TYPES
        if proposal.event_type not in allowed_types:
            return f"event type {proposal.event_type!r} is not publicly legal"
        if not isinstance(proposal.actor, str) or proposal.actor not in self.actors:
            return f"unknown actor {proposal.actor!r}"
        if not isinstance(proposal.targets, (list, tuple)) or not isinstance(
            proposal.witnesses, (list, tuple)
        ):
            return "targets and witnesses must be identity sequences"
        if not isinstance(proposal.parents, (list, tuple)) or any(
            not isinstance(parent, str) for parent in proposal.parents
        ):
            return "causal parents must be an event-identity sequence"
        if len(proposal.parents) != len(set(proposal.parents)):
            return "causal parent identities must be distinct"
        if not isinstance(proposal.location, str):
            return "location must be a string"
        if proposal.root_input is not None and not isinstance(proposal.root_input, str):
            return "root input must be a string when supplied"
        if not isinstance(proposal.payload, dict):
            return "payload must be an object"
        participants = (*proposal.targets, *proposal.witnesses)
        invalid = [value for value in participants if not isinstance(value, str)]
        if invalid:
            return f"participant identities must be strings: {invalid!r}"
        unknown = [value for value in participants if value not in self.actors]
        if unknown:
            return f"unknown participants: {unknown}"
        if not proposal.parents and not proposal.root_input:
            return "a causal parent or root input is required"
        try:
            for parent in proposal.parents:
                self._event(parent)
        except ValidationError as error:
            return str(error)
        if internal:
            return self._validate_internal_rules(proposal)
        if proposal.event_type == "meaningful_interaction":
            if len(proposal.targets) != 1:
                return "meaningful interactions require exactly one target"
            factors = proposal.payload.get("factors")
            if (
                not isinstance(factors, list)
                or not factors
                or any(not isinstance(value, str) or value not in FACTOR_EFFECTS for value in factors)
            ):
                return "meaningful interaction factors are missing or invalid"
            if len(factors) != len(set(factors)):
                return "meaningful interaction factors must be distinct within one event"
            if proposal.root_input is not None:
                consumed = any(
                    event.event_type == "meaningful_interaction"
                    and event.root_input == proposal.root_input
                    for event in self.events
                )
            else:
                parent_identity = tuple(sorted(proposal.parents))
                consumed = any(
                    event.event_type == "meaningful_interaction"
                    and event.root_input is None
                    and tuple(sorted(event.parents)) == parent_identity
                    for event in self.events
                )
            if consumed:
                return "meaningful interaction causal input has already been consumed"
        if proposal.event_type == "rumor_shared":
            if len(proposal.targets) != 1:
                return "rumor sharing requires exactly one listener"
            if not {"source_event", "provenance", "confidence"} <= proposal.payload.keys():
                return "rumor provenance is incomplete"
            provenance = proposal.payload["provenance"]
            if not isinstance(provenance, (list, tuple)) or not all(
                isinstance(actor_id, str) and actor_id in self.actors for actor_id in provenance
            ):
                return "rumor provenance must be a sequence of known actor identities"
            try:
                knowledge = self._knows(proposal.actor, proposal.payload["source_event"])
            except ValidationError as error:
                return str(error)
            if knowledge is None:
                return "teller has no traceable knowledge of the source event"
            chain, confidence, parent = knowledge
            if chain[-1] != proposal.actor:
                chain.append(proposal.actor)
            chain.append(proposal.targets[0])
            relationship = self.relationship(proposal.targets[0], proposal.actor)
            credibility = max(0.5, min(1.0, 0.8 + 0.02 * (relationship["trust"] - relationship["resentment"])))
            expected_confidence = round(confidence * credibility, 6)
            if proposal.parents != (parent,):
                return "rumor must parent the evidence through which the teller learned it"
            if list(provenance) != chain or proposal.payload["confidence"] != expected_confidence:
                return "rumor provenance or confidence is not derivable"
        if proposal.event_type == "request":
            if len(proposal.targets) != 1:
                return "requests require exactly one recipient"
            if not isinstance(proposal.payload.get("action"), str) or not proposal.payload["action"]:
                return "requests require a non-empty action"
            if proposal.payload["action"] not in FEASIBLE_REQUEST_ACTIONS:
                return "request action has no established physical-possibility rule"
        return None

    def _validate_internal_rules(self, proposal: Proposal) -> str | None:
        if proposal.event_type == "independent_goal_formed":
            actor = self.actors[proposal.actor]
            if any(event.event_type == "independent_goal_formed" and event.actor == actor.id for event in self.events):
                return "actor already has a persistent goal"
            if actor.category == "thinker":
                value = actor.values[0] if actor.values else actor.role
                if (
                    proposal.parents
                    or proposal.root_input != f"thinker-goal:{actor.id}"
                    or proposal.location != "genesis"
                    or proposal.targets
                    or proposal.witnesses
                    or proposal.payload.get("goal") != f"uphold {value} while serving as {actor.role}"
                    or proposal.payload.get("values") != list(actor.values)
                    or proposal.payload.get("rule") != "conscious_thinker_genesis_goal"
                ):
                    return "Thinker genesis goal violates identity or initialization rules"
                return None
            if len(proposal.parents) != 1:
                return "awakened goal requires exactly one transition parent"
            transition = self._event(proposal.parents[0])
            if (
                actor.category != "non_thinker"
                or transition.event_type != "awakening_transition"
                or transition.targets != (actor.id,)
                or proposal.root_input is not None
                or proposal.targets
                or proposal.witnesses
                or proposal.location != transition.location
                or proposal.payload.get("goal") != f"protect the people served as {actor.role}"
                or proposal.payload.get("values") != list(actor.values)
                or proposal.payload.get("rule") != "awakened_independent_goal"
            ):
                return "awakened goal violates cognition, transition, or identity preconditions"
            return None

        if not proposal.parents:
            return "internal action requires causal evidence"
        if proposal.event_type == "awakening_transition":
            if len(proposal.targets) != 1:
                return "awakening transition requires exactly one subject"
            actor = self.actors[proposal.targets[0]]
            contributors = tuple(self._event(parent_id) for parent_id in proposal.parents)
            expected_contributors = tuple(
                event
                for event in self.events
                if event.event_type == "meaningful_interaction"
                and actor.id in event.targets
                and self.actors[event.actor].category == "bio"
            )
            score = sum(
                AWAKENING_WEIGHTS[factor]
                for event in contributors
                for factor in event.payload.get("factors", ())
                if factor in AWAKENING_WEIGHTS
            )
            if (
                actor.category != "non_thinker"
                or self.is_awakened(actor.id)
                or self.actors[proposal.actor].category != "bio"
                or proposal.actor != contributors[-1].actor
                or contributors != expected_contributors
                or len(contributors) < 3
                or len({event.tick for event in contributors}) < 3
                or any(
                    event.event_type != "meaningful_interaction"
                    or actor.id not in event.targets
                    or self.actors[event.actor].category != "bio"
                    for event in contributors
                )
                or score < AWAKENING_THRESHOLD
                or proposal.location != contributors[-1].location
                or tuple(proposal.witnesses) != contributors[-1].witnesses
                or proposal.root_input is not None
                or proposal.payload.get("rule") != "repeated_meaningful_soul_pattern"
                or proposal.payload.get("score") != score
                or proposal.payload.get("threshold") != AWAKENING_THRESHOLD
                or proposal.payload.get("interaction_count") != len(contributors)
            ):
                return "awakening transition violates evidence, cognition, or threshold preconditions"
            return None

        parent = self._event(proposal.parents[0])
        if proposal.event_type == "crisis_changed":
            expected_severity = min(5, self.tick)
            phases = ("warning", "strain", "danger", "collapse", "aftermath")
            if proposal.actor != self.crisis_actor:
                return "only the configured crisis authority may change world pressure"
            if len(proposal.parents) != 1:
                return "crisis change requires exactly one logical-tick parent"
            if proposal.targets or proposal.witnesses or proposal.location != "albion-town":
                return "crisis change violates the Albion World Anchor boundary"
            if parent.event_type != "time_advanced" or parent.tick != self.tick:
                return "crisis change must follow the current logical tick"
            if any(event.event_type == "crisis_changed" and event.tick == self.tick for event in self.events):
                return "crisis pressure may change only once per logical tick"
            if (
                proposal.payload.get("crisis") != "river_flood"
                or not isinstance(proposal.payload.get("severity"), int)
                or isinstance(proposal.payload.get("severity"), bool)
                or proposal.payload.get("severity") != expected_severity
                or proposal.payload.get("phase") != phases[expected_severity - 1]
                or proposal.payload.get("player_intervened") is not False
            ):
                return "crisis change violates deterministic severity, phase, or playability constraints"
            return None

        if proposal.event_type == "routine_action":
            actor = self.actors[proposal.actor]
            if (
                len(proposal.parents) != 1
                or actor.category != "non_thinker"
                or self.cognition(actor.id) != "routine"
                or proposal.targets
                or proposal.witnesses
                or proposal.location != "albion"
                or parent.event_type != "time_advanced"
                or parent.tick != self.tick
                or proposal.payload.get("role") != actor.role
                or proposal.payload.get("action") != f"perform {actor.role} routine"
            ):
                return "routine action violates role, cognition, or tick preconditions"
            return None

        if parent.event_type != "request" or len(parent.targets) != 1:
            return "actor decision must answer one request"
        goal_events = self._goal_events(proposal.actor)
        expected_parents = (parent.id, *(event.id for event in goal_events))
        if proposal.parents != expected_parents:
            return "actor decision must parent the request and every consulted persistent goal"
        if (
            proposal.actor != parent.targets[0]
            or tuple(proposal.targets) != (parent.actor,)
            or proposal.witnesses
            or proposal.location != "albion"
        ):
            return "actor decision identities do not match the request"
        if proposal.payload.get("action") != parent.payload.get("action"):
            return "actor decision action does not match the request"
        cognition = self.cognition(proposal.actor)
        if proposal.event_type == "routine_response":
            return None if cognition != "conscious" else "conscious actors cannot emit routine responses"
        if cognition != "conscious":
            return "routine actors cannot emit independent decisions"
        conflicts = self._action_conflicts_with_goals(proposal.actor, proposal.payload.get("action"))
        if proposal.event_type == "values_refusal" and not conflicts:
            return "refusal is not supported by persistent goals or values"
        if proposal.event_type == "independent_choice" and conflicts:
            return "conflicting action requires a values-based refusal"
        expected_decision = "refuse" if conflicts else "accept"
        if (
            proposal.payload.get("decision") != expected_decision
            or proposal.payload.get("goals") != self.goals(proposal.actor)
            or proposal.payload.get("values") != list(self.actors[proposal.actor].values)
        ):
            return "decision evidence does not match persistent goals and values"
        return None

    def _action_conflicts_with_goals(self, actor_id: str, action: Any) -> bool:
        actor = self.actors[actor_id]
        return action == "abandon_town" and (
            "protect_community" in actor.values or any("protect" in goal for goal in self.goals(actor_id))
        )

    def _goal_events(self, actor_id: str) -> tuple[Event, ...]:
        return tuple(
            event
            for event in self.events
            if event.event_type == "independent_goal_formed" and event.actor == actor_id
        )

    def _reject(self, proposal: Proposal, reason: str) -> Event:
        fallback_actor = (
            proposal.actor
            if isinstance(proposal.actor, str) and proposal.actor in self.actors
            else self.crisis_actor
        )
        return self._record(
            "proposal_rejected",
            fallback_actor,
            (),
            proposal.location if isinstance(proposal.location, str) else "unknown",
            (),
            (),
            f"invalid-proposal:{len(self.events) + 1}",
            {"proposal": _thaw(_freeze(asdict(proposal))), "reason": reason},
        )

    def apply(self, proposal: Proposal) -> Event:
        """Validate a proposal and append either its legal event or rejection evidence."""
        reason = self._validate(proposal)
        if reason:
            return self._reject(proposal, reason)
        event = self._record(
            proposal.event_type,
            proposal.actor,
            proposal.targets,
            proposal.location,
            proposal.witnesses,
            proposal.parents,
            proposal.root_input,
            proposal.payload,
        )
        if event.event_type == "meaningful_interaction":
            self._maybe_awaken(event.targets[0], event.actor, event)
        return event

    def _apply_internal(self, proposal: Proposal) -> Event:
        reason = self._validate(proposal, internal=True)
        if reason:
            return self._reject(proposal, reason)
        payload = dict(proposal.payload)
        payload["validation"] = {
            "authority": "world_validator",
            "cooldowns": True,
            "identity": True,
            "permissions": True,
            "physical_possibility": True,
            "preconditions": True,
            "world_anchor": True,
            "playability": True,
        }
        return self._record(
            proposal.event_type,
            proposal.actor,
            proposal.targets,
            proposal.location,
            proposal.witnesses,
            proposal.parents,
            proposal.root_input,
            payload,
        )

    def apply_all(self, proposals: Iterable[Proposal]) -> list[Event]:
        """Apply simultaneous proposals in a stable, process-independent order."""
        ordered = sorted(proposals, key=lambda proposal: _canonical(asdict(proposal)))
        return [self.apply(proposal) for proposal in ordered]

    def _record(
        self,
        event_type: str,
        actor: str,
        targets: tuple[str, ...],
        location: str,
        witnesses: tuple[str, ...],
        parents: tuple[str, ...],
        root_input: str | None,
        payload: dict[str, Any],
    ) -> Event:
        event = Event(
            id=f"evt-{len(self.events) + 1:06d}",
            schema_version=SCHEMA_VERSION,
            tick=self.tick,
            event_type=event_type,
            actor=actor,
            targets=tuple(targets),
            location=location,
            witnesses=tuple(sorted(set(witnesses))),
            parents=tuple(parents),
            root_input=root_input,
            order=len(self.events),
            payload=_freeze(json.loads(_canonical(payload))),
        )
        self._events.append(event)
        return event

    def meaningful_interaction(
        self,
        actor: str,
        target: str,
        factors: list[str],
        *,
        witnesses: Iterable[str] = (),
        location: str = "albion",
        root_input: str,
    ) -> Event:
        return self.apply(
            Proposal(
                "meaningful_interaction",
                actor,
                (target,),
                location,
                tuple(witnesses),
                root_input=root_input,
                payload={"factors": list(factors)},
            )
        )

    def is_awakened(self, actor_id: str) -> bool:
        return any(event.event_type == "awakening_transition" and actor_id in event.targets for event in self.events)

    def cognition(self, actor_id: str) -> str:
        actor = self.actors[actor_id]
        if actor.category == "non_thinker" and not self.is_awakened(actor_id):
            return "routine"
        return "conscious"

    def awakening_score(self, actor_id: str) -> int:
        actor = self.actors[actor_id]
        if actor.category != "non_thinker":
            return 0
        return sum(
            AWAKENING_WEIGHTS[factor]
            for event in self.events
            if event.event_type == "meaningful_interaction"
            and actor_id in event.targets
            and self.actors[event.actor].category == "bio"
            for factor in event.payload["factors"]
            if factor in AWAKENING_WEIGHTS
        )

    def _maybe_awaken(self, actor_id: str, bio_id: str, cause: Event) -> None:
        actor = self.actors[actor_id]
        if actor.category != "non_thinker" or self.is_awakened(actor_id):
            return
        qualifying = tuple(
            event
            for event in self.events
            if event.event_type == "meaningful_interaction"
            and actor_id in event.targets
            and self.actors[event.actor].category == "bio"
        )
        if (
            self.actors[bio_id].category != "bio"
            or len(qualifying) < 3
            or len({event.tick for event in qualifying}) < 3
            or self.awakening_score(actor_id) < AWAKENING_THRESHOLD
        ):
            return
        contributors = tuple(event.id for event in qualifying)
        transition = self._apply_internal(
            Proposal(
                "awakening_transition",
                bio_id,
                (actor_id,),
                cause.location,
                cause.witnesses,
                contributors,
                None,
                {
                    "rule": "repeated_meaningful_soul_pattern",
                    "score": self.awakening_score(actor_id),
                    "threshold": AWAKENING_THRESHOLD,
                    "interaction_count": len(qualifying),
                },
            )
        )
        if transition.event_type != "awakening_transition":
            return
        self._apply_internal(
            Proposal(
                "independent_goal_formed",
                actor_id,
                (),
                cause.location,
                (),
                (transition.id,),
                None,
                {
                    "goal": f"protect the people served as {actor.role}",
                    "values": list(actor.values),
                    "rule": "awakened_independent_goal",
                },
            )
        )

    def relationship(self, observer: str, subject: str) -> dict[str, int]:
        result = {dimension: 0 for dimension in RELATIONSHIP_DIMENSIONS}
        for event in self.events:
            if event.event_type != "meaningful_interaction" or event.actor != subject or observer not in event.targets:
                continue
            for factor in event.payload["factors"]:
                for dimension, delta in FACTOR_EFFECTS[factor].items():
                    result[dimension] += delta
        return result

    def relationship_trace(self, observer: str, subject: str) -> dict[str, Any]:
        contributions = []
        for event in self.events:
            if event.event_type != "meaningful_interaction" or event.actor != subject or observer not in event.targets:
                continue
            for factor in event.payload["factors"]:
                contributions.append(
                    {
                        "event_id": event.id,
                        "factor": factor,
                        "rule": "relationship_factor_effects",
                        "deltas": dict(FACTOR_EFFECTS[factor]),
                    }
                )
        return {
            "observer": observer,
            "subject": subject,
            "dimensions": self.relationship(observer, subject),
            "contributions": contributions,
        }

    def memories(self, actor_id: str) -> list[dict[str, Any]]:
        if self.cognition(actor_id) != "conscious":
            return []
        memories = []
        for event in self.events:
            if actor_id not in (event.actor, *event.targets, *event.witnesses):
                continue
            if actor_id == event.actor:
                perspective = "actor"
            elif actor_id in event.targets:
                perspective = "target"
            else:
                perspective = "witness"
            memories.append({"event_id": event.id, "perspective": perspective, "tick": event.tick})
        return memories

    def beliefs(self, actor_id: str) -> list[dict[str, Any]]:
        beliefs = []
        for event in self.events:
            if event.event_type == "rumor_shared" and actor_id in event.targets:
                beliefs.append(
                    {
                        "source_event": event.payload["source_event"],
                        "provenance": list(event.payload["provenance"]),
                        "confidence": event.payload["confidence"],
                    }
                )
            elif self.cognition(actor_id) == "conscious" and actor_id in event.witnesses:
                beliefs.append({"source_event": event.id, "provenance": [event.actor, actor_id], "confidence": 1.0})
        return beliefs

    def _knows(self, actor_id: str, source_event: str) -> tuple[list[str], float, str] | None:
        source = self._event(source_event)
        candidates: list[tuple[int, list[str], float, str]] = []
        if actor_id in (source.actor, *source.targets, *source.witnesses):
            chain = [source.actor, actor_id] if source.actor != actor_id else [actor_id]
            candidates.append((source.order, chain, 1.0, source.id))
        for event in self.events:
            if (
                event.event_type == "rumor_shared"
                and actor_id in event.targets
                and event.payload["source_event"] == source_event
            ):
                candidates.append(
                    (event.order, list(event.payload["provenance"]), float(event.payload["confidence"]), event.id)
                )
        if not candidates:
            return None
        _order, chain, confidence, parent = max(candidates, key=lambda candidate: candidate[0])
        return chain, confidence, parent

    def share_rumor(self, teller: str, listener: str, source_event: str, *, root_input: str) -> Event:
        try:
            knowledge = self._knows(teller, source_event)
        except ValidationError:
            knowledge = None
        if knowledge is None:
            return self.apply(
                Proposal("rumor_shared", teller, (listener,), root_input=root_input, payload={"source_event": source_event})
            )
        chain, confidence, parent = knowledge
        if chain[-1] != teller:
            chain.append(teller)
        chain.append(listener)
        relationship = self.relationship(listener, teller)
        credibility = max(0.5, min(1.0, 0.8 + 0.02 * (relationship["trust"] - relationship["resentment"])))
        return self.apply(
            Proposal(
                "rumor_shared",
                teller,
                (listener,),
                parents=(parent,),
                root_input=root_input,
                payload={
                    "source_event": source_event,
                    "provenance": chain,
                    "confidence": round(confidence * credibility, 6),
                },
            )
        )

    def decide_request(self, bio_id: str, actor_id: str, action: str, *, root_input: str) -> Event:
        request = self.apply(
            Proposal("request", bio_id, (actor_id,), root_input=root_input, payload={"action": action})
        )
        if request.event_type != "request":
            return request
        actor = self.actors[actor_id]
        if self.cognition(actor_id) != "conscious":
            return self._apply_internal(
                Proposal("routine_response", actor_id, (bio_id,), "albion", (), (request.id,), None, {"action": action})
            )
        conflicts = self._action_conflicts_with_goals(actor_id, action)
        event_type = "values_refusal" if conflicts else "independent_choice"
        goal_events = self._goal_events(actor_id)
        return self._apply_internal(
            Proposal(
                event_type,
                actor_id,
                (bio_id,),
                "albion",
                (),
                (request.id, *(event.id for event in goal_events)),
                None,
                {
                    "action": action,
                    "decision": "refuse" if conflicts else "accept",
                    "goals": self.goals(actor_id),
                    "values": list(actor.values),
                },
            )
        )

    def advance(self, ticks: int = 1) -> list[Event]:
        if not isinstance(ticks, int) or isinstance(ticks, bool) or ticks < 0:
            raise ValidationError("logical tick count must be a non-negative integer")
        emitted = []
        for _ in range(ticks):
            self.tick += 1
            tick_event = self._record(
                "time_advanced", self.crisis_actor, (), "albion", (), (), f"tick:{self.tick}", {"tick": self.tick}
            )
            emitted.append(tick_event)
            for actor in sorted(self.actors.values(), key=lambda item: item.id):
                if actor.category == "non_thinker" and not self.is_awakened(actor.id):
                    emitted.append(
                        self._apply_internal(
                            Proposal(
                                "routine_action",
                                actor.id,
                                (),
                                "albion",
                                (),
                                (tick_event.id,),
                                None,
                                {"role": actor.role, "action": f"perform {actor.role} routine"},
                            )
                        )
                    )
            severity = min(5, self.tick)
            phase = ("warning", "strain", "danger", "collapse", "aftermath")[severity - 1]
            emitted.append(
                self._apply_internal(
                    Proposal(
                        "crisis_changed",
                        self.crisis_actor,
                        (),
                        "albion-town",
                        (),
                        (tick_event.id,),
                        None,
                        {
                            "crisis": "river_flood",
                            "severity": severity,
                            "phase": phase,
                            "player_intervened": False,
                        },
                    )
                )
            )
        return emitted

    def crisis(self) -> dict[str, Any]:
        event = next((event for event in reversed(self.events) if event.event_type == "crisis_changed"), None)
        return dict(event.payload) if event else {"crisis": "river_flood", "severity": 0, "phase": "dormant"}

    def goals(self, actor_id: str) -> list[str]:
        return [
            event.payload["goal"]
            for event in self.events
            if event.event_type == "independent_goal_formed" and event.actor == actor_id
        ]

    def trace(self, event_id: str) -> dict[str, Any]:
        event = self._event(event_id)
        return {
            "event": event.to_dict(),
            "causes": [self.trace(parent) for parent in event.parents],
            "rule": event.payload.get("rule", event.event_type),
        }

    def state(self) -> dict[str, Any]:
        state = {
            "schema_version": SCHEMA_VERSION,
            "seed": self.seed,
            "tick": self.tick,
            "crisis_actor": self.crisis_actor,
            "actors": [self.actors[key].to_dict() for key in sorted(self.actors)],
            "events": [event.to_dict() for event in self.events],
        }
        if self._extensions:
            state["extensions"] = json.loads(_canonical(self._extensions))
        return state

    def extension_state(self, namespace: str) -> dict[str, Any] | None:
        state = self._extensions.get(namespace)
        return json.loads(_canonical(state)) if state is not None else None

    def set_extension_state(self, namespace: str, state: Mapping[str, Any]) -> None:
        if not isinstance(namespace, str) or not namespace:
            raise ValidationError("extension namespace must be a non-empty string")
        try:
            copied = json.loads(_canonical(dict(state)))
        except (TypeError, ValueError) as error:
            raise ValidationError(f"extension state must be a JSON object: {error}") from error
        if not isinstance(copied, dict):
            raise ValidationError("extension state must be a JSON object")
        self._extensions[namespace] = copied

    def state_digest(self) -> str:
        return _digest(self.state())

    def save(self, path: Path) -> None:
        state = self.state()
        envelope = {"format": "jarvis-world-h1", "state": state, "digest": _digest(state)}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_canonical(envelope) + "\n", encoding="utf-8")

    @staticmethod
    def _proposal_from_dict(data: Mapping[str, Any]) -> Proposal:
        return Proposal(
            event_type=data["event_type"],
            actor=data["actor"],
            targets=tuple(data.get("targets", ())),
            location=data.get("location", "unknown"),
            witnesses=tuple(data.get("witnesses", ())),
            parents=tuple(data.get("parents", ())),
            root_input=data.get("root_input"),
            payload=_thaw(data.get("payload", {})),
        )

    @classmethod
    def _replay(cls, state: dict[str, Any]) -> World:
        saved_events = [Event.from_dict(item) for item in state["events"]]
        world = cls(state["seed"], (Actor.from_dict(item) for item in state["actors"]), state["crisis_actor"])
        index = 0
        while index < len(saved_events):
            if index < len(world.events):
                if world.events[index].to_dict() != saved_events[index].to_dict():
                    raise ValidationError(f"event {saved_events[index].id} does not replay from its causal history")
                index += 1
                continue

            event = saved_events[index]
            if event.event_type == "meaningful_interaction":
                world.apply(
                    Proposal(
                        event.event_type,
                        event.actor,
                        event.targets,
                        event.location,
                        event.witnesses,
                        event.parents,
                        event.root_input,
                        _thaw(event.payload),
                    )
                )
            elif event.event_type == "rumor_shared":
                world.apply(
                    Proposal(
                        event.event_type,
                        event.actor,
                        event.targets,
                        event.location,
                        event.witnesses,
                        event.parents,
                        event.root_input,
                        _thaw(event.payload),
                    )
                )
            elif event.event_type == "request":
                next_event = saved_events[index + 1] if index + 1 < len(saved_events) else None
                if (
                    next_event
                    and next_event.parents
                    and next_event.parents[0] == event.id
                    and next_event.event_type
                    in {
                        "routine_response",
                        "values_refusal",
                        "independent_choice",
                    }
                ):
                    world.decide_request(
                        event.actor,
                        event.targets[0],
                        event.payload["action"],
                        root_input=event.root_input or "replayed-request",
                    )
                else:
                    world.apply(
                        Proposal(
                            event.event_type,
                            event.actor,
                            event.targets,
                            event.location,
                            event.witnesses,
                            event.parents,
                            event.root_input,
                            _thaw(event.payload),
                        )
                    )
            elif event.event_type == "time_advanced":
                world.advance()
            elif event.event_type == "proposal_rejected":
                proposal = event.payload.get("proposal")
                if not isinstance(proposal, Mapping):
                    raise ValidationError("rejection event does not preserve its proposed input")
                world.apply(cls._proposal_from_dict(proposal))
            else:
                raise ValidationError(f"internal event {event.event_type!r} has no valid causal precursor")

            if len(world.events) <= index:
                raise ValidationError(f"event {event.id} could not be reproduced")

        if len(world.events) != len(saved_events) or world.tick != state["tick"]:
            raise ValidationError("persisted world does not replay to its claimed boundary")
        extensions = state.get("extensions", {})
        if not isinstance(extensions, dict) or any(
            not isinstance(namespace, str) or not isinstance(value, dict)
            for namespace, value in extensions.items()
        ):
            raise ValidationError("persisted world extensions are malformed")
        for namespace, value in extensions.items():
            world.set_extension_state(namespace, value)
        return world

    @classmethod
    def load(cls, path: Path, *, expected_state_digest: str) -> World:
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            state = envelope["state"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise ValidationError(f"invalid persisted world: {error}") from error
        if envelope.get("format") != "jarvis-world-h1" or state.get("schema_version") != SCHEMA_VERSION:
            raise ValidationError("incompatible persisted world")
        if not expected_state_digest or _digest(state) != expected_state_digest:
            raise ValidationError("persisted world does not match the trusted snapshot boundary")
        if envelope.get("digest") != _digest(state):
            raise ValidationError("persisted world digest mismatch")
        return cls._replay(state)

    def write_trace(
        self,
        path: Path,
        name: str,
        event_ids: Iterable[str],
        relationships: Iterable[tuple[str, str]] = (),
    ) -> None:
        payload = {
            "scenario": name,
            "seed": self.seed,
            "tick": self.tick,
            "events": [self.trace(event_id) for event_id in event_ids],
            "relationships": [self.relationship_trace(observer, subject) for observer, subject in relationships],
            "state_digest": self.state_digest(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_canonical(payload) + "\n", encoding="utf-8")
