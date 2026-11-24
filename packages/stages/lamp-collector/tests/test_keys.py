import pytest
from lamp_collector.keys import REDACTED, ApiKeyError, ApiKeys, KeyKind

BUS_KEY = "Bus0Key0Bus0Key0Bus0Key0B"
TRAIN_KEY = "train0key0train0key0train0key012"


@pytest.mark.parametrize(
    "bad_key",
    ["short0key", f"{BUS_KEY} ", f'"{BUS_KEY}"', "key-with-dash-000000"],
)
def test_malformed_key_is_refused_without_echoing_it(bad_key: str) -> None:
    with pytest.raises(ApiKeyError, match="CTA_BUS_KEY must be") as caught:
        ApiKeys(bus=bad_key, train=TRAIN_KEY)

    assert bad_key.strip(' "') not in str(caught.value)


def test_repr_never_shows_a_key() -> None:
    text = repr(ApiKeys(bus=BUS_KEY, train=TRAIN_KEY))

    assert BUS_KEY not in text
    assert TRAIN_KEY not in text


def test_redact_replaces_every_copy_of_either_key() -> None:
    keys = ApiKeys(bus=BUS_KEY, train=TRAIN_KEY)
    text = f"GET /a?key={BUS_KEY} then /b?key={TRAIN_KEY}&again={BUS_KEY}"

    assert keys.redact(text) == f"GET /a?key={REDACTED} then /b?key={REDACTED}&again={REDACTED}"


def test_redact_works_when_both_keys_are_the_same() -> None:
    keys = ApiKeys(bus=BUS_KEY, train=BUS_KEY)

    assert keys.redact(f"key={BUS_KEY}") == f"key={REDACTED}"


def test_for_kind_picks_each_key() -> None:
    keys = ApiKeys(bus=BUS_KEY, train=TRAIN_KEY)

    assert keys.for_kind(KeyKind.BUS) == BUS_KEY
    assert keys.for_kind(KeyKind.TRAIN) == TRAIN_KEY
