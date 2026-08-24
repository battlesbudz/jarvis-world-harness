import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from world_os import Proposal, ValidationError
from world_os.scenarios import albion_world, awaken_elias


class PersistenceTracesTest(unittest.TestCase):
    def test_save_reload_matches_uninterrupted_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.json"
            resumed = albion_world(123)
            genesis = resumed.genesis_digest()
            resumed.advance(2)
            resumed.save(path)
            resumed = type(resumed).load(path, expected_genesis_digest=genesis)
            resumed.advance(2)

            uninterrupted = albion_world(123)
            uninterrupted.advance(4)
            self.assertEqual(resumed.state(), uninterrupted.state())
            self.assertEqual(resumed.state_digest(), uninterrupted.state_digest())

            awakened = albion_world(321)
            awaken_elias(awakened)
            awakened.decide_request("bio", "elias", "abandon_town", root_input="bio-order")
            awakened.save(path)
            loaded = type(awakened).load(path, expected_genesis_digest=awakened.genesis_digest())
            self.assertEqual(loaded.state(), awakened.state())

    def test_tampered_and_incompatible_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "world.json"
            world = albion_world()
            genesis = world.genesis_digest()
            world.save(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["state"]["tick"] = 999
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValidationError):
                type(world).load(path, expected_genesis_digest=genesis)

            world = albion_world()
            rejected = world.apply(
                Proposal("request", "bio", ("mara",), parents=("evt-999999",), payload={"action": "wait"})
            )
            self.assertEqual(rejected.event_type, "proposal_rejected")
            world.save(path)
            self.assertEqual(
                type(world).load(path, expected_genesis_digest=genesis).state_digest(), world.state_digest()
            )

            world.advance()
            world.save(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["state"]["events"][-1]["parents"] = ["evt-999999"]
            canonical = json.dumps(payload["state"], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            payload["digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValidationError):
                type(world).load(path, expected_genesis_digest=genesis)

            world = albion_world()
            world.meaningful_interaction("bio", "elias", ["attention"], root_input="hello")
            world.save(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            event = payload["state"]["events"][-1]
            event["event_type"] = "awakening_transition"
            event["payload"] = {
                "rule": "repeated_meaningful_soul_pattern",
                "score": 99,
                "threshold": 12,
                "interaction_count": 3,
            }
            canonical = json.dumps(payload["state"], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            payload["digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValidationError):
                type(world).load(path, expected_genesis_digest=genesis)

            world.save(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["format"] = "future-format"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValidationError):
                type(world).load(path, expected_genesis_digest=genesis)

            world.save(path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["state"]["seed"] = world.seed + 1
            canonical = json.dumps(payload["state"], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
            payload["digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValidationError):
                type(world).load(path, expected_genesis_digest=genesis)

    def test_machine_readable_causal_evidence(self):
        world = albion_world(808)
        transition_id = awaken_elias(world)
        refusal = world.decide_request("bio", "elias", "abandon_town", root_input="bio-order")
        world.advance(3)
        crisis = next(event for event in reversed(world.events) if event.event_type == "crisis_changed")
        path = Path(".harness/evidence/h1/causal-scenario.json")
        world.write_trace(
            path,
            "awakening-refusal-crisis",
            [transition_id, refusal.id, crisis.id],
            relationships=[("elias", "bio")],
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["seed"], 808)
        self.assertEqual(len(payload["events"]), 3)
        self.assertEqual(payload["state_digest"], world.state_digest())
        self.assertTrue(payload["events"][0]["causes"])
        self.assertTrue(payload["relationships"][0]["contributions"])


if __name__ == "__main__":
    unittest.main()
