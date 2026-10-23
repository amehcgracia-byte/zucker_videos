"""Show paces offered by Montara: Fast, Normal, Storytelling, Podcast."""
from core.show_edit import STYLES


def test_the_four_paces_get_slower_in_order():
    fast, normal, story, podcast = (STYLES[name] for name in ("fast", "normal", "storytelling", "podcast"))
    assert 1.5 <= fast.min_shot and fast.max_shot <= 2.0
    assert fast.target_shot < normal.target_shot < story.target_shot < podcast.target_shot
    assert podcast.zooms == (1.0,) and podcast.push_every == 0  # steady shots for conversation
    assert STYLES["choe"] and STYLES["calm"]  # Zucker's own styles stay
