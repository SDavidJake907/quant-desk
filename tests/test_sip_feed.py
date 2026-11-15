"""Recent SIP is the tape a funded book has to read. Paper stays on the feed that works."""

from scanner.sources.alpaca_bars import recent_sip_verdict


def test_a_sip_quote_is_ok_only_when_the_plan_allows_it():
    assert recent_sip_verdict(200, '{"quote":{}}') == "ok"


def test_the_free_plan_refusal_blocks_funding():
    body = '{"message":"subscription does not permit querying recent SIP data"}'
    assert recent_sip_verdict(403, body) == "refused"
    assert recent_sip_verdict(200, body) == "ok"


def test_any_other_failure_is_unchecked():
    assert recent_sip_verdict(500, "upstream") == "unchecked"
    assert recent_sip_verdict(429, "") == "unchecked"
