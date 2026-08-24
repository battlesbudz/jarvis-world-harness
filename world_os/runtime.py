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

    @property
    def events(self) -> tuple[Event, ...]:
        return tuple(self._events)

    def _event(self, event_id: str) -> Event:
        for event in self.events:
            if event.id == event_id:
                return event
        raise ValidationError(f"unknown causal parent: {event_id}")

    def _validate(self, proposal: Proposal) -> str | None:
        if proposal.event_type not in PUBLIC_EVENT_TYPES:
            return f"event type {proposal.event_type!r} is not publicly legal"
        if proposal.actor not in self.actors:
            return f"unknown actor {proposal.actor!r}"
        unknown = [value for value in (*proposal.targets, *proposal.witnesses) if value not in self.actors]
        if unknown:
            return f"unknown participants: {unknown}"
        if not proposal.parents and not proposal.root_input:
            return "a causal parent or root input is required"
        try:
            for parent in proposal.parents:
                self._event(parent)
        except ValidationError as error:
            return str(error)
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
            if any(
                event.event_type == "meaningful_interaction" and event.root_input == proposal.root_input
                for event in self.events
            ):
                return "meaningful interaction root input has already been consumed"
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
        if proposal.event_type == "request" and len(proposal.targets) != 1:
            return "requests require exactly one recipient"
        return None

    def apply(self, proposal: Proposal) -> Event:
        """Validate a proposal and append either its legal event or rejection evidence."""
        reason = self._validate(proposal)
        if reason:
            fallback_actor = proposal.actor if proposal.actor in self.actors else self.crisis_actor
            return self._record(
                "proposal_rejected",
                fallback_actor,
                (),
                proposal.location,
                (),
                (),
                f"invalid-proposal:{len(self.events) + 1}",
                {"proposal": _thaw(_freeze(asdict(proposal))), "reason": reason},
            )
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
        transition = self._record(
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
        self._record(
            "independent_goal_formed",
            actor_id,
            (),
            cause.location,
            (),
            (transition.id,),
            None,
            {"goal": f"protect the people served as {actor.role}", "values": list(actor.values)},
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
            return self._record(
                "routine_response", actor_id, (bio_id,), "albion", (), (request.id,), None, {"action": action}
            )
        conflicts = action == "abandon_town" and "protect_community" in actor.values
        event_type = "values_refusal" if conflicts else "independent_choice"
        return self._record(
            event_type,
            actor_id,
            (bio_id,),
            "albion",
            (),
            (request.id,),
            None,
            {"action": action, "decision": "refuse" if conflicts else "accept", "values": list(actor.values)},
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
                        self._record(
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
            severity = min(5, self.tick)
            phase = ("warning", "strain", "danger", "collapse", "aftermath")[severity - 1]
            emitted.append(
                self._record(
                    "crisis_changed",
                    self.crisis_actor,
                    (),
                    "albion-town",
                    (),
                    (tick_event.id,),
                    None,
                    {"crisis": "river_flood", "severity": severity, "phase": phase, "player_intervened": False},
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
        return {
            "schema_version": SCHEMA_VERSION,
            "seed": self.seed,
            "tick": self.tick,
            "crisis_actor": self.crisis_actor,
            "actors": [self.actors[key].to_dict() for key in sorted(self.actors)],
            "events": [event.to_dict() for event in self.events],
        }

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
                if next_event and next_event.parents == (event.id,) and next_event.event_type in {
                    "routine_response",
                    "values_refusal",
                    "independent_choice",
                }:
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
