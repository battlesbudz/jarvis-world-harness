import json
import math
import tempfile
import unittest
from pathlib import Path

from world_os import (
    BRIDGE_SCHEMA_VERSION,
    BridgeValidationError,
    EngineAuthority,
    EngineDecision,
    Envelope,
    WorldOSBridge,
)
from world_os.scenarios import albion_world


PROPOSAL_ORIGIN_KEY = b"h2-deterministic-test-key"


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
    )


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
                "authority_proof": "0" * 64,
                "engine_event_id": None,
                "reason": "forged_rejection",
                "state_version": 0,
                "status": "rejected",
            },
            correlation_id=elias.message_id,
        )
        with self.assertRaisesRegex(BridgeValidationError, "authority proof"):
            bridge.receive_engine_decision(EngineDecision("rejected", forged_outcome))

        decision = authority.validate_and_apply(elias)
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
        self.assertIs(authority.validate_and_apply(elias), decision)
        self.assertEqual(authority.state()["state_version"], 1)
        self.assertEqual(authority.state()["positions"]["elias"], "ferry-dock")
        bridge.receive_engine_decision(decision)

        nella = next(item for item in proposals if item.actor_id == "nella")
        conflicting_authority = engine_authority()
        conflicting_authority.validate_and_apply(nella)
        reused_event_decision = conflicting_authority.validate_and_apply(elias)
        with self.assertRaisesRegex(BridgeValidationError, "event id was reused"):
            bridge.receive_engine_decision(reused_event_decision)

        restarted_authority = engine_authority()
        reused_version = restarted_authority.validate_and_apply(nella)
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
        lineage_first = lineage_authority.validate_and_apply(lineage_proposals[0])
        lineage_second = lineage_authority.validate_and_apply(lineage_proposals[1])
        lineage_bridge.receive_engine_decision(lineage_second)
        forked_first = engine_authority().validate_and_apply(fork_proposal)
        with self.assertRaisesRegex(BridgeValidationError, "state version was reused"):
            lineage_bridge.receive_engine_decision(forked_first)
        lineage_bridge.receive_engine_decision(lineage_first)

    def test_observation_ledger_and_proposals_survive_world_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.json"
            bridge = h2_bridge()
            observation = envelope("engine-observation:persisted", 1, "bio", "time_advance", {"ticks": 1})
            proposals = bridge.ingest_engine_observation(observation)
            authority = engine_authority()
            decision = authority.validate_and_apply(proposals[0])
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
            self.assertEqual(resumed_authority.validate_and_apply(proposals[0]), decision)
            self.assertEqual(resumed_authority.state(), before_engine_retry)
            self.assertEqual(resumed_authority.state()["state_version"], 1)

    def test_stale_impossible_unauthorized_and_conflicting_inputs_do_not_mutate(self):
        bridge = h2_bridge()
        first = next(
            item
            for item in bridge.ingest_engine_observation(
                envelope("engine-observation:first", 1, "bio", "time_advance", {"ticks": 1})
            )
            if item.actor_id == "elias"
        )
        second = next(
            item
            for item in bridge.ingest_engine_observation(
                envelope("engine-observation:second", 2, "bio", "time_advance", {"ticks": 1})
            )
            if item.actor_id == "elias"
        )

        stale_authority = engine_authority()
        self.assertEqual(stale_authority.validate_and_apply(second).status, "applied")
        before = stale_authority.state()
        stale = stale_authority.validate_and_apply(first)
        self.assertEqual((stale.status, stale.reason), ("rejected", "stale_sequence"))
        self.assertEqual(stale_authority.state(), before)

        impossible_authority = EngineAuthority(
            {"elias": "village-square"},
            {"elias": {"routine_move"}},
            {"ferry-dock"},
            PROPOSAL_ORIGIN_KEY,
            blocked_paths={("elias", "ferry-dock")},
        )
        before = impossible_authority.state()
        impossible = impossible_authority.validate_and_apply(first)
        self.assertEqual((impossible.status, impossible.reason), ("rejected", "physically_impossible"))
        self.assertEqual(impossible_authority.state(), before)

        unauthorized_authority = EngineAuthority(
            {"elias": "village-square"},
            {"elias": set()},
            {"ferry-dock"},
            PROPOSAL_ORIGIN_KEY,
        )
        before = unauthorized_authority.state()
        unauthorized = unauthorized_authority.validate_and_apply(first)
        self.assertEqual((unauthorized.status, unauthorized.reason), ("rejected", "permission_denied"))
        self.assertEqual(unauthorized_authority.state(), before)

        authority = engine_authority()
        canonical = authority.validate_and_apply(first)
        before = authority.state()
        conflicting = Envelope.from_dict(
            {**first.to_dict(), "payload": {**first.to_dict()["payload"], "destination": "bakery"}}
        )
        self.assertIs(authority.validate_and_apply(conflicting), canonical)
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
        self.assertEqual(fresh_authority.validate_and_apply(first).status, "applied")

        preempting = Envelope.from_dict(
            {**first.to_dict(), "payload": {**first.to_dict()["payload"], "destination": "bakery"}}
        )
        preemption_authority = engine_authority()
        with self.assertRaisesRegex(BridgeValidationError, "origin proof"):
            preemption_authority.validate_and_apply(preempting)
        genuine = preemption_authority.validate_and_apply(first)
        self.assertEqual(genuine.status, "applied")
        self.assertIs(preemption_authority.validate_and_apply(preempting), genuine)

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

        decision = authority.validate_and_apply(proposal)
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
