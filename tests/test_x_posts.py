"""X recent search is parsed offline. The card decides which post is shown."""

from datetime import datetime, timezone

from scanner.sources import x_posts


def test_the_query_is_cashtags_only():
    assert x_posts.search_query(["glw", "CI", "GLW"]) == "($GLW OR $CI) -is:retweet"
    assert x_posts.search_query([]) == ""


def test_a_search_body_keeps_the_text_and_the_time():
    payload = {"data": [
        {"id": "1", "text": "$GLW  is   bid up", "created_at": "2026-11-11T15:10:00.000Z"},
        {"id": "2", "text": "   "},
        "nope",
    ]}
    posts = x_posts.parse_posts(payload)
    assert len(posts) == 1
    when, text = posts[0]
    assert text == "$GLW is bid up"
    assert when == datetime(2026, 11, 11, 15, 10, tzinfo=timezone.utc)


def test_a_bad_body_is_empty():
    assert x_posts.parse_posts({"errors": []}) == ()
    assert x_posts.parse_posts(None) == ()
