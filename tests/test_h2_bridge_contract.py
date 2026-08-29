import hashlib
import hmac
import json
import math
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from world_os import (
    Actor,
    BRIDGE_SCHEMA_VERSION,
    BridgeValidationError,
    EngineAuthority,
    EngineDecision,
    Envelope,
    WorldOSBridge,
    World,
)
from world_os.scenarios import albion_world
from world_os.bridge import _authority_material, _origin_proof


PROPOSAL_ORIGIN_KEY = b"h2-deterministic-test-key"
AUTHORITY_SIGNING_KEY = Ed25519PrivateKey.from_private_bytes(
    hashlib.sha256(b"h2-deterministic-engine-authority-key").digest()
)
AUTHORITY_PUBLIC_KEY = AUTHORITY_SIGNING_KEY.public_key()
ATTACKER_SIGNING_KEY = Ed25519PrivateKey.from_private_bytes(
    hashlib.sha256(b"world-os-cannot-sign-engine-results").digest()
)

_EngineAuthority = EngineAuthority
_WorldOSBridge = WorldOSBridge


class EngineAuthority(_EngineAuthority):
    def __init__(
        self,
        actor_positions,
        permissions,
        destinations,
        proposal_origin_key,
        authority_signing_key=AUTHORITY_SIGNING_KEY,
        blocked_paths=(),
    ):
        super().__init__(
            actor_positions,
            permissions,
            destinations,
            proposal_origin_key,
            authority_signing_key,
            blocked_paths,
        )

    @classmethod
    def load(
        cls,
        path,
        proposal_origin_key,
        authority_signing_key=AUTHORITY_SIGNING_KEY,
        *,
        expected_snapshot_digest,
    ):
        return super().load(
            path,
            proposal_origin_key,
            authority_signing_key,
            expected_snapshot_digest=expected_snapshot_digest,
        )


def WorldOSBridge(
    world,
    role_stations,
    proposal_origin_key,
    engine_authority_public_key=AUTHORITY_PUBLIC_KEY,
):
    return _WorldOSBridge(
        world,
        role_stations,
        proposal_origin_key,
        engine_authority_public_key,
    )


def envelope(message_id, sequence, actor_id, message_type, payload, correlation_id="h2-run:1"):
    return Envelope(
        BRIDGE_SCHEMA_VERSION,
        message_id,
        correlation_id,
        sequence,
        actor_id,
        message_type,
        payload,
    )


def h2_bridge():
    return WorldOSBridge(
        albion_world(2202),
        {"ferryman": "ferry-dock", "baker": "bakery"},
        PROPOSAL_ORIGIN_KEY,
        AUTHORITY_PUBLIC_KEY,
    )


def engine_authority():
    return EngineAuthority(
        {"elias": "village-square", "nella": "village-square", "mara": "captain-post"},
        {
            "elias": {"routine_move"},
            "nella": {"routine_move"},
            "mara": {"independent_choice", "values_refusal"},
        },
        {"ferry-dock", "bakery", "captain-post"},
        PROPOSAL_ORIGIN_KEY,
        AUTHORITY_SIGNING_KEY,
    )


def legacy_proposal(proposal):
    if "global_order" not in proposal.payload:
        return proposal
    payload = dict(proposal.payload)
    payload.pop("global_order")
    payload.pop("origin_proof")
    unsigned = Envelope.from_dict(
        {**proposal.to_dict(), "payload": payload}
    )
    return Envelope.from_dict(
        {
            **unsigned.to_dict(),
            "payload": {
                **payload,
                "origin_proof": _origin_proof(
                    unsigned, PROPOSAL_ORIGIN_KEY
                ),
            },
        }
    )


def decide(authority, proposal):
    proposal = legacy_proposal(proposal)
    decisions = authority.validate_and_apply(proposal)
    if len(decisions) != 1:
        raise AssertionError(f"expected exactly one engine decision, received {len(decisions)}")
    return decisions[0]


class H2BridgeContractTest(unittest.TestCase):
    def test_versioned_envelope_round_trip_and_fail_closed_schema(self):
        source_payload = {"ticks": 1, "context": {"tags": ["village"]}}
        original = envelope("engine-observation:1", 1, "bio", "time_advance", source_payload)
        source_payload["context"]["tags"].append("mutated")
        self.assertEqual(original.payload["context"]["tags"], ("village",))
        self.assertEqual(Envelope.from_dict(original.to_dict()), original)
        self.assertEqual(original.digest(), Envelope.from_dict(original.to_dict()).digest())

        future = original.to_dict()
        future["schema_version"] = 2
        with self.assertRaises(BridgeValidationError):
            Envelope.from_dict(future)
        boolean_version = original.to_dict()
        boolean_version["schema_version"] = True
        with self.assertRaises(BridgeValidationError):
            Envelope.from_dict(boolean_version)
        malformed = original.to_dict()
        malformed["unexpected"] = True
        with self.assertRaises(BridgeValidationError):
            Envelope.from_dict(malformed)
        with self.assertRaises(BridgeValidationError):
            envelope("engine-observation:nan", 2, "bio", "time_advance", {"ticks": math.nan})

        incomplete_outcome = envelope(
            "engine-outcome:incomplete",
            1,
            "elias",
            "engine_proposal_outcome",
            {"status": "rejected", "engine_event_id": None},
            correlation_id="world-proposal:incomplete",
        )
        with self.assertRaisesRegex(BridgeValidationError, "payload fields"):
            EngineDecision("rejected", incomplete_outcome)

        proposal = h2_bridge().ingest_engine_observation(
            envelope("engine-observation:state-version", 1, "bio", "time_advance", {"ticks": 1})
        )[0]
        applied = decide(engine_authority(), proposal)
        for malformed_version in (True, 1.0):
            malformed_event = Envelope.from_dict(
                {
                    **applied.engine_event.to_dict(),
                    "payload": {
                        **applied.engine_event.to_dict()["payload"],
                        "state_version": malformed_version,
                    },
                }
            )
            with self.assertRaisesRegex(BridgeValidationError, "event state_version"):
                EngineDecision("applied", applied.outcome, malformed_event)

    def test_observation_and_successful_proposal_are_exactly_once(self):
        bridge = h2_bridge()
        authority = engine_authority()
        observation = envelope("engine-observation:1", 1, "bio", "time_advance", {"ticks": 1})

        proposals = bridge.ingest_engine_observation(observation)
        world_events = bridge.world.events
        elias = next(item for item in proposals if item.actor_id == "elias")
        self.assertEqual(elias.payload["action_type"], "routine_move")
        self.assertEqual(elias.payload["destination"], "ferry-dock")
        self.assertEqual(bridge.world.trace(elias.payload["causal_event_id"])["event"]["actor"], "elias")

        forged_outcome = envelope(
            f"engine-outcome:{elias.message_id}",
            1,
            "elias",
            "engine_proposal_outcome",
            {
                "authority_proof": "0" * 128,
                "engine_event_id": None,
                "reason": "forged_rejection",
                "state_version": 0,
                "status": "rejected",
            },
            correlation_id=elias.message_id,
        )
        with self.assertRaisesRegex(BridgeValidationError, "authority proof"):
            bridge.receive_engine_decision(EngineDecision("rejected", forged_outcome))

        attacker_authority = EngineAuthority(
            {"elias": "village-square", "nella": "village-square", "mara": "captain-post"},
            {
                "elias": {"routine_move"},
                "nella": {"routine_move"},
                "mara": {"independent_choice", "values_refusal"},
            },
            {"ferry-dock", "bakery", "captain-post"},
            PROPOSAL_ORIGIN_KEY,
            ATTACKER_SIGNING_KEY,
        )
        with self.assertRaisesRegex(BridgeValidationError, "authority proof"):
            bridge.receive_engine_decision(decide(attacker_authority, elias))

        decision = decide(authority, elias)
        self.assertEqual(decision.status, "applied")
        self.assertEqual(decision.engine_event.correlation_id, elias.message_id)
        self.assertEqual(decision.outcome.payload["engine_event_id"], decision.engine_event.message_id)
        tampered_event = Envelope.from_dict(
            {
                **decision.engine_event.to_dict(),
                "payload": {**decision.engine_event.to_dict()["payload"], "command": "engine-chose-a-different-action"},
            }
        )
        with self.assertRaises(BridgeValidationError):
            bridge.receive_engine_decision(EngineDecision("applied", decision.outcome, tampered_event))
        bridge.receive_engine_decision(decision)

        invalid = envelope(
            "engine-observation:invalid-request",
            2,
            "bio",
            "npc_request",
            {"target_id": "mara", "action": "invent-an-engine-side-choice"},
        )
        before_invalid = bridge.world.state_digest()
        with self.assertRaises(BridgeValidationError):
            bridge.ingest_engine_observation(invalid)
        self.assertEqual(bridge.world.state_digest(), before_invalid)

        before_retry = bridge.world.state_digest()
        self.assertEqual(bridge.ingest_engine_observation(observation), proposals)
        self.assertEqual(bridge.world.state_digest(), before_retry)
        self.assertEqual(bridge.world.events, world_events)
        self.assertIs(decide(authority, elias), decision)
        self.assertEqual(authority.state()["state_version"], 1)
        self.assertEqual(authority.state()["positions"]["elias"], "ferry-dock")
        bridge.receive_engine_decision(decision)

        nella = next(item for item in proposals if item.actor_id == "nella")
        conflicting_authority = engine_authority()
        decide(conflicting_authority, nella)
        reused_event_decision = decide(conflicting_authority, elias)
        with self.assertRaisesRegex(BridgeValidationError, "event id was reused"):
            bridge.receive_engine_decision(reused_event_decision)

        restarted_authority = engine_authority()
        reused_version = decide(restarted_authority, nella)
        self.assertNotEqual(reused_version.engine_event.message_id, decision.engine_event.message_id)
        self.assertEqual(reused_version.engine_event.payload["state_version"], 1)
        with self.assertRaisesRegex(BridgeValidationError, "state version was reused"):
            bridge.receive_engine_decision(reused_version)

        lineage_bridge = h2_bridge()
        lineage_proposals = lineage_bridge.ingest_engine_observation(
            envelope("engine-observation:lineage", 1, "bio", "time_advance", {"ticks": 1})
        )
        fork_proposal = next(
            item
            for item in lineage_bridge.ingest_engine_observation(
                envelope("engine-observation:lineage-fork", 2, "bio", "time_advance", {"ticks": 1})
            )
            if item.actor_id == "elias"
        )
        lineage_authority = engine_authority()
        lineage_first = decide(lineage_authority, lineage_proposals[0])
        lineage_second = decide(lineage_authority, lineage_proposals[1])
        lineage_bridge.receive_engine_decision(lineage_second)
        forked_authority = engine_authority()
        decide(forked_authority, lineage_proposals[0])
        forked_first = decide(forked_authority, fork_proposal)
        with self.assertRaisesRegex(BridgeValidationError, "sequence was reused"):
            lineage_bridge.receive_engine_decision(forked_first)
        lineage_bridge.receive_engine_decision(lineage_first)

    def test_engine_applies_cross_actor_proposals_in_global_order(self):
        proposals = h2_bridge().ingest_engine_observation(
            envelope(
                "engine-observation:global-proposal-order",
                1,
                "bio",
                "time_advance",
                {"ticks": 1},
            )
        )
        self.assertGreater(len(proposals), 1)

        forward = engine_authority()
        forward_decisions = []
        for proposal in proposals:
            forward_decisions.extend(forward.validate_and_apply(proposal))

        reverse = engine_authority()
        reverse_decisions = []
        for proposal in reversed(proposals):
            reverse_decisions.extend(reverse.validate_and_apply(proposal))

        self.assertEqual(len(forward_decisions), len(proposals))
        self.assertEqual(len(reverse_decisions), len(proposals))
        self.assertEqual(reverse.snapshot_digest(), forward.snapshot_digest())

    def test_state_version_changes_are_bounded_by_outcome_ordering(self):
        bridge = h2_bridge()
        first_proposals = bridge.ingest_engine_observation(
            envelope("engine-observation:rollback:1", 1, "bio", "time_advance", {"ticks": 1})
        )
        first = next(item for item in first_proposals if item.actor_id == "elias")
        second_proposals = bridge.ingest_engine_observation(
            envelope("engine-observation:rollback:2", 2, "bio", "time_advance", {"ticks": 1})
        )
        second = next(item for item in second_proposals if item.actor_id == "elias")

        current_authority = engine_authority()
        applied = decide(current_authority, first)
        self.assertEqual(
            (applied.outcome.sequence, applied.outcome.payload["state_version"]),
            (1, 1),
        )
        bridge.receive_engine_decision(applied)

        stale_authority = EngineAuthority(
            {"elias": "village-square"},
            {"elias": set()},
            {"ferry-dock"},
            PROPOSAL_ORIGIN_KEY,
        )
        self.assertEqual(decide(stale_authority, first).outcome.sequence, 1)
        stale_rejection = decide(stale_authority, second)
        self.assertEqual(
            (stale_rejection.outcome.sequence, stale_rejection.outcome.payload["state_version"]),
            (2, 0),
        )
        before_rollback = bridge.world.state_digest()
        with self.assertRaisesRegex(BridgeValidationError, "state version conflicts"):
            bridge.receive_engine_decision(stale_rejection)
        self.assertEqual(bridge.world.state_digest(), before_rollback)

        hidden_mutation_bridge = h2_bridge()
        hidden_first = next(
            item
            for item in hidden_mutation_bridge.ingest_engine_observation(
                envelope("engine-observation:hidden:1", 1, "bio", "time_advance", {"ticks": 1})
            )
            if item.actor_id == "elias"
        )
        hidden_second = next(
            item
            for item in hidden_mutation_bridge.ingest_engine_observation(
                envelope("engine-observation:hidden:2", 2, "bio", "time_advance", {"ticks": 1})
            )
            if item.actor_id == "elias"
        )
        rejecting_fork = EngineAuthority(
            {"elias": "village-square"},
            {"elias": set()},
            {"ferry-dock"},
            PROPOSAL_ORIGIN_KEY,
        )
        hidden_mutation_bridge.receive_engine_decision(decide(rejecting_fork, hidden_first))
        applying_fork = engine_authority()
        hidden_first_applied = decide(applying_fork, hidden_first)
        self.assertEqual(hidden_first_applied.outcome.payload["state_version"], 1)
        hidden_second_applied = decide(applying_fork, hidden_second)
        self.assertEqual(
            (hidden_second_applied.outcome.sequence, hidden_second_applied.outcome.payload["state_version"]),
            (2, 2),
        )
        self.assertNotIn(
            "prior_event_digests", hidden_first_applied.engine_event.payload
        )
        self.assertIsNone(
            hidden_first_applied.engine_event.payload["prior_event_digest"]
        )
        self.assertEqual(
            hidden_second_applied.engine_event.payload["prior_event_digest"],
            hidden_first_applied.engine_event.digest(),
        )
        before_hidden = hidden_mutation_bridge.world.state_digest()
        with self.assertRaisesRegex(BridgeValidationError, "state version conflicts"):
            hidden_mutation_bridge.receive_engine_decision(hidden_second_applied)
        self.assertEqual(hidden_mutation_bridge.world.state_digest(), before_hidden)

        rejected_jump_bridge = h2_bridge()
        first_batch = rejected_jump_bridge.ingest_engine_observation(
            envelope("engine-observation:rejected-jump:1", 1, "bio", "time_advance", {"ticks": 1})
        )
        rejected_first = next(item for item in first_batch if item.actor_id == "elias")
        privately_applied = rejected_first
        rejected_second = next(item for item in first_batch if item.actor_id == "nella")
        visible_rejecting_fork = EngineAuthority(
            {"elias": "village-square"},
            {"elias": set()},
            {"ferry-dock"},
            PROPOSAL_ORIGIN_KEY,
        )
        visible_first_rejection = decide(visible_rejecting_fork, rejected_first)
        mixed_fork = EngineAuthority(
            {"elias": "village-square", "nella": "village-square"},
            {"elias": {"routine_move"}, "nella": set()},
            {"ferry-dock", "bakery"},
            PROPOSAL_ORIGIN_KEY,
        )
        self.assertEqual(decide(mixed_fork, privately_applied).status, "applied")
        rejected_after_hidden_apply = decide(mixed_fork, rejected_second)
        self.assertEqual(
            (
                rejected_after_hidden_apply.status,
                rejected_after_hidden_apply.outcome.sequence,
                rejected_after_hidden_apply.outcome.payload["state_version"],
            ),
            ("rejected", 2, 1),
        )
        rejected_jump_bridge.receive_engine_decision(rejected_after_hidden_apply)
        buffered_state = rejected_jump_bridge.world.extension_state("h2_bridge")
        self.assertEqual(
            (len(buffered_state["decisions"]), len(buffered_state["buffered_decisions"])),
            (0, 1),
        )
        rejected_jump_bridge.receive_engine_decision(visible_first_rejection)
        committed_state = rejected_jump_bridge.world.extension_state("h2_bridge")
        self.assertEqual(
            (len(committed_state["decisions"]), len(committed_state["buffered_decisions"])),
            (1, 0),
        )
        valid_second_rejection = decide(visible_rejecting_fork, rejected_second)
        self.assertEqual(valid_second_rejection.outcome.payload["state_version"], 0)
        rejected_jump_bridge.receive_engine_decision(valid_second_rejection)

    def test_out_of_order_engine_decisions_buffer_across_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "buffered-decisions.json"
            bridge = h2_bridge()
            proposals = bridge.ingest_engine_observation(
                envelope("engine-observation:decision-buffer", 1, "bio", "time_advance", {"ticks": 1})
            )
            authority = engine_authority()
            first = decide(authority, proposals[0])
            second = decide(authority, proposals[1])
            bridge.receive_engine_decision(second)
            buffered_state = bridge.world.extension_state("h2_bridge")
            self.assertEqual(
                (len(buffered_state["decisions"]), len(buffered_state["buffered_decisions"])),
                (0, 1),
            )

            trusted = bridge.world.state_digest()
            bridge.world.save(path)

            schema_two_world = World.load(path, expected_state_digest=trusted)
            schema_two_state = schema_two_world.extension_state("h2_bridge")
            schema_two_state["schema_version"] = 2
            schema_two_state["decisions"] = schema_two_state.pop("buffered_decisions")
            event_digest = second.engine_event.digest()
            schema_two_state["engine_events"] = {
                second.engine_event.message_id: event_digest
            }
            schema_two_state["engine_versions"] = {
                "1": first.engine_event.digest(),
                str(second.engine_event.payload["state_version"]): event_digest,
            }
            schema_two_world.set_extension_state("h2_bridge", schema_two_state)
            schema_two_digest = schema_two_world.state_digest()
            schema_two_world.save(path)
            migrated = WorldOSBridge(
                World.load(path, expected_state_digest=schema_two_digest),
                {"ferryman": "ferry-dock", "baker": "bakery"},
                PROPOSAL_ORIGIN_KEY,
            )
            migrated_state = migrated.world.extension_state("h2_bridge")
            self.assertEqual(
                (
                    migrated_state["schema_version"],
                    len(migrated_state["decisions"]),
                    len(migrated_state["buffered_decisions"]),
                    migrated_state["engine_events"],
                    migrated_state["engine_versions"],
                ),
                (11, 0, 1, {}, {}),
            )

            bridge.world.save(path)
            resumed = WorldOSBridge(
                World.load(path, expected_state_digest=trusted),
                {"ferryman": "ferry-dock", "baker": "bakery"},
                PROPOSAL_ORIGIN_KEY,
            )
            resumed.receive_engine_decision(first)
            resumed_state = resumed.world.extension_state("h2_bridge")
            self.assertEqual(
                (len(resumed_state["decisions"]), len(resumed_state["buffered_decisions"])),
                (2, 0),
            )

    def test_invalid_replacement_does_not_remove_a_later_buffered_outcome(self):
        bridge = h2_bridge()
        first_batch = bridge.ingest_engine_observation(
            envelope("engine-observation:buffer-retention:1", 1, "bio", "time_advance", {"ticks": 1})
        )
        first = next(item for item in first_batch if item.actor_id == "elias")
        middle = next(item for item in first_batch if item.actor_id == "nella")
        later = next(
            item
            for item in bridge.ingest_engine_observation(
                envelope("engine-observation:buffer-retention:2", 2, "bio", "time_advance", {"ticks": 1})
            )
            if item.actor_id == "elias"
        )
        authority = engine_authority()
        first_applied = decide(authority, first)
        middle_applied = decide(authority, middle)
        later_applied = decide(authority, later)
        bridge.receive_engine_decision(first_applied)
        bridge.receive_engine_decision(later_applied)

        stale = EngineAuthority(
            {"elias": "village-square"},
            {"elias": set()},
            {"ferry-dock"},
            PROPOSAL_ORIGIN_KEY,
        )
        decide(stale, first)
        invalid_replacement = decide(stale, later)
        replacement_outcome = Envelope.from_dict(
            {
                **invalid_replacement.outcome.to_dict(),
                "message_id": "engine-outcome:buffer-retention:replacement",
                "payload": {
                    **invalid_replacement.outcome.to_dict()["payload"],
                    "authority_proof": "0" * 128,
                },
            }
        )
        replacement_proof = AUTHORITY_SIGNING_KEY.sign(
            _authority_material(replacement_outcome, invalid_replacement.engine_event)
        ).hex()
        replacement_outcome = Envelope.from_dict(
            {
                **replacement_outcome.to_dict(),
                "payload": {
                    **replacement_outcome.to_dict()["payload"],
                    "authority_proof": replacement_proof,
                },
            }
        )
        invalid_replacement = EngineDecision(
            invalid_replacement.status,
            replacement_outcome,
            invalid_replacement.engine_event,
        )
        with self.assertRaisesRegex(BridgeValidationError, "state version conflicts"):
            bridge.receive_engine_decision(invalid_replacement)
        retained_state = bridge.world.extension_state("h2_bridge")
        self.assertEqual(
            (len(retained_state["decisions"]), len(retained_state["buffered_decisions"])),
            (1, 1),
        )

        bridge.receive_engine_decision(middle_applied)
        drained_state = bridge.world.extension_state("h2_bridge")
        self.assertEqual(
            (len(drained_state["decisions"]), len(drained_state["buffered_decisions"])),
            (3, 0),
        )

    def test_missing_persisted_causal_event_fails_as_bridge_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing-causal-event.json"
            bridge = h2_bridge()
            proposals = bridge.ingest_engine_observation(
                envelope("engine-observation:missing-causal", 1, "bio", "time_advance", {"ticks": 1})
            )
            original = proposals[0]
            later_proposals = bridge.ingest_engine_observation(
                envelope("engine-observation:alternate-causal", 2, "bio", "time_advance", {"ticks": 1})
            )
            clean_state = json.loads(
                json.dumps(bridge.world.extension_state("h2_bridge"))
            )
            unsigned = Envelope.from_dict(
                {
                    **original.to_dict(),
                    "payload": {
                        **original.to_dict()["payload"],
                        "causal_event_id": "event-that-does-not-exist",
                        "origin_proof": "0" * 64,
                    },
                }
            )
            authenticated = Envelope.from_dict(
                {
                    **unsigned.to_dict(),
                    "payload": {
                        **unsigned.to_dict()["payload"],
                        "origin_proof": _origin_proof(unsigned, PROPOSAL_ORIGIN_KEY),
                    },
                }
            )
            state = bridge.world.extension_state("h2_bridge")
            state["pending"] = [
                authenticated.to_dict() if item["message_id"] == original.message_id else item
                for item in state["pending"]
            ]
            for observation in state["observations"]:
                observation["proposals"] = [
                    authenticated.to_dict()
                    if item["message_id"] == original.message_id
                    else item
                    for item in observation["proposals"]
                ]
            for message_id, delivered in state["delivery_results"].items():
                state["delivery_results"][message_id] = [
                    authenticated.to_dict()
                    if item["message_id"] == original.message_id
                    else item
                    for item in delivered
                ]
            bridge.world.set_extension_state("h2_bridge", state)
            trusted = bridge.world.state_digest()
            bridge.world.save(path)

            with self.assertRaisesRegex(BridgeValidationError, "causal event reference"):
                WorldOSBridge(
                    World.load(path, expected_state_digest=trusted),
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

            alternate_source = next(
                proposal
                for proposal in later_proposals
                if proposal.actor_id == original.actor_id
            )
            alternate_event_id = alternate_source.payload["causal_event_id"]
            alternate_unsigned = Envelope.from_dict(
                {
                    **original.to_dict(),
                    "message_id": (
                        f"world-proposal:{original.correlation_id}:{alternate_event_id}"
                    ),
                    "payload": {
                        **original.to_dict()["payload"],
                        "causal_event_id": alternate_event_id,
                        "origin_proof": "0" * 64,
                    },
                }
            )
            alternate = Envelope.from_dict(
                {
                    **alternate_unsigned.to_dict(),
                    "payload": {
                        **alternate_unsigned.to_dict()["payload"],
                        "origin_proof": _origin_proof(
                            alternate_unsigned, PROPOSAL_ORIGIN_KEY
                        ),
                    },
                }
            )
            state["pending"] = [
                alternate.to_dict() if item["message_id"] == original.message_id else item
                for item in state["pending"]
            ]
            for observation in state["observations"]:
                observation["proposals"] = [
                    alternate.to_dict()
                    if item["message_id"] == original.message_id
                    else item
                    for item in observation["proposals"]
                ]
            for message_id, delivered in state["delivery_results"].items():
                state["delivery_results"][message_id] = [
                    alternate.to_dict()
                    if item["message_id"] == original.message_id
                    else item
                    for item in delivered
                ]
            bridge.world.set_extension_state("h2_bridge", state)
            trusted = bridge.world.state_digest()
            bridge.world.save(path)
            with self.assertRaisesRegex(BridgeValidationError, "causal tick|originate from its observation"):
                WorldOSBridge(
                    World.load(path, expected_state_digest=trusted),
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

            omitted_state = json.loads(json.dumps(clean_state))
            later_ids = {proposal.message_id for proposal in later_proposals}
            omitted_state["pending"] = [
                alternate.to_dict()
                if item["message_id"] == original.message_id
                else item
                for item in omitted_state["pending"]
                if item["message_id"] not in later_ids
            ]
            for observation in omitted_state["observations"]:
                observation["proposals"] = [
                    alternate.to_dict()
                    if item["message_id"] == original.message_id
                    else item
                    for item in observation["proposals"]
                    if item["message_id"] not in later_ids
                ]
            for message_id, delivered in omitted_state["delivery_results"].items():
                omitted_state["delivery_results"][message_id] = [
                    alternate.to_dict()
                    if item["message_id"] == original.message_id
                    else item
                    for item in delivered
                    if item["message_id"] not in later_ids
                ]
            omitted_state["proposal_sequence"] = {}
            omitted_state["proposal_global_order"] = len(
                omitted_state["pending"]
            )
            for item in omitted_state["pending"]:
                actor_id = item["actor_id"]
                omitted_state["proposal_sequence"][actor_id] = max(
                    omitted_state["proposal_sequence"].get(actor_id, 0), item["sequence"]
                )
            bridge.world.set_extension_state("h2_bridge", omitted_state)
            trusted = bridge.world.state_digest()
            bridge.world.save(path)
            with self.assertRaisesRegex(BridgeValidationError, "observation"):
                WorldOSBridge(
                    World.load(path, expected_state_digest=trusted),
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

            truncated_state = json.loads(json.dumps(clean_state))
            later_observation_id = later_proposals[0].correlation_id
            truncated_state["observations"] = [
                item
                for item in truncated_state["observations"]
                if item["envelope"]["message_id"] != later_observation_id
            ]
            truncated_state["pending"] = [
                item
                for item in truncated_state["pending"]
                if item["message_id"] not in later_ids
            ]
            truncated_state["delivery_results"].pop(later_observation_id)
            truncated_state["delivery_observations"].pop(later_observation_id)
            truncated_state["last_engine_sequence"]["bio"] = 1
            truncated_state["proposal_sequence"] = {}
            truncated_state["proposal_global_order"] = len(
                truncated_state["pending"]
            )
            for item in truncated_state["pending"]:
                actor_id = item["actor_id"]
                truncated_state["proposal_sequence"][actor_id] = max(
                    truncated_state["proposal_sequence"].get(actor_id, 0),
                    item["sequence"],
                )
            bridge.world.set_extension_state("h2_bridge", truncated_state)
            trusted = bridge.world.state_digest()
            bridge.world.save(path)
            with self.assertRaisesRegex(BridgeValidationError, "bridge start"):
                WorldOSBridge(
                    World.load(path, expected_state_digest=trusted),
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

    def test_legacy_causal_binding_marker_survives_repeated_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy-causal-binding.json"
            bridge = h2_bridge()
            bridge.ingest_engine_observation(
                envelope("engine-observation:legacy-causal", 1, "bio", "time_advance", {"ticks": 1})
            )
            trusted = bridge.world.state_digest()
            bridge.world.save(path)

            saved = json.loads(path.read_text(encoding="utf-8"))
            for event in saved["state"]["events"]:
                if event["event_type"] == "time_advanced":
                    event["root_input"] = f"tick:{event['tick']}"
            extension = saved["state"]["extensions"]["h2_bridge"]
            extension["schema_version"] = 3
            extension.pop("legacy_time_observations")
            extension.pop("legacy_time_anchors")
            canonical = json.dumps(
                saved["state"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
            saved["digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            path.write_text(json.dumps(saved), encoding="utf-8")

            legacy_digest = saved["digest"]
            migrated = WorldOSBridge(
                World.load(path, expected_state_digest=legacy_digest),
                {"ferryman": "ferry-dock", "baker": "bakery"},
                PROPOSAL_ORIGIN_KEY,
            )
            migrated_state = migrated.world.extension_state("h2_bridge")
            self.assertEqual(
                (
                    migrated_state["schema_version"],
                    migrated_state["legacy_time_observations"],
                ),
                (11, ["engine-observation:legacy-causal"]),
            )
            migrated.ingest_engine_observation(
                envelope(
                    "engine-observation:direct-after-legacy",
                    2,
                    "bio",
                    "time_advance",
                    {"ticks": 1},
                )
            )
            migrated_digest = migrated.world.state_digest()
            migrated.world.save(path)
            reloaded = WorldOSBridge(
                World.load(path, expected_state_digest=migrated_digest),
                {"ferryman": "ferry-dock", "baker": "bakery"},
                PROPOSAL_ORIGIN_KEY,
            )
            self.assertEqual(
                reloaded.world.extension_state("h2_bridge")["legacy_time_observations"],
                ["engine-observation:legacy-causal"],
            )

            direct_bridge = h2_bridge()
            direct_bridge.ingest_engine_observation(
                envelope("engine-observation:schema-four-direct", 1, "bio", "time_advance", {"ticks": 1})
            )
            direct_state = direct_bridge.world.extension_state("h2_bridge")
            direct_state["schema_version"] = 4
            direct_state.pop("legacy_time_observations")
            direct_state.pop("bridge_start_proof")
            direct_bridge.world.set_extension_state("h2_bridge", direct_state)
            direct_digest = direct_bridge.world.state_digest()
            direct_bridge.world.save(path)
            direct_migrated = WorldOSBridge(
                World.load(path, expected_state_digest=direct_digest),
                {"ferryman": "ferry-dock", "baker": "bakery"},
                PROPOSAL_ORIGIN_KEY,
            )
            self.assertEqual(
                direct_migrated.world.extension_state("h2_bridge")[
                    "legacy_time_observations"
                ],
                [],
            )

    def test_orphaned_legacy_time_root_fails_restore(self):
        bridge = h2_bridge()
        observation = envelope(
            "engine-observation:orphaned-legacy",
            1,
            "bio",
            "time_advance",
            {"ticks": 1},
        )
        bridge.ingest_engine_observation(observation)
        for event in bridge.world._events:
            if event.event_type == "time_advanced":
                object.__setattr__(event, "root_input", f"tick:{event.tick}")
        legacy_state = bridge.world.extension_state("h2_bridge")
        legacy_state["schema_version"] = 5
        legacy_state.pop("legacy_time_anchors")
        legacy_state["legacy_time_observations"] = [observation.message_id]
        bridge.world.set_extension_state("h2_bridge", legacy_state)

        migrated = WorldOSBridge(
            bridge.world,
            {"ferryman": "ferry-dock", "baker": "bakery"},
            PROPOSAL_ORIGIN_KEY,
        )
        orphaned_state = migrated.world.extension_state("h2_bridge")
        orphaned_state["observations"] = []
        orphaned_state["pending"] = []
        orphaned_state["delivery_results"] = {}
        orphaned_state["delivery_observations"] = {}
        orphaned_state["last_engine_sequence"] = {}
        orphaned_state["proposal_sequence"] = {}
        orphaned_state["proposal_global_order"] = 0
        orphaned_state["legacy_time_observations"] = []
        orphaned_state["legacy_time_anchors"] = {}
        orphaned_state["bridge_start_tick"] = migrated.world.tick
        migrated.world.set_extension_state("h2_bridge", orphaned_state)

        with self.assertRaisesRegex(BridgeValidationError, "start proof"):
            WorldOSBridge(
                migrated.world,
                {"ferryman": "ferry-dock", "baker": "bakery"},
                PROPOSAL_ORIGIN_KEY,
            )

    def test_bridge_preserves_a_nonzero_start_boundary(self):
        world = albion_world(2202)
        world.advance()
        bridge = WorldOSBridge(
            world,
            {"ferryman": "ferry-dock", "baker": "bakery"},
            PROPOSAL_ORIGIN_KEY,
        )
        WorldOSBridge(
            bridge.world,
            {"ferryman": "ferry-dock", "baker": "bakery"},
            PROPOSAL_ORIGIN_KEY,
        )
        bridge.ingest_engine_observation(
            envelope(
                "engine-observation:after-prebridge-tick",
                1,
                "bio",
                "time_advance",
                {"ticks": 1},
            )
        )
        self.assertEqual(
            bridge.world.extension_state("h2_bridge")["bridge_start_tick"], 1
        )
        previous_state = bridge.world.extension_state("h2_bridge")
        previous_state["schema_version"] = 8
        previous_state.pop("bridge_start_proof")
        bridge.world.set_extension_state("h2_bridge", previous_state)
        with self.assertRaisesRegex(BridgeValidationError, "unverifiable"):
            WorldOSBridge(
                bridge.world,
                {"ferryman": "ferry-dock", "baker": "bakery"},
                PROPOSAL_ORIGIN_KEY,
            )

    def test_bridge_root_without_the_extension_fails_initialization(self):
        bridge = h2_bridge()
        bridge.ingest_engine_observation(
            envelope(
                "engine-observation:missing-extension",
                1,
                "bio",
                "npc_request",
                {"target_id": "mara", "action": "wait"},
            )
        )
        bridge.world._extensions.pop("h2_bridge")
        with self.assertRaisesRegex(BridgeValidationError, "without its observation ledger"):
            WorldOSBridge(
                bridge.world,
                {"ferryman": "ferry-dock", "baker": "bakery"},
                PROPOSAL_ORIGIN_KEY,
            )

        legacy_world = albion_world(2202)
        legacy_time_event = legacy_world.advance()[0]
        legacy_world.record_bridge_causal_anchor(
            "engine-observation:missing-legacy-extension", legacy_time_event.id
        )
        with self.assertRaisesRegex(BridgeValidationError, "without its observation ledger"):
            WorldOSBridge(
                legacy_world,
                {"ferryman": "ferry-dock", "baker": "bakery"},
                PROPOSAL_ORIGIN_KEY,
            )

    def test_incompatible_actor_id_fails_before_world_mutation(self):
        world = World(
            2204,
            [
                Actor("bio one", "Bio One", "bio", "wanderer", ("freedom",)),
                Actor("mara", "Mara", "thinker", "captain", ("protect_community",)),
            ],
            crisis_actor="mara",
        )
        before = world.state_digest()
        with self.assertRaisesRegex(BridgeValidationError, "bridge-compatible"):
            WorldOSBridge(world, {}, PROPOSAL_ORIGIN_KEY)
        self.assertEqual(world.state_digest(), before)

    def test_h2_bridge_rejects_multiple_bios_before_world_mutation(self):
        world = World(
            2204,
            [
                Actor("bio-a", "Bio A", "bio", "wanderer", ("freedom",)),
                Actor("bio-b", "Bio B", "bio", "wanderer", ("freedom",)),
                Actor("mara", "Mara", "thinker", "captain", ("protect_community",)),
            ],
            crisis_actor="mara",
        )
        before = world.state_digest()
        with self.assertRaisesRegex(BridgeValidationError, "exactly one Bio"):
            WorldOSBridge(world, {}, PROPOSAL_ORIGIN_KEY)
        self.assertEqual(world.state_digest(), before)

    def test_orphaned_request_root_fails_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "orphaned-request-root.json"
            bridge = h2_bridge()
            request = envelope(
                "engine-observation:orphaned-request",
                1,
                "bio",
                "npc_request",
                {"target_id": "mara", "action": "wait"},
            )
            bridge.ingest_engine_observation(request)
            state = bridge.world.extension_state("h2_bridge")
            state["observations"] = []
            state["pending"] = []
            state["delivery_results"] = {}
            state["delivery_observations"] = {}
            state["last_engine_sequence"] = {}
            state["proposal_sequence"] = {}
            state["proposal_global_order"] = 0
            bridge.world.set_extension_state("h2_bridge", state)
            trusted = bridge.world.state_digest()
            bridge.world.save(path)
            with self.assertRaisesRegex(BridgeValidationError, "bridge-rooted events"):
                WorldOSBridge(
                    World.load(path, expected_state_digest=trusted),
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

    def test_legacy_proposals_are_authenticated_before_global_order_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy-proposal-migration.json"
            bridge = h2_bridge()
            bridge.ingest_engine_observation(
                envelope(
                    "engine-observation:legacy-proposal-migration",
                    1,
                    "bio",
                    "time_advance",
                    {"ticks": 1},
                )
            )
            legacy_state = bridge.world.extension_state("h2_bridge")
            legacy_state.pop("proposal_global_order")
            migrated_by_id = {
                item["message_id"]: legacy_proposal(
                    Envelope.from_dict(item)
                ).to_dict()
                for item in legacy_state["pending"]
            }
            legacy_state["pending"] = [
                migrated_by_id[item["message_id"]]
                for item in legacy_state["pending"]
            ]
            for observation in legacy_state["observations"]:
                observation["proposals"] = [
                    migrated_by_id[item["message_id"]]
                    for item in observation["proposals"]
                ]
            for message_id, proposals in legacy_state[
                "delivery_results"
            ].items():
                legacy_state["delivery_results"][message_id] = [
                    migrated_by_id[item["message_id"]]
                    for item in proposals
                ]

            bridge.world.set_extension_state("h2_bridge", legacy_state)
            trusted = bridge.world.state_digest()
            bridge.world.save(path)
            migrated = WorldOSBridge(
                World.load(path, expected_state_digest=trusted),
                {"ferryman": "ferry-dock", "baker": "bakery"},
                PROPOSAL_ORIGIN_KEY,
            )
            self.assertEqual(
                migrated.world.extension_state("h2_bridge")[
                    "proposal_global_order"
                ],
                len(migrated_by_id),
            )

            tampered_state = json.loads(json.dumps(legacy_state))
            target_id = tampered_state["pending"][0]["message_id"]
            for item in tampered_state["pending"]:
                if item["message_id"] == target_id:
                    item["payload"]["command"] = "forged command"
            for observation in tampered_state["observations"]:
                for item in observation["proposals"]:
                    if item["message_id"] == target_id:
                        item["payload"]["command"] = "forged command"
            for proposals in tampered_state["delivery_results"].values():
                for item in proposals:
                    if item["message_id"] == target_id:
                        item["payload"]["command"] = "forged command"
            bridge.world.set_extension_state("h2_bridge", tampered_state)
            tampered_digest = bridge.world.state_digest()
            bridge.world.save(path)
            with self.assertRaisesRegex(
                BridgeValidationError, "legacy proposal origin proof"
            ):
                WorldOSBridge(
                    World.load(path, expected_state_digest=tampered_digest),
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

    def test_observation_ledger_and_proposals_survive_world_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.json"
            bridge = h2_bridge()
            observation = envelope("engine-observation:persisted", 1, "bio", "time_advance", {"ticks": 1})
            proposals = bridge.ingest_engine_observation(observation)
            authority = engine_authority()
            decision = authority.validate_and_apply(proposals[0])[0]
            bridge.receive_engine_decision(decision)
            trusted = bridge.world.state_digest()
            bridge.world.save(path)

            loaded_world = type(bridge.world).load(path, expected_state_digest=trusted)
            resumed = WorldOSBridge(
                loaded_world,
                {"ferryman": "ferry-dock", "baker": "bakery"},
                PROPOSAL_ORIGIN_KEY,
            )
            before_retry = resumed.world.state_digest()
            self.assertEqual(resumed.ingest_engine_observation(observation), proposals)
            self.assertEqual(resumed.world.state_digest(), before_retry)
            self.assertEqual(resumed.world.tick, 1)
            resumed.receive_engine_decision(decision)
            self.assertEqual(resumed.world.state_digest(), before_retry)

            pre_versioned_world = type(bridge.world).load(path, expected_state_digest=trusted)
            pre_versioned_state = pre_versioned_world.extension_state("h2_bridge")
            pre_versioned_state["schema_version"] = 1
            pre_versioned_world.set_extension_state("h2_bridge", pre_versioned_state)
            pre_versioned_digest = pre_versioned_world.state_digest()
            pre_versioned_world.save(path)
            migrated_bridge = WorldOSBridge(
                type(pre_versioned_world).load(path, expected_state_digest=pre_versioned_digest),
                {"ferryman": "ferry-dock", "baker": "bakery"},
                PROPOSAL_ORIGIN_KEY,
            )
            self.assertEqual(
                migrated_bridge.world.extension_state("h2_bridge")["schema_version"],
                11,
            )

            legacy_signature_state = bridge.world.extension_state("h2_bridge")
            legacy_signature_state["schema_version"] = 1
            legacy_signature_state["decisions"][0]["outcome"]["payload"]["authority_proof"] = "0" * 64
            bridge.world.set_extension_state("h2_bridge", legacy_signature_state)
            legacy_signature_digest = bridge.world.state_digest()
            bridge.world.save(path)
            legacy_signature_world = type(bridge.world).load(
                path, expected_state_digest=legacy_signature_digest
            )
            with self.assertRaisesRegex(BridgeValidationError, "signature format is incompatible"):
                WorldOSBridge(
                    legacy_signature_world,
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

            next_observation = envelope("engine-observation:after-reload", 2, "bio", "time_advance", {"ticks": 1})
            next_proposals = resumed.ingest_engine_observation(next_observation)
            self.assertEqual(resumed.world.tick, 2)
            self.assertTrue(all(item.sequence == 2 for item in next_proposals))

            engine_path = Path(directory) / "engine.json"
            authority.save(engine_path)
            engine_digest = authority.snapshot_digest()
            resumed_authority = EngineAuthority.load(
                engine_path,
                PROPOSAL_ORIGIN_KEY,
                expected_snapshot_digest=engine_digest,
            )
            before_engine_retry = resumed_authority.state()
            self.assertEqual(decide(resumed_authority, proposals[0]), decision)
            self.assertEqual(resumed_authority.state(), before_engine_retry)
            self.assertEqual(resumed_authority.state()["state_version"], 1)

            ambiguous_legacy = engine_authority()
            decide(ambiguous_legacy, proposals[-1])
            ambiguous_legacy.save(engine_path)
            ambiguous_payload = json.loads(engine_path.read_text(encoding="utf-8"))
            ambiguous_payload["state"].pop("proposal_order_mode")
            ambiguous_payload["state"]["schema_version"] = 4
            canonical = json.dumps(
                ambiguous_payload["state"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            ambiguous_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            ambiguous_payload["digest"] = ambiguous_digest
            engine_path.write_text(json.dumps(ambiguous_payload), encoding="utf-8")
            with self.assertRaisesRegex(BridgeValidationError, "ambiguous global order"):
                EngineAuthority.load(
                    engine_path,
                    PROPOSAL_ORIGIN_KEY,
                    expected_snapshot_digest=ambiguous_digest,
                )

            buffered_authority = engine_authority()
            first_global, second_global = sorted(
                proposals, key=lambda item: item.payload["global_order"]
            )[:2]
            self.assertEqual(buffered_authority.validate_and_apply(second_global), ())
            self.assertEqual(buffered_authority.state()["state_version"], 0)
            buffered_authority.save(engine_path)
            buffered_digest = buffered_authority.snapshot_digest()
            resumed_buffer = EngineAuthority.load(
                engine_path,
                PROPOSAL_ORIGIN_KEY,
                expected_snapshot_digest=buffered_digest,
            )
            drained = resumed_buffer.validate_and_apply(first_global)
            self.assertEqual([item.status for item in drained], ["applied", "applied"])
            self.assertEqual(
                [item.outcome.correlation_id for item in drained],
                [first_global.message_id, second_global.message_id],
            )
            self.assertEqual(resumed_buffer.state()["state_version"], 2)
            self.assertEqual(resumed_buffer.validate_and_apply(first_global), drained)
            self.assertEqual(
                resumed_buffer.validate_and_apply(second_global), (drained[1],)
            )
            resumed_buffer.save(engine_path)
            drained_digest = resumed_buffer.snapshot_digest()
            retry_buffer = EngineAuthority.load(
                engine_path,
                PROPOSAL_ORIGIN_KEY,
                expected_snapshot_digest=drained_digest,
            )
            self.assertEqual(retry_buffer.validate_and_apply(first_global), drained)

            schema_five_payload = json.loads(
                engine_path.read_text(encoding="utf-8")
            )
            schema_five_payload["state"]["schema_version"] = 5
            schema_five_payload["state"]["response_batches"] = [
                {
                    "message_id": item["message_id"],
                    "decision_ids": [item["message_id"]],
                }
                for item in schema_five_payload["state"]["processed"]
            ]
            canonical = json.dumps(
                schema_five_payload["state"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            schema_five_digest = hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()
            schema_five_payload["digest"] = schema_five_digest
            engine_path.write_text(
                json.dumps(schema_five_payload), encoding="utf-8"
            )
            migrated_schema_five = EngineAuthority.load(
                engine_path,
                PROPOSAL_ORIGIN_KEY,
                expected_snapshot_digest=schema_five_digest,
            )
            self.assertEqual(
                migrated_schema_five.snapshot()["response_batches"], []
            )
            self.assertEqual(
                migrated_schema_five.validate_and_apply(first_global), drained
            )

            first_elias = next(
                item for item in proposals if item.actor_id == "elias"
            )
            second_elias = next(
                item for item in next_proposals if item.actor_id == "elias"
            )
            ambiguous_v2_authority = engine_authority()
            decide(ambiguous_v2_authority, first_elias)
            decide(ambiguous_v2_authority, second_elias)
            ambiguous_v2_authority.save(engine_path)
            version_two_payload = json.loads(engine_path.read_text(encoding="utf-8"))
            version_two_payload["state"].pop("response_batches")
            version_two_payload["state"].pop("proposal_order_mode")
            version_two_payload["state"]["schema_version"] = 2
            canonical = json.dumps(
                version_two_payload["state"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
            version_two_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            version_two_payload["digest"] = version_two_digest
            engine_path.write_text(json.dumps(version_two_payload), encoding="utf-8")
            with self.assertRaisesRegex(BridgeValidationError, "ambiguous response-batch history"):
                EngineAuthority.load(
                    engine_path,
                    PROPOSAL_ORIGIN_KEY,
                    expected_snapshot_digest=version_two_digest,
                )

            authority.save(engine_path)
            legacy_authority_payload = json.loads(engine_path.read_text(encoding="utf-8"))
            legacy_authority_payload["state"].pop("proposal_order_mode")
            legacy_authority_payload["state"]["schema_version"] = 3
            legacy_proof = hmac.new(
                PROPOSAL_ORIGIN_KEY,
                _authority_material(decision.outcome, decision.engine_event),
                hashlib.sha256,
            ).hexdigest()
            legacy_authority_payload["state"]["processed"][0]["decision"]["outcome"]["payload"][
                "authority_proof"
            ] = legacy_proof
            canonical = json.dumps(
                legacy_authority_payload["state"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            legacy_authority_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            legacy_authority_payload["digest"] = legacy_authority_digest
            engine_path.write_text(json.dumps(legacy_authority_payload), encoding="utf-8")
            migrated_signature_authority = EngineAuthority.load(
                engine_path,
                PROPOSAL_ORIGIN_KEY,
                expected_snapshot_digest=legacy_authority_digest,
            )
            self.assertEqual(decide(migrated_signature_authority, proposals[0]), decision)
            self.assertEqual(migrated_signature_authority.snapshot()["schema_version"], 9)

            schema_eight_authority = engine_authority()
            schema_eight_decision = schema_eight_authority._process_proposal(
                proposals[0], legacy_full_lineage=True
            )
            schema_eight_authority.save(engine_path)
            schema_eight_payload = json.loads(
                engine_path.read_text(encoding="utf-8")
            )
            schema_eight_payload["state"]["schema_version"] = 8
            canonical = json.dumps(
                schema_eight_payload["state"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            schema_eight_digest = hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()
            schema_eight_payload["digest"] = schema_eight_digest
            engine_path.write_text(
                json.dumps(schema_eight_payload), encoding="utf-8"
            )
            migrated_schema_eight = EngineAuthority.load(
                engine_path,
                PROPOSAL_ORIGIN_KEY,
                expected_snapshot_digest=schema_eight_digest,
            )
            self.assertEqual(
                decide(migrated_schema_eight, proposals[0]),
                schema_eight_decision,
            )
            self.assertEqual(
                migrated_schema_eight.snapshot()["schema_version"], 9
            )
            migrated_schema_eight.save(engine_path)
            migrated_schema_eight_digest = (
                migrated_schema_eight.snapshot_digest()
            )
            reloaded_schema_eight = EngineAuthority.load(
                engine_path,
                PROPOSAL_ORIGIN_KEY,
                expected_snapshot_digest=migrated_schema_eight_digest,
            )
            self.assertEqual(
                reloaded_schema_eight.snapshot_digest(),
                migrated_schema_eight_digest,
            )

            authority.save(engine_path)
            unambiguous_v2 = json.loads(engine_path.read_text(encoding="utf-8"))
            unambiguous_v2["state"].pop("response_batches")
            unambiguous_v2["state"].pop("proposal_order_mode")
            unambiguous_v2["state"]["schema_version"] = 2
            canonical = json.dumps(
                unambiguous_v2["state"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
            unambiguous_v2_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            unambiguous_v2["digest"] = unambiguous_v2_digest
            engine_path.write_text(json.dumps(unambiguous_v2), encoding="utf-8")
            migrated_v2 = EngineAuthority.load(
                engine_path,
                PROPOSAL_ORIGIN_KEY,
                expected_snapshot_digest=unambiguous_v2_digest,
            )
            self.assertEqual(decide(migrated_v2, proposals[0]), decision)

            authority.save(engine_path)
            previous_payload = json.loads(engine_path.read_text(encoding="utf-8"))
            previous_payload["state"].pop("buffered_proposals")
            previous_payload["state"].pop("response_batches")
            previous_payload["state"].pop("proposal_order_mode")
            previous_payload["state"]["schema_version"] = 1
            canonical = json.dumps(
                previous_payload["state"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
            previous_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            previous_payload["digest"] = previous_digest
            engine_path.write_text(json.dumps(previous_payload), encoding="utf-8")
            migrated_authority = EngineAuthority.load(
                engine_path,
                PROPOSAL_ORIGIN_KEY,
                expected_snapshot_digest=previous_digest,
            )
            self.assertEqual(decide(migrated_authority, proposals[0]), decision)
            self.assertEqual(migrated_authority.snapshot()["schema_version"], 9)

            legacy_out_of_order = engine_authority()
            legacy_second = legacy_out_of_order._process_proposal(
                legacy_proposal(second_elias)
            )
            legacy_first = legacy_out_of_order._process_proposal(
                legacy_proposal(first_elias)
            )
            self.assertEqual((legacy_second.status, legacy_first.reason), ("applied", "stale_sequence"))
            legacy_state = legacy_out_of_order.snapshot()
            legacy_state.pop("buffered_proposals")
            legacy_state.pop("response_batches")
            legacy_state.pop("proposal_order_mode")
            legacy_state["schema_version"] = 1
            canonical = json.dumps(
                legacy_state, sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
            legacy_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            engine_path.write_text(
                json.dumps(
                    {"format": "jarvis-world-h2-engine", "state": legacy_state, "digest": legacy_digest}
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(
                BridgeValidationError, "ambiguous global order"
            ):
                EngineAuthority.load(
                    engine_path,
                    PROPOSAL_ORIGIN_KEY,
                    expected_snapshot_digest=legacy_digest,
                )

            malformed_previous = json.loads(json.dumps(previous_payload))
            del malformed_previous["state"]["processed"][0]["decision"]["outcome"]["sequence"]
            canonical = json.dumps(
                malformed_previous["state"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
            malformed_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            malformed_previous["digest"] = malformed_digest
            engine_path.write_text(json.dumps(malformed_previous), encoding="utf-8")
            with self.assertRaisesRegex(BridgeValidationError, "malformed"):
                EngineAuthority.load(
                    engine_path,
                    PROPOSAL_ORIGIN_KEY,
                    expected_snapshot_digest=malformed_digest,
                )

            missing_index_bridge = h2_bridge()
            indexed_proposals = missing_index_bridge.ingest_engine_observation(
                envelope("engine-observation:indexed", 1, "bio", "time_advance", {"ticks": 1})
            )
            missing_index_bridge.receive_engine_decision(
                decide(engine_authority(), indexed_proposals[0])
            )
            missing_index_state = missing_index_bridge.world.extension_state("h2_bridge")
            missing_index_state["engine_events"] = {}
            missing_index_bridge.world.set_extension_state("h2_bridge", missing_index_state)
            missing_index_digest = missing_index_bridge.world.state_digest()
            missing_index_bridge.world.save(path)
            missing_index_world = type(missing_index_bridge.world).load(
                path, expected_state_digest=missing_index_digest
            )
            with self.assertRaisesRegex(BridgeValidationError, "identity maps do not match applied decisions"):
                WorldOSBridge(
                    missing_index_world,
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

            tampered_delivery_bridge = h2_bridge()
            tampered_delivery_bridge.ingest_engine_observation(
                envelope("engine-observation:delivery", 1, "bio", "time_advance", {"ticks": 1})
            )
            tampered_delivery_state = tampered_delivery_bridge.world.extension_state("h2_bridge")
            tampered_delivery_state["delivery_results"]["engine-observation:delivery"][0]["payload"]["command"] = "tampered"
            tampered_delivery_bridge.world.set_extension_state("h2_bridge", tampered_delivery_state)
            tampered_delivery_digest = tampered_delivery_bridge.world.state_digest()
            tampered_delivery_bridge.world.save(path)
            tampered_delivery_world = type(tampered_delivery_bridge.world).load(
                path, expected_state_digest=tampered_delivery_digest
            )
            with self.assertRaisesRegex(BridgeValidationError, "delivery result"):
                WorldOSBridge(
                    tampered_delivery_world,
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

            schema_ten_bridge = h2_bridge()
            schema_ten_bridge.ingest_engine_observation(
                envelope(
                    "engine-observation:schema-ten",
                    1,
                    "bio",
                    "time_advance",
                    {"ticks": 1},
                )
            )
            schema_ten_state = schema_ten_bridge.world.extension_state("h2_bridge")
            schema_ten_state["schema_version"] = 10
            schema_ten_bridge.world.set_extension_state("h2_bridge", schema_ten_state)
            schema_ten_digest = schema_ten_bridge.world.state_digest()
            schema_ten_bridge.world.save(path)
            migrated_schema_ten = WorldOSBridge(
                type(schema_ten_bridge.world).load(
                    path, expected_state_digest=schema_ten_digest
                ),
                {"ferryman": "ferry-dock", "baker": "bakery"},
                PROPOSAL_ORIGIN_KEY,
            )
            self.assertEqual(
                migrated_schema_ten.world.extension_state("h2_bridge")[
                    "schema_version"
                ],
                11,
            )

            corrupt_legacy_delivery_bridge = h2_bridge()
            corrupt_legacy_delivery_bridge.ingest_engine_observation(
                envelope(
                    "engine-observation:corrupt-legacy-delivery",
                    1,
                    "bio",
                    "time_advance",
                    {"ticks": 1},
                )
            )
            corrupt_legacy_delivery_state = (
                corrupt_legacy_delivery_bridge.world.extension_state("h2_bridge")
            )
            corrupt_legacy_delivery_state["schema_version"] = 9
            corrupt_legacy_delivery_state["delivery_observations"][
                "engine-observation:corrupt-legacy-delivery"
            ].append("does-not-exist")
            corrupt_legacy_delivery_bridge.world.set_extension_state(
                "h2_bridge", corrupt_legacy_delivery_state
            )
            corrupt_legacy_delivery_digest = (
                corrupt_legacy_delivery_bridge.world.state_digest()
            )
            corrupt_legacy_delivery_bridge.world.save(path)
            corrupt_legacy_delivery_world = type(
                corrupt_legacy_delivery_bridge.world
            ).load(path, expected_state_digest=corrupt_legacy_delivery_digest)
            with self.assertRaisesRegex(
                BridgeValidationError, "unknown observation"
            ):
                WorldOSBridge(
                    corrupt_legacy_delivery_world,
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

            incomplete_previous_bridge = h2_bridge()
            incomplete_previous_bridge.ingest_engine_observation(
                envelope("engine-observation:missing-delivery", 1, "bio", "time_advance", {"ticks": 1})
            )
            incomplete_previous_state = incomplete_previous_bridge.world.extension_state("h2_bridge")
            incomplete_previous_state.pop("delivery_observations")
            incomplete_previous_state["delivery_results"] = {}
            incomplete_previous_bridge.world.set_extension_state("h2_bridge", incomplete_previous_state)
            incomplete_previous_digest = incomplete_previous_bridge.world.state_digest()
            incomplete_previous_bridge.world.save(path)
            incomplete_previous_world = type(incomplete_previous_bridge.world).load(
                path, expected_state_digest=incomplete_previous_digest
            )
            with self.assertRaisesRegex(BridgeValidationError, "cover applied observations"):
                WorldOSBridge(
                    incomplete_previous_world,
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

            invalid_observation_bridge = h2_bridge()
            invalid_observation_bridge.ingest_engine_observation(
                envelope("engine-observation:invalid-restored", 1, "bio", "time_advance", {"ticks": 1})
            )
            invalid_observation_state = invalid_observation_bridge.world.extension_state("h2_bridge")
            invalid_observation_state["observations"][0]["envelope"]["payload"] = {"ticks": 2}
            invalid_observation_bridge.world.set_extension_state("h2_bridge", invalid_observation_state)
            invalid_observation_digest = invalid_observation_bridge.world.state_digest()
            invalid_observation_bridge.world.save(path)
            invalid_observation_world = type(invalid_observation_bridge.world).load(
                path, expected_state_digest=invalid_observation_digest
            )
            with self.assertRaisesRegex(BridgeValidationError, "time advance requires one tick"):
                WorldOSBridge(
                    invalid_observation_world,
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

            legacy_bridge = h2_bridge()
            legacy_state = legacy_bridge.world.extension_state("h2_bridge")
            legacy_state.pop("buffered_observations")
            legacy_state.pop("delivery_results")
            legacy_state.pop("delivery_observations")
            legacy_bridge.world.set_extension_state("h2_bridge", legacy_state)
            legacy_digest = legacy_bridge.world.state_digest()
            legacy_bridge.world.save(path)
            migrated = WorldOSBridge(
                type(legacy_bridge.world).load(path, expected_state_digest=legacy_digest),
                {"ferryman": "ferry-dock", "baker": "bakery"},
                PROPOSAL_ORIGIN_KEY,
            )
            migrated_state = migrated.world.extension_state("h2_bridge")
            self.assertEqual(migrated_state["buffered_observations"], [])
            self.assertEqual(migrated_state["delivery_results"], {})

            invalid_bridge = h2_bridge()
            invalid_state = invalid_bridge.world.extension_state("h2_bridge")
            invalid_state["schema_version"] = True
            invalid_bridge.world.set_extension_state("h2_bridge", invalid_state)
            invalid_digest = invalid_bridge.world.state_digest()
            invalid_bridge.world.save(path)
            invalid_world = type(invalid_bridge.world).load(path, expected_state_digest=invalid_digest)
            with self.assertRaisesRegex(BridgeValidationError, "incompatible"):
                WorldOSBridge(
                    invalid_world,
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

            invalid_counter_bridge = h2_bridge()
            invalid_counter_state = invalid_counter_bridge.world.extension_state("h2_bridge")
            invalid_counter_state["last_engine_sequence"] = {"bio": True}
            invalid_counter_bridge.world.set_extension_state("h2_bridge", invalid_counter_state)
            invalid_counter_digest = invalid_counter_bridge.world.state_digest()
            invalid_counter_bridge.world.save(path)
            invalid_counter_world = type(invalid_counter_bridge.world).load(
                path, expected_state_digest=invalid_counter_digest
            )
            with self.assertRaisesRegex(BridgeValidationError, "last_engine_sequence"):
                WorldOSBridge(
                    invalid_counter_world,
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

            authority.save(engine_path)
            engine_payload = json.loads(engine_path.read_text(encoding="utf-8"))
            engine_payload["state"]["schema_version"] = True
            canonical = json.dumps(
                engine_payload["state"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
            invalid_engine_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            engine_payload["digest"] = invalid_engine_digest
            engine_path.write_text(json.dumps(engine_payload), encoding="utf-8")
            with self.assertRaisesRegex(BridgeValidationError, "incompatible"):
                EngineAuthority.load(
                    engine_path,
                    PROPOSAL_ORIGIN_KEY,
                    expected_snapshot_digest=invalid_engine_digest,
                )

            fresh_authority = engine_authority()
            fresh_authority.save(engine_path)
            engine_payload = json.loads(engine_path.read_text(encoding="utf-8"))
            engine_payload["state"]["state_version"] = True
            canonical = json.dumps(
                engine_payload["state"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
            )
            invalid_counter_digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            engine_payload["digest"] = invalid_counter_digest
            engine_path.write_text(json.dumps(engine_payload), encoding="utf-8")
            with self.assertRaisesRegex(BridgeValidationError, "state_version"):
                EngineAuthority.load(
                    engine_path,
                    PROPOSAL_ORIGIN_KEY,
                    expected_snapshot_digest=invalid_counter_digest,
                )

    def test_engine_restore_rejects_duplicate_ordering_slots(self):
        with tempfile.TemporaryDirectory() as directory:
            proposals = h2_bridge().ingest_engine_observation(
                envelope("engine-observation:duplicate-slots", 1, "bio", "time_advance", {"ticks": 1})
            )
            paths = [Path(directory) / f"authority-{index}.json" for index in range(2)]

            def load_payloads(authorities):
                payloads = []
                for authority, path in zip(authorities, paths):
                    authority.save(path)
                    payloads.append(json.loads(path.read_text(encoding="utf-8")))
                return payloads

            def write_payload(payload):
                canonical = json.dumps(
                    payload["state"], sort_keys=True, separators=(",", ":"), ensure_ascii=True
                )
                digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
                payload["digest"] = digest
                paths[0].write_text(json.dumps(payload), encoding="utf-8")
                return digest

            unmoved_payload = load_payloads([engine_authority()])[0]
            unmoved_payload["state"]["positions"]["elias"] = "bakery"
            digest = write_payload(unmoved_payload)
            with self.assertRaisesRegex(BridgeValidationError, "positions do not match applied event history"):
                EngineAuthority.load(paths[0], PROPOSAL_ORIGIN_KEY, expected_snapshot_digest=digest)

            no_gap_buffer = load_payloads([engine_authority()])[0]
            no_gap_buffer["state"]["buffered_proposals"] = [
                legacy_proposal(proposals[0]).to_dict()
            ]
            no_gap_buffer["state"]["proposal_order_mode"] = "legacy"
            digest = write_payload(no_gap_buffer)
            with self.assertRaisesRegex(BridgeValidationError, "does not follow a real gap"):
                EngineAuthority.load(paths[0], PROPOSAL_ORIGIN_KEY, expected_snapshot_digest=digest)

            rejecting_authorities = [
                EngineAuthority(
                    {"elias": "village-square", "nella": "village-square", "mara": "captain-post"},
                    {"elias": set(), "nella": set(), "mara": set()},
                    {"ferry-dock", "bakery", "captain-post"},
                    PROPOSAL_ORIGIN_KEY,
                )
                for _ in range(2)
            ]
            for authority, proposal in zip(rejecting_authorities, proposals):
                self.assertEqual(decide(authority, proposal).status, "rejected")
            rejected_payloads = load_payloads(rejecting_authorities)
            duplicate_decision_sequence = rejected_payloads[0]
            duplicate_decision_sequence["state"]["processed"].extend(
                rejected_payloads[1]["state"]["processed"]
            )
            duplicate_decision_sequence["state"]["last_sequence"].update(
                rejected_payloads[1]["state"]["last_sequence"]
            )
            digest = write_payload(duplicate_decision_sequence)
            with self.assertRaisesRegex(BridgeValidationError, "ordering is not unique and contiguous"):
                EngineAuthority.load(paths[0], PROPOSAL_ORIGIN_KEY, expected_snapshot_digest=digest)

            applying_authorities = [engine_authority(), engine_authority()]
            for authority, proposal in zip(applying_authorities, proposals):
                self.assertEqual(decide(authority, proposal).status, "applied")
            applied_payloads = load_payloads(applying_authorities)
            missing_high_water = json.loads(json.dumps(applied_payloads[0]))
            missing_high_water["state"]["last_sequence"] = {}
            digest = write_payload(missing_high_water)
            with self.assertRaisesRegex(BridgeValidationError, "high-water marks"):
                EngineAuthority.load(paths[0], PROPOSAL_ORIGIN_KEY, expected_snapshot_digest=digest)

            forged_high_water = json.loads(json.dumps(applied_payloads[0]))
            forged_high_water["state"]["processed"][0]["proposal"]["sequence"] = 999
            forged_high_water["state"]["last_sequence"] = {"elias": 999}
            digest = write_payload(forged_high_water)
            with self.assertRaisesRegex(BridgeValidationError, "proposal (origin proof|identity or digest)"):
                EngineAuthority.load(paths[0], PROPOSAL_ORIGIN_KEY, expected_snapshot_digest=digest)

            substituted_proposal = json.loads(json.dumps(applied_payloads[0]))
            alternate_bridge = WorldOSBridge(
                albion_world(2202),
                {"ferryman": "bakery", "baker": "ferry-dock"},
                PROPOSAL_ORIGIN_KEY,
            )
            alternate_proposals = alternate_bridge.ingest_engine_observation(
                envelope("engine-observation:duplicate-slots", 1, "bio", "time_advance", {"ticks": 1})
            )
            processed = substituted_proposal["state"]["processed"][0]
            alternate = next(
                item for item in alternate_proposals
                if item.message_id == processed["proposal"]["message_id"]
            )
            processed["proposal"] = alternate.to_dict()
            processed["proposal_digest"] = alternate.digest()
            digest = write_payload(substituted_proposal)
            with self.assertRaisesRegex(BridgeValidationError, "does not match its authenticated proposal"):
                EngineAuthority.load(paths[0], PROPOSAL_ORIGIN_KEY, expected_snapshot_digest=digest)

            stale_position = json.loads(json.dumps(applied_payloads[0]))
            applied_actor = stale_position["state"]["processed"][0]["proposal"]["actor_id"]
            stale_position["state"]["positions"][applied_actor] = "village-square"
            digest = write_payload(stale_position)
            with self.assertRaisesRegex(BridgeValidationError, "positions do not match applied event history"):
                EngineAuthority.load(paths[0], PROPOSAL_ORIGIN_KEY, expected_snapshot_digest=digest)

            revoked_permission = json.loads(json.dumps(applied_payloads[0]))
            revoked_permission["state"]["permissions"][applied_actor] = []
            digest = write_payload(revoked_permission)
            with self.assertRaisesRegex(BridgeValidationError, "decisions do not match replayed policy"):
                EngineAuthority.load(paths[0], PROPOSAL_ORIGIN_KEY, expected_snapshot_digest=digest)

            duplicate_state_version = applied_payloads[0]
            duplicate_state_version["state"]["processed"].extend(
                applied_payloads[1]["state"]["processed"]
            )
            duplicate_state_version["state"]["last_sequence"].update(
                applied_payloads[1]["state"]["last_sequence"]
            )
            digest = write_payload(duplicate_state_version)
            with self.assertRaisesRegex(BridgeValidationError, "state version is duplicated"):
                EngineAuthority.load(paths[0], PROPOSAL_ORIGIN_KEY, expected_snapshot_digest=digest)

            duplicate_outcome_bridge = h2_bridge()
            duplicate_outcome_proposals = duplicate_outcome_bridge.ingest_engine_observation(
                envelope("engine-observation:duplicate-outcomes", 1, "bio", "time_advance", {"ticks": 1})
            )

            def rejecting_authority():
                return EngineAuthority(
                    {"elias": "village-square", "nella": "village-square", "mara": "captain-post"},
                    {"elias": set(), "nella": set(), "mara": set()},
                    {"ferry-dock", "bakery", "captain-post"},
                    PROPOSAL_ORIGIN_KEY,
                )

            duplicate_outcome_bridge.receive_engine_decision(
                decide(rejecting_authority(), duplicate_outcome_proposals[0])
            )
            duplicate_outcome = decide(rejecting_authority(), duplicate_outcome_proposals[1])
            with self.assertRaisesRegex(BridgeValidationError, "outcome sequence was reused"):
                duplicate_outcome_bridge.receive_engine_decision(duplicate_outcome)

            duplicate_outcome_state = duplicate_outcome_bridge.world.extension_state("h2_bridge")
            duplicate_outcome_state["decisions"].append(duplicate_outcome.to_dict())
            duplicate_outcome_bridge.world.set_extension_state("h2_bridge", duplicate_outcome_state)
            duplicate_outcome_digest = duplicate_outcome_bridge.world.state_digest()
            duplicate_outcome_bridge.world.save(paths[0])
            duplicate_outcome_world = type(duplicate_outcome_bridge.world).load(
                paths[0], expected_state_digest=duplicate_outcome_digest
            )
            with self.assertRaisesRegex(BridgeValidationError, "outcome ordering slot is duplicated"):
                WorldOSBridge(
                    duplicate_outcome_world,
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

    def test_out_of_order_observations_buffer_across_reload_and_apply_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "buffered-world.json"
            bridge = h2_bridge()
            second = envelope("engine-observation:out-of-order:2", 2, "bio", "time_advance", {"ticks": 1})
            self.assertEqual(bridge.ingest_engine_observation(second), ())
            self.assertEqual(bridge.ingest_engine_observation(second), ())
            self.assertEqual(bridge.world.tick, 0)
            trusted = bridge.world.state_digest()
            bridge.world.save(path)

            resumed = WorldOSBridge(
                type(bridge.world).load(path, expected_state_digest=trusted),
                {"ferryman": "ferry-dock", "baker": "bakery"},
                PROPOSAL_ORIGIN_KEY,
            )
            first = envelope("engine-observation:out-of-order:1", 1, "bio", "time_advance", {"ticks": 1})
            delivered = resumed.ingest_engine_observation(first)
            self.assertEqual(resumed.world.tick, 2)
            self.assertEqual(len(delivered), 4)
            self.assertEqual(resumed.ingest_engine_observation(first), delivered)
            second_proposals = resumed.ingest_engine_observation(second)
            self.assertEqual(len(second_proposals), 2)
            self.assertTrue(all(item.correlation_id == second.message_id for item in second_proposals))

            no_gap_bridge = h2_bridge()
            no_gap_bridge.ingest_engine_observation(second)
            no_gap_state = no_gap_bridge.world.extension_state("h2_bridge")
            no_gap_state["buffered_observations"][0]["sequence"] = 1
            no_gap_bridge.world.set_extension_state("h2_bridge", no_gap_state)
            no_gap_digest = no_gap_bridge.world.state_digest()
            no_gap_bridge.world.save(path)
            no_gap_world = type(no_gap_bridge.world).load(
                path, expected_state_digest=no_gap_digest
            )
            with self.assertRaisesRegex(BridgeValidationError, "buffered observation ordering"):
                WorldOSBridge(
                    no_gap_world,
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

            duplicate_bridge = h2_bridge()
            duplicate_bridge.ingest_engine_observation(second)
            duplicate_bridge.ingest_engine_observation(
                envelope("engine-observation:out-of-order:3", 3, "bio", "time_advance", {"ticks": 1})
            )
            duplicate_state = duplicate_bridge.world.extension_state("h2_bridge")
            duplicate_state["buffered_observations"][1]["message_id"] = second.message_id
            duplicate_bridge.world.set_extension_state("h2_bridge", duplicate_state)
            duplicate_digest = duplicate_bridge.world.state_digest()
            duplicate_bridge.world.save(path)
            duplicate_world = type(duplicate_bridge.world).load(
                path, expected_state_digest=duplicate_digest
            )
            with self.assertRaisesRegex(BridgeValidationError, "buffered observation identity is duplicated"):
                WorldOSBridge(
                    duplicate_world,
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

            collision_bridge = h2_bridge()
            first = envelope("engine-observation:out-of-order:1", 1, "bio", "time_advance", {"ticks": 1})
            collision_bridge.ingest_engine_observation(first)
            collision_bridge.ingest_engine_observation(
                envelope("engine-observation:out-of-order:3", 3, "bio", "time_advance", {"ticks": 1})
            )
            collision_state = collision_bridge.world.extension_state("h2_bridge")
            collision_state["buffered_observations"][0]["message_id"] = first.message_id
            collision_bridge.world.set_extension_state("h2_bridge", collision_state)
            collision_digest = collision_bridge.world.state_digest()
            collision_bridge.world.save(path)
            collision_world = type(collision_bridge.world).load(path, expected_state_digest=collision_digest)
            with self.assertRaisesRegex(BridgeValidationError, "shared across applied and buffered ledgers"):
                WorldOSBridge(
                    collision_world,
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

            duplicate_slot_bridge = h2_bridge()
            duplicate_slot_bridge.ingest_engine_observation(first)
            duplicate_slot_state = duplicate_slot_bridge.world.extension_state("h2_bridge")
            duplicate_observation = json.loads(json.dumps(duplicate_slot_state["observations"][0]))
            duplicate_observation["envelope"]["message_id"] = "engine-observation:duplicate-slot"
            duplicate_slot_state["observations"].append(duplicate_observation)
            duplicate_slot_bridge.world.set_extension_state("h2_bridge", duplicate_slot_state)
            duplicate_slot_digest = duplicate_slot_bridge.world.state_digest()
            duplicate_slot_bridge.world.save(path)
            duplicate_slot_world = type(duplicate_slot_bridge.world).load(
                path, expected_state_digest=duplicate_slot_digest
            )
            with self.assertRaisesRegex(BridgeValidationError, "ordering is not unique and contiguous"):
                WorldOSBridge(
                    duplicate_slot_world,
                    {"ferryman": "ferry-dock", "baker": "bakery"},
                    PROPOSAL_ORIGIN_KEY,
                )

    def test_delivery_groups_preserve_observations_without_proposals(self):
        with tempfile.TemporaryDirectory() as directory:
            world = World(
                2203,
                [
                    Actor("bio", "The Bio", "bio", "wanderer", ("freedom",)),
                    Actor("mara", "Mara", "thinker", "captain", ("protect_community",)),
                ],
                crisis_actor="mara",
            )
            bridge = WorldOSBridge(world, {}, PROPOSAL_ORIGIN_KEY)
            request = envelope(
                "engine-observation:zero-proposal:2",
                2,
                "bio",
                "npc_request",
                {"target_id": "mara", "action": "wait"},
            )
            self.assertEqual(bridge.ingest_engine_observation(request), ())
            advance = envelope(
                "engine-observation:zero-proposal:1", 1, "bio", "time_advance", {"ticks": 1}
            )
            delivered = bridge.ingest_engine_observation(advance)
            self.assertEqual(len(delivered), 1)
            self.assertEqual(delivered[0].correlation_id, request.message_id)

            path = Path(directory) / "zero-proposal.json"
            trusted = bridge.world.state_digest()
            bridge.world.save(path)
            resumed = WorldOSBridge(
                World.load(path, expected_state_digest=trusted), {}, PROPOSAL_ORIGIN_KEY
            )
            self.assertEqual(resumed.ingest_engine_observation(advance), delivered)
            self.assertEqual(resumed.ingest_engine_observation(request), delivered)

            previous_state = bridge.world.extension_state("h2_bridge")
            previous_state.pop("delivery_observations")
            bridge.world.set_extension_state("h2_bridge", previous_state)
            previous_digest = bridge.world.state_digest()
            bridge.world.save(path)
            migrated = WorldOSBridge(
                World.load(path, expected_state_digest=previous_digest), {}, PROPOSAL_ORIGIN_KEY
            )
            self.assertEqual(migrated.ingest_engine_observation(advance), delivered)

    def test_stale_impossible_unauthorized_and_conflicting_inputs_do_not_mutate(self):
        bridge = h2_bridge()
        first, second = sorted(
            bridge.ingest_engine_observation(
                envelope(
                    "engine-observation:first",
                    1,
                    "bio",
                    "time_advance",
                    {"ticks": 1},
                )
            ),
            key=lambda item: item.payload["global_order"],
        )[:2]

        stale_authority = engine_authority()
        self.assertEqual(stale_authority.validate_and_apply(second), ())
        self.assertEqual(stale_authority.state()["state_version"], 0)
        drained = stale_authority.validate_and_apply(first)
        self.assertEqual([item.status for item in drained], ["applied", "applied"])
        self.assertEqual(
            [item.outcome.correlation_id for item in drained],
            [first.message_id, second.message_id],
        )
        self.assertEqual(stale_authority.state()["state_version"], 2)
        self.assertEqual(decide(stale_authority, second), drained[1])

        stale_payload = dict(first.payload)
        stale_payload.pop("origin_proof")
        stale_unsigned = Envelope.from_dict(
            {
                **first.to_dict(),
                "message_id": "world-proposal:stale-global-slot",
                "payload": stale_payload,
            }
        )
        stale_global = Envelope.from_dict(
            {
                **stale_unsigned.to_dict(),
                "payload": {
                    **stale_payload,
                    "origin_proof": _origin_proof(
                        stale_unsigned, PROPOSAL_ORIGIN_KEY
                    ),
                },
            }
        )
        before_stale_global = stale_authority.snapshot_digest()
        with self.assertRaisesRegex(BridgeValidationError, "global order is stale"):
            stale_authority.validate_and_apply(stale_global)
        self.assertEqual(
            stale_authority.snapshot_digest(), before_stale_global
        )

        zero_payload = dict(first.payload)
        zero_payload["global_order"] = 0
        zero_payload.pop("origin_proof")
        zero_unsigned = Envelope.from_dict(
            {
                **first.to_dict(),
                "message_id": "world-proposal:zero-global-slot",
                "payload": zero_payload,
            }
        )
        zero_global = Envelope.from_dict(
            {
                **zero_unsigned.to_dict(),
                "payload": {
                    **zero_payload,
                    "origin_proof": _origin_proof(
                        zero_unsigned, PROPOSAL_ORIGIN_KEY
                    ),
                },
            }
        )
        pristine_authority = engine_authority()
        before_zero_global = pristine_authority.snapshot_digest()
        with self.assertRaisesRegex(BridgeValidationError, "global order is stale"):
            pristine_authority.validate_and_apply(zero_global)
        self.assertEqual(
            pristine_authority.snapshot_digest(), before_zero_global
        )

        sequence_payload = dict(first.payload)
        sequence_payload.pop("origin_proof")
        sequence_two_unsigned = Envelope.from_dict(
            {
                **first.to_dict(),
                "sequence": 2,
                "payload": sequence_payload,
            }
        )
        sequence_two = Envelope.from_dict(
            {
                **sequence_two_unsigned.to_dict(),
                "payload": {
                    **sequence_payload,
                    "origin_proof": _origin_proof(
                        sequence_two_unsigned, PROPOSAL_ORIGIN_KEY
                    ),
                },
            }
        )
        regressing_payload = {
            **sequence_payload,
            "global_order": 2,
        }
        regressing_unsigned = Envelope.from_dict(
            {
                **first.to_dict(),
                "message_id": "world-proposal:regressing-actor-sequence",
                "payload": regressing_payload,
            }
        )
        regressing_sequence = Envelope.from_dict(
            {
                **regressing_unsigned.to_dict(),
                "payload": {
                    **regressing_payload,
                    "origin_proof": _origin_proof(
                        regressing_unsigned, PROPOSAL_ORIGIN_KEY
                    ),
                },
            }
        )
        sequence_authority = engine_authority()
        skipped_sequence = sequence_authority.validate_and_apply(
            sequence_two
        )[0]
        self.assertEqual(
            (skipped_sequence.status, skipped_sequence.reason),
            ("rejected", "out_of_order_sequence"),
        )
        self.assertEqual(sequence_authority.state()["state_version"], 0)
        regressing_decision = sequence_authority.validate_and_apply(
            regressing_sequence
        )[0]
        self.assertEqual(
            (regressing_decision.status, regressing_decision.reason),
            ("rejected", "stale_sequence"),
        )
        with tempfile.TemporaryDirectory() as directory:
            sequence_path = Path(directory) / "sequence-authority.json"
            sequence_authority.save(sequence_path)
            sequence_digest = sequence_authority.snapshot_digest()
            restored_sequence_authority = EngineAuthority.load(
                sequence_path,
                PROPOSAL_ORIGIN_KEY,
                expected_snapshot_digest=sequence_digest,
            )
            self.assertEqual(
                restored_sequence_authority.snapshot_digest(),
                sequence_digest,
            )

            strict_schema_six_authority = engine_authority()
            strict_schema_six_authority._process_proposal(
                sequence_two,
                require_contiguous_global_actor_sequence=False,
            )
            strict_schema_six_authority._process_proposal(
                regressing_sequence,
                require_contiguous_global_actor_sequence=False,
            )
            strict_schema_six_authority.save(sequence_path)
            strict_schema_six_payload = json.loads(
                sequence_path.read_text(encoding="utf-8")
            )
            strict_schema_six_payload["state"]["schema_version"] = 6
            canonical = json.dumps(
                strict_schema_six_payload["state"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            strict_schema_six_digest = hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()
            strict_schema_six_payload["digest"] = strict_schema_six_digest
            sequence_path.write_text(
                json.dumps(strict_schema_six_payload), encoding="utf-8"
            )
            migrated_strict_schema_six = EngineAuthority.load(
                sequence_path,
                PROPOSAL_ORIGIN_KEY,
                expected_snapshot_digest=strict_schema_six_digest,
            )
            self.assertEqual(
                migrated_strict_schema_six.snapshot()["schema_version"], 9
            )

            strict_schema_six_authority.save(sequence_path)
            schema_seven_payload = json.loads(
                sequence_path.read_text(encoding="utf-8")
            )
            schema_seven_payload["state"]["schema_version"] = 7
            canonical = json.dumps(
                schema_seven_payload["state"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            schema_seven_digest = hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()
            schema_seven_payload["digest"] = schema_seven_digest
            sequence_path.write_text(
                json.dumps(schema_seven_payload), encoding="utf-8"
            )
            migrated_schema_seven = EngineAuthority.load(
                sequence_path,
                PROPOSAL_ORIGIN_KEY,
                expected_snapshot_digest=schema_seven_digest,
            )
            self.assertEqual(
                migrated_schema_seven.snapshot()["schema_version"], 9
            )

            schema_six_authority = EngineAuthority(
                {first.actor_id: "village-square"},
                {first.actor_id: set()},
                {"ferry-dock", "bakery"},
                PROPOSAL_ORIGIN_KEY,
            )
            first_schema_six = schema_six_authority._process_proposal(
                sequence_two,
                enforce_global_actor_sequence=False,
            )
            second_schema_six = schema_six_authority._process_proposal(
                regressing_sequence,
                enforce_global_actor_sequence=False,
            )
            self.assertEqual(
                (first_schema_six.reason, second_schema_six.reason),
                ("permission_denied", "permission_denied"),
            )
            schema_six_authority.save(sequence_path)
            schema_six_payload = json.loads(
                sequence_path.read_text(encoding="utf-8")
            )
            schema_six_payload["state"]["schema_version"] = 6
            canonical = json.dumps(
                schema_six_payload["state"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            schema_six_digest = hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()
            schema_six_payload["digest"] = schema_six_digest
            sequence_path.write_text(
                json.dumps(schema_six_payload), encoding="utf-8"
            )
            migrated_schema_six = EngineAuthority.load(
                sequence_path,
                PROPOSAL_ORIGIN_KEY,
                expected_snapshot_digest=schema_six_digest,
            )
            self.assertEqual(
                migrated_schema_six.snapshot()["schema_version"], 9
            )

            gap_payload = {
                **dict(first.payload),
                "global_order": 3,
            }
            gap_payload.pop("origin_proof")
            gap_unsigned = Envelope.from_dict(
                {
                    **first.to_dict(),
                    "message_id": "world-proposal:schema-six-buffered-gap",
                    "sequence": 3,
                    "payload": gap_payload,
                }
            )
            buffered_gap = Envelope.from_dict(
                {
                    **gap_unsigned.to_dict(),
                    "payload": {
                        **gap_payload,
                        "origin_proof": _origin_proof(
                            gap_unsigned, PROPOSAL_ORIGIN_KEY
                        ),
                    },
                }
            )
            gap_authority = engine_authority()
            self.assertEqual(len(gap_authority.validate_and_apply(first)), 1)
            self.assertEqual(
                gap_authority.validate_and_apply(buffered_gap), ()
            )
            gap_authority.save(sequence_path)
            gap_payload_state = json.loads(
                sequence_path.read_text(encoding="utf-8")
            )
            gap_payload_state["state"]["schema_version"] = 6
            canonical = json.dumps(
                gap_payload_state["state"],
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            )
            gap_digest = hashlib.sha256(
                canonical.encode("utf-8")
            ).hexdigest()
            gap_payload_state["digest"] = gap_digest
            sequence_path.write_text(
                json.dumps(gap_payload_state), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                BridgeValidationError,
                "schema-6 buffered proposal policy is ambiguous",
            ):
                EngineAuthority.load(
                    sequence_path,
                    PROPOSAL_ORIGIN_KEY,
                    expected_snapshot_digest=gap_digest,
                )

        impossible_authority = EngineAuthority(
            {"elias": "village-square"},
            {"elias": {"routine_move"}},
            {"ferry-dock"},
            PROPOSAL_ORIGIN_KEY,
            blocked_paths={("elias", "ferry-dock")},
        )
        before = impossible_authority.state()
        impossible = decide(impossible_authority, first)
        self.assertEqual((impossible.status, impossible.reason), ("rejected", "physically_impossible"))
        self.assertEqual(impossible_authority.state(), before)

        unauthorized_authority = EngineAuthority(
            {"elias": "village-square"},
            {"elias": set()},
            {"ferry-dock"},
            PROPOSAL_ORIGIN_KEY,
        )
        before = unauthorized_authority.state()
        unauthorized = decide(unauthorized_authority, first)
        self.assertEqual((unauthorized.status, unauthorized.reason), ("rejected", "permission_denied"))
        self.assertEqual(unauthorized_authority.state(), before)

        authority = engine_authority()
        canonical = decide(authority, first)
        before = authority.state()
        conflicting = Envelope.from_dict(
            {**first.to_dict(), "payload": {**first.to_dict()["payload"], "destination": "bakery"}}
        )
        self.assertIs(decide(authority, conflicting), canonical)
        self.assertEqual(authority.state(), before)
        self.assertEqual(authority.conflicts()[0]["message_id"], first.message_id)

        forged = envelope(
            "world-proposal:forged",
            1,
            "elias",
            "world_action_proposed",
            {
                "action_type": "routine_move",
                "command": "perform ferryman routine",
                "destination": "ferry-dock",
                "causal_event_id": "made-up",
                "origin_proof": "0" * 64,
            },
        )
        forged_authority = engine_authority()
        before = forged_authority.state()
        with self.assertRaisesRegex(BridgeValidationError, "origin proof"):
            forged_authority.validate_and_apply(forged)
        self.assertEqual(forged_authority.state(), before)

        high_sequence_forgery = Envelope.from_dict({**forged.to_dict(), "sequence": 999})
        fresh_authority = engine_authority()
        with self.assertRaisesRegex(BridgeValidationError, "origin proof"):
            fresh_authority.validate_and_apply(high_sequence_forgery)
        self.assertEqual(decide(fresh_authority, first).status, "applied")

        preempting = Envelope.from_dict(
            {**first.to_dict(), "payload": {**first.to_dict()["payload"], "destination": "bakery"}}
        )
        preemption_authority = engine_authority()
        with self.assertRaisesRegex(BridgeValidationError, "origin proof"):
            preemption_authority.validate_and_apply(preempting)
        genuine = decide(preemption_authority, first)
        self.assertEqual(genuine.status, "applied")
        self.assertIs(decide(preemption_authority, preempting), genuine)

    def test_thinker_choice_originates_in_world_os_and_evidence_is_machine_readable(self):
        bridge = h2_bridge()
        authority = engine_authority()
        request = envelope(
            "engine-observation:request",
            1,
            "bio",
            "npc_request",
            {"target_id": "mara", "action": "wait"},
        )
        proposal = bridge.ingest_engine_observation(request)[0]
        self.assertEqual((proposal.actor_id, proposal.payload["action_type"]), ("mara", "independent_choice"))
        trace = bridge.world.trace(proposal.payload["causal_event_id"])
        self.assertEqual(trace["event"]["event_type"], "independent_choice")
        self.assertTrue(any(item["event"]["event_type"] == "request" for item in trace["causes"]))

        decision = decide(authority, proposal)
        bridge.receive_engine_decision(decision)
        evidence = bridge.evidence()
        path = Path(".harness/evidence/h2/bridge-contract.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        loaded = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(loaded["schema_version"], BRIDGE_SCHEMA_VERSION)
        self.assertEqual(loaded["observations"][0]["envelope"], request.to_dict())
        self.assertEqual(loaded["decisions"][0]["status"], "applied")
        self.assertEqual(loaded["decisions"][0]["outcome"]["correlation_id"], proposal.message_id)
        self.assertIn(proposal.message_id, loaded["causal_traces"])


if __name__ == "__main__":
    unittest.main()
