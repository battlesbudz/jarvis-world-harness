import unittest

from world_os.scenarios import albion_world, awaken_elias


class AwakeningAgencyTest(unittest.TestCase):
    def test_role_routine_precedes_evidence_driven_awakening(self):
        world = albion_world()
        world.advance()
        self.assertTrue(any(event.event_type == "routine_action" and event.actor == "elias" for event in world.events))
        self.assertEqual(world.memories("elias"), [])

        transition_id = awaken_elias(world)
        transition = next(event for event in world.events if event.id == transition_id)
        self.assertEqual(transition.payload["rule"], "meaningful_soul_pattern")
        self.assertGreaterEqual(transition.payload["score"], transition.payload["threshold"])
        self.assertTrue(world.is_awakened("elias"))
        self.assertEqual(world.cognition("elias"), "conscious")
        interaction_ids = {
            event.id
            for event in world.events
            if event.event_type == "meaningful_interaction" and "elias" in event.targets
        }
        memory_ids = {memory["event_id"] for memory in world.memories("elias")}
        self.assertLessEqual(interaction_ids, memory_ids)
        self.assertTrue(world.goals("elias"))

    def test_awakened_actor_can_refuse_bio_on_values(self):
        world = albion_world()
        transition_id = awaken_elias(world)
        refusal = world.decide_request("bio", "elias", "abandon_town", root_input="bio-order")
        self.assertEqual(refusal.event_type, "values_refusal")
        self.assertEqual(refusal.payload["decision"], "refuse")
        trace = world.trace(refusal.id)
        self.assertEqual(trace["causes"][0]["event"]["event_type"], "request")
        self.assertTrue(world.trace(transition_id)["causes"])


if __name__ == "__main__":
    unittest.main()
