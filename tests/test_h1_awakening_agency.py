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
        self.assertEqual(transition.payload["rule"], "repeated_meaningful_soul_pattern")
        self.assertGreaterEqual(transition.payload["score"], transition.payload["threshold"])
        self.assertGreaterEqual(transition.payload["interaction_count"], 3)
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

    def test_one_event_or_non_bio_contact_cannot_awaken(self):
        world = albion_world()
        duplicate = world.meaningful_interaction(
            "bio", "elias", ["protection", "protection", "protection"], root_input="spam"
        )
        self.assertEqual(duplicate.event_type, "proposal_rejected")
        world.meaningful_interaction("mara", "elias", ["protection", "shared_danger"], root_input="captain-rescue")
        world.meaningful_interaction("bio", "elias", ["protection", "shared_danger"], root_input="bio-rescue")
        world.meaningful_interaction("bio", "elias", ["attention", "vulnerability"], root_input="bio-talk")
        world.meaningful_interaction("bio", "elias", ["protection"], root_input="bio-protection")
        self.assertFalse(world.is_awakened("elias"))

        replay = albion_world()
        first = replay.meaningful_interaction("bio", "elias", ["protection"], root_input="same-contact")
        second = replay.meaningful_interaction("bio", "elias", ["protection"], root_input="same-contact")
        third = replay.meaningful_interaction("bio", "elias", ["protection"], root_input="same-contact")
        self.assertEqual(first.event_type, "meaningful_interaction")
        self.assertEqual(second.event_type, "proposal_rejected")
        self.assertEqual(third.event_type, "proposal_rejected")
        self.assertFalse(replay.is_awakened("elias"))

    def test_awakened_actor_can_refuse_bio_on_values(self):
        world = albion_world()
        transition_id = awaken_elias(world)
        refusal = world.decide_request("bio", "elias", "abandon_town", root_input="bio-order")
        self.assertEqual(refusal.event_type, "values_refusal")
        self.assertEqual(refusal.payload["decision"], "refuse")
        trace = world.trace(refusal.id)
        self.assertEqual(trace["causes"][0]["event"]["event_type"], "request")
        self.assertTrue(world.trace(transition_id)["causes"])

        thinker_refusal = world.decide_request("bio", "mara", "abandon_town", root_input="captain-order")
        self.assertEqual(thinker_refusal.event_type, "values_refusal")


if __name__ == "__main__":
    unittest.main()
