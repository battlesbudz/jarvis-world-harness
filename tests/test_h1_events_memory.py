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
        self.assertEqual(event.id, "evt-000001")
        self.assertEqual(event.schema_version, 1)
        self.assertEqual(event.root_input, "bridge-defense")
        self.assertEqual(event.witnesses, ("orin",))

        self.assertEqual(world.memories("bio")[0]["perspective"], "actor")
        self.assertEqual(world.memories("mara")[0]["perspective"], "target")
        self.assertEqual(world.memories("orin")[0]["perspective"], "witness")
        self.assertEqual(world.beliefs("orin")[0]["source_event"], event.id)
        with self.assertRaises(TypeError):
            event.payload["factors"] = ("betrayal",)
        self.assertIsInstance(world.events, tuple)

    def test_invalid_proposal_is_rejected_without_requested_mutation(self):
        world = albion_world()
        rejected = world.apply(Proposal("awakening_transition", "bio", ("elias",), root_input="cheat"))
        self.assertEqual(rejected.event_type, "proposal_rejected")
        self.assertFalse(world.is_awakened("elias"))
        self.assertFalse(hasattr(world, "awaken"))

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
