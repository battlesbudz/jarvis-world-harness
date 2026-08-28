import json
import math
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
    return WorldOSBridge(albion_world(2202), {"ferryman": "ferry-dock", "baker": "bakery"})


def engine_authority():
    return EngineAuthority(
        {"elias": "village-square", "nella": "village-square", "mara": "captain-post"},
        {
            "elias": {"routine_move"},
            "nella": {"routine_move"},
            "mara": {"independent_choice", "values_refusal"},
        },
        {"ferry-dock", "bakery", "captain-post"},
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

    def test_observation_and_successful_proposal_are_exactly_once(self):
        bridge = h2_bridge()
        authority = engine_authority()
        observation = envelope("engine-observation:1", 1, "bio", "time_advance", {"ticks": 1})

        proposals = bridge.ingest_engine_observation(observation)
        world_digest = bridge.world.state_digest()
        elias = next(item for item in proposals if item.actor_id == "elias")
        self.assertEqual(elias.payload["action_type"], "routine_move")
        self.assertEqual(elias.payload["destination"], "ferry-dock")
        self.assertEqual(bridge.world.trace(elias.payload["causal_event_id"])["event"]["actor"], "elias")

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

        self.assertEqual(bridge.ingest_engine_observation(observation), proposals)
        self.assertEqual(bridge.world.state_digest(), world_digest)
        self.assertIs(authority.validate_and_apply(elias), decision)
        self.assertEqual(authority.state()["state_version"], 1)
        self.assertEqual(authority.state()["positions"]["elias"], "ferry-dock")
        bridge.receive_engine_decision(decision)

    def test_stale_impossible_unauthorized_and_conflicting_inputs_do_not_mutate(self):
        authority = engine_authority()
        valid = envelope(
            "world-proposal:valid",
            1,
            "elias",
            "world_action_proposed",
            {
                "action_type": "routine_move",
                "command": "perform ferryman routine",
                "destination": "ferry-dock",
                "causal_event_id": "evt-000001",
            },
        )
        self.assertEqual(authority.validate_and_apply(valid).status, "applied")

        cases = [
            (
                envelope(
                    "world-proposal:stale",
                    1,
                    "elias",
                    "world_action_proposed",
                    {**dict(valid.payload), "destination": "bakery"},
                ),
                "stale_sequence",
            ),
            (
                envelope(
                    "world-proposal:impossible",
                    2,
                    "elias",
                    "world_action_proposed",
                    {**dict(valid.payload), "destination": "missing-place"},
                ),
                "physically_impossible",
            ),
            (
                envelope(
                    "world-proposal:unauthorized",
                    1,
                    "mara",
                    "world_action_proposed",
                    {**dict(valid.payload), "destination": "captain-post"},
                ),
                "permission_denied",
            ),
        ]
        for proposal, reason in cases:
            before = authority.state()
            decision = authority.validate_and_apply(proposal)
            self.assertEqual((decision.status, decision.reason), ("rejected", reason))
            self.assertEqual(authority.state(), before)
            self.assertIs(authority.validate_and_apply(proposal), decision)

        repeated_sequence = envelope(
            "world-proposal:reused-rejected-sequence",
            2,
            "elias",
            "world_action_proposed",
            {**dict(valid.payload), "destination": "bakery"},
        )
        before = authority.state()
        self.assertEqual(authority.validate_and_apply(repeated_sequence).reason, "stale_sequence")
        self.assertEqual(authority.state(), before)

        before = authority.state()
        conflicting = Envelope.from_dict({**valid.to_dict(), "payload": {**dict(valid.payload), "destination": "bakery"}})
        self.assertEqual(authority.validate_and_apply(conflicting).reason, "message_id_conflict")
        self.assertEqual(authority.state(), before)

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
