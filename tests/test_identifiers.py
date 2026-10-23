import pytest

from bo3wd.identifiers import normalize_workshop_id, parse_workshop_list


def test_normalizes_numeric_ids_without_mutation():
    assert normalize_workshop_id("3296316642") == "3296316642"
    assert normalize_workshop_id(3296316642) == "3296316642"
    assert normalize_workshop_id(" 000123 ") == "123"


def test_parses_supported_steam_workshop_urls():
    assert normalize_workshop_id("https://steamcommunity.com/sharedfiles/filedetails/?id=3296316642") == "3296316642"
    assert normalize_workshop_id("steamcommunity.com/workshop/filedetails/?id=123&searchtext=x") == "123"
    assert normalize_workshop_id("https://www.steamcommunity.com/sharedfiles/filedetails/?extra=1&id=456") == "456"


@pytest.mark.parametrize("value", ["", "0", "0000", "abc", "../12", "-1", "1/2", "123456789012345678901", "https://example.com/?id=123", "https://steamcommunity.com/id/user", "https://steamcommunity.com/sharedfiles/filedetails/?id=x", "https://steamcommunity.com/sharedfiles/filedetails/?id=1&id=2", "javascript:alert(1)"])
def test_rejects_invalid_ids_and_urls(value):
    with pytest.raises(ValueError):
        normalize_workshop_id(value)


def test_pasted_list_supports_comments_delimiters_and_deduplication():
    text = """# favourites
3296316642, 123
https://steamcommunity.com/sharedfiles/filedetails/?id=456
123; 000124
"""
    assert parse_workshop_list(text) == ["3296316642", "123", "456", "124"]


def test_empty_or_comment_only_list_is_empty():
    assert parse_workshop_list("\n# comment\n  \n") == []


def test_invalid_token_does_not_return_partial_list():
    with pytest.raises(ValueError):
        parse_workshop_list("123\nnot-a-workshop-id\n456")
