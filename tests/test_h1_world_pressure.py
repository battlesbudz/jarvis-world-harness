import unittest

from world_os.scenarios import albion_world


class WorldPressureTest(unittest.TestCase):
    def test_crisis_evolves_without_player_action(self):
        world = albion_world(99)
        world.advance(4)
        crisis_events = [event for event in world.events if event.event_type == "crisis_changed"]
        self.assertEqual([event.payload["severity"] for event in crisis_events], [1, 2, 3, 4])
        self.assertEqual(world.crisis()["phase"], "collapse")
        self.assertTrue(all(not event.payload["player_intervened"] for event in crisis_events))
        self.assertTrue(all(event.parents for event in crisis_events))

    def test_seeded_world_pressure_is_reproducible(self):
        first = albion_world(42)
        second = albion_world(42)
        first.advance(3)
        second.advance(3)
        self.assertEqual(first.state_digest(), second.state_digest())


if __name__ == "__main__":
    unittest.main()
