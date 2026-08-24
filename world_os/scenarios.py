from __future__ import annotations

from .runtime import Actor, World


def albion_world(seed: int = 1701) -> World:
    return World(
        seed,
        [
            Actor("bio", "The Bio", "bio", "wanderer", ("freedom",)),
            Actor("mara", "Mara", "thinker", "captain", ("protect_community",)),
            Actor("orin", "Orin", "thinker", "scribe", ("truth",)),
            Actor("tavi", "Tavi", "thinker", "healer", ("mercy",)),
            Actor("elias", "Elias", "non_thinker", "ferryman", ("protect_community", "independence")),
            Actor("nella", "Nella", "non_thinker", "baker", ("community",)),
        ],
        crisis_actor="mara",
    )


def awaken_elias(world: World) -> str:
    world.meaningful_interaction("bio", "elias", ["attention", "vulnerability"], root_input="conversation:1")
    world.advance()
    world.meaningful_interaction(
        "bio", "elias", ["shared_danger", "protection"], witnesses=("mara",), root_input="flood-rescue:1"
    )
    world.advance()
    world.meaningful_interaction("bio", "elias", ["attention", "vulnerability"], root_input="conversation:2")
    return next(event.id for event in world.events if event.event_type == "awakening_transition")
