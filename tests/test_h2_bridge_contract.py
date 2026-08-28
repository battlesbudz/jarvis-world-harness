import hashlib
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


def decide(authority, proposal):
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
            decision = decide(authority, proposals[0])
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
            self.assertEqual(decide(resumed_authority, proposals[0]), decision)
            self.assertEqual(resumed_authority.state(), before_engine_retry)
            self.assertEqual(resumed_authority.state()["state_version"], 1)

            buffered_authority = engine_authority()
            first_elias = next(item for item in proposals if item.actor_id == "elias")
            second_elias = next(item for item in next_proposals if item.actor_id == "elias")
            self.assertEqual(buffered_authority.validate_and_apply(second_elias), ())
            self.assertEqual(buffered_authority.state()["state_version"], 0)
            buffered_authority.save(engine_path)
            buffered_digest = buffered_authority.snapshot_digest()
            resumed_buffer = EngineAuthority.load(
                engine_path,
                PROPOSAL_ORIGIN_KEY,
                expected_snapshot_digest=buffered_digest,
            )
            drained = resumed_buffer.validate_and_apply(first_elias)
            self.assertEqual([item.status for item in drained], ["applied", "applied"])
            self.assertEqual(
                [item.outcome.correlation_id for item in drained],
                [first_elias.message_id, second_elias.message_id],
            )
            self.assertEqual(resumed_buffer.state()["state_version"], 2)
            self.assertEqual(resumed_buffer.validate_and_apply(first_elias), drained)
            self.assertEqual(decide(resumed_buffer, second_elias), drained[1])
            resumed_buffer.save(engine_path)
            drained_digest = resumed_buffer.snapshot_digest()
            retry_buffer = EngineAuthority.load(
                engine_path,
                PROPOSAL_ORIGIN_KEY,
                expected_snapshot_digest=drained_digest,
            )
            self.assertEqual(retry_buffer.validate_and_apply(first_elias), drained)

            version_two_payload = json.loads(engine_path.read_text(encoding="utf-8"))
            version_two_payload["state"].pop("response_batches")
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
            unambiguous_v2 = json.loads(engine_path.read_text(encoding="utf-8"))
            unambiguous_v2["state"].pop("response_batches")
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
            self.assertEqual(migrated_authority.snapshot()["schema_version"], 3)

            legacy_out_of_order = engine_authority()
            legacy_second = legacy_out_of_order._process_proposal(second_elias)
            legacy_first = legacy_out_of_order._process_proposal(first_elias)
            self.assertEqual((legacy_second.status, legacy_first.reason), ("applied", "stale_sequence"))
            legacy_state = legacy_out_of_order.snapshot()
            legacy_state.pop("buffered_proposals")
            legacy_state.pop("response_batches")
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
            migrated_out_of_order = EngineAuthority.load(
                engine_path,
                PROPOSAL_ORIGIN_KEY,
                expected_snapshot_digest=legacy_digest,
            )
            self.assertEqual(decide(migrated_out_of_order, second_elias), legacy_second)
            self.assertEqual(decide(migrated_out_of_order, first_elias), legacy_first)

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
            no_gap_buffer["state"]["buffered_proposals"] = [proposals[0].to_dict()]
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
