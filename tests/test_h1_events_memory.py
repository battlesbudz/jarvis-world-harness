import unittest

from world_os import Proposal
from world_os.scenarios import albion_world


class EventsMemoryTest(unittest.TestCase):
    def test_actor_categories_and_append_only_events(self):
        world = albion_world()
        self.assertEqual({actor.category for actor in world.actors.values()}, {"bio", "thinker", "non_thinker"})

        event = world.meaningful_interaction(
            "bio", "mara", ["shared_danger"], witnesses=("orin",), root_input="bridge-defense"
        )
        self.assertEqual(event.id, world.events[-1].id)
        self.assertEqual(event.schema_version, 1)
        self.assertEqual(event.root_input, "bridge-defense")
        self.assertEqual(event.witnesses, ("orin",))

        self.assertEqual(world.memories("bio")[0]["perspective"], "actor")
        self.assertEqual(world.memories("mara")[0]["perspective"], "target")
        self.assertEqual(world.memories("orin")[0]["perspective"], "witness")
        self.assertEqual(world.beliefs("orin")[0]["source_event"], event.id)
        earth_memory = world.memories("bio")[0]
        self.assertEqual(world.trace(earth_memory["event_id"])["rule"], "bio_remembers_earth_immediately")
        with self.assertRaises(TypeError):
            event.payload["factors"] = ("betrayal",)
        self.assertIsInstance(world.events, tuple)

    def test_invalid_proposal_is_rejected_without_requested_mutation(self):
        world = albion_world()
        rejected = world.apply(Proposal("awakening_transition", "bio", ("elias",), root_input="cheat"))
        self.assertEqual(rejected.event_type, "proposal_rejected")
        self.assertFalse(world.is_awakened("elias"))
        self.assertFalse(hasattr(world, "awaken"))

        bad_parent = world.apply(
            Proposal("request", "bio", ("mara",), parents=("evt-999999",), payload={"action": "wait"})
        )
        self.assertEqual(bad_parent.parents, ())
        self.assertEqual(bad_parent.payload["proposal"]["parents"], ("evt-999999",))

        malformed = world.apply(
            Proposal(
                "meaningful_interaction",
                "bio",
                ("elias",),
                root_input="malformed-factors",
                payload={"factors": [{}]},
            )
        )
        self.assertEqual(malformed.event_type, "proposal_rejected")

        before = len(world.events)
        unknown_bio = world.decide_request("missing-bio", "elias", "wait", root_input="bad-request")
        self.assertEqual(unknown_bio.event_type, "proposal_rejected")
        self.assertEqual(len(world.events), before + 1)

    def test_simultaneous_proposals_have_stable_order(self):
        proposals = [
            Proposal("request", "bio", ("mara",), root_input="b", payload={"action": "wait"}),
            Proposal("request", "bio", ("orin",), root_input="a", payload={"action": "read"}),
        ]
        first = albion_world(7)
        second = albion_world(7)
        first.apply_all(proposals)
        second.apply_all(reversed(proposals))
        self.assertEqual(first.state_digest(), second.state_digest())


if __name__ == "__main__":
    unittest.main()
