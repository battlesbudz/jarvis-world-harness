import unittest

from world_os import Proposal
from world_os.scenarios import albion_world


class RelationshipsRumorsTest(unittest.TestCase):
    def test_relationships_are_multidimensional_and_evidence_derived(self):
        world = albion_world()
        source = world.meaningful_interaction(
            "bio", "mara", ["shared_danger", "protection"], witnesses=("orin",), root_input="rescue"
        )
        relationship = world.relationship("mara", "bio")
        self.assertEqual(set(relationship), {"trust", "fear", "respect", "resentment", "affection"})
        self.assertEqual(relationship, {"trust": 5, "fear": 0, "respect": 4, "resentment": 0, "affection": 2})
        self.assertEqual(world.trace(source.id)["event"]["root_input"], "rescue")

    def test_rumor_retains_provenance_and_loses_confidence(self):
        world = albion_world()
        source = world.meaningful_interaction(
            "bio", "mara", ["shared_danger"], witnesses=("mara",), root_input="wolf-attack"
        )
        first = world.share_rumor("mara", "orin", source.id, root_input="mara-tells-orin")
        second = world.share_rumor("orin", "tavi", source.id, root_input="orin-tells-tavi")
        self.assertEqual(first.payload["provenance"], ("bio", "mara", "orin"))
        self.assertEqual(second.payload["provenance"], ("bio", "mara", "orin", "tavi"))
        self.assertEqual(first.payload["confidence"], 0.8)
        self.assertEqual(second.payload["confidence"], 0.64)
        self.assertEqual(world.beliefs("tavi")[-1]["source_event"], source.id)
        self.assertEqual(world.trace(second.id)["causes"][0]["event"]["id"], first.id)

        trace = world.relationship_trace("mara", "bio")
        self.assertEqual(trace["dimensions"], world.relationship("mara", "bio"))
        self.assertEqual({item["event_id"] for item in trace["contributions"]}, {source.id})

        forged = world.apply(
            Proposal(
                "rumor_shared",
                "tavi",
                ("mara",),
                root_input="forged",
                payload={"source_event": source.id, "provenance": ["tavi", "mara"], "confidence": 1.0},
            )
        )
        self.assertEqual(forged.event_type, "proposal_rejected")


if __name__ == "__main__":
    unittest.main()
