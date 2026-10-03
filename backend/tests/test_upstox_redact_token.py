import json
from datetime import datetime, timezone

import pytest

from app.upstox.redact import REDACTED, redact
from app.upstox.token import DataToken, jwt_expiry
from tests.upstox_helpers import FAKE_TOKEN


def test_redact_removes_the_exact_token_bearer_headers_and_jwt_shaped_strings() -> None:
    secret = "plain-secret-token-123"
    text = f"failed with {secret}; Authorization: Bearer abc.def.ghi and {FAKE_TOKEN} and 'authorization': 'Bearer xyz'"
    out = redact(text, [secret])
    assert secret not in out
    assert FAKE_TOKEN not in out
    assert "abc.def.ghi" not in out
    assert "xyz" not in out
    assert REDACTED in out


def test_redact_leaves_ordinary_text_alone_and_ignores_empty_secrets() -> None:
    assert redact("HTTP 429 too many requests", [None, ""]) == "HTTP 429 too many requests"


def test_datatoken_never_prints_itself() -> None:
    t = DataToken(FAKE_TOKEN)
    assert FAKE_TOKEN not in repr(t)
    assert FAKE_TOKEN not in str(t)
    assert FAKE_TOKEN not in f"{t}"
    assert t.reveal() == FAKE_TOKEN
    with pytest.raises(ValueError):
        DataToken("   ")


def test_jwt_expiry_is_read_locally_from_the_exp_claim() -> None:
    exp = jwt_expiry(FAKE_TOKEN)
    assert exp == datetime.fromtimestamp(4102444800, tz=timezone.utc)  # 2100-01-01


@pytest.mark.parametrize(
    "token",
    ["not-a-jwt", "a.b", "a.b.c.d", "aaa.%%%.ccc", "", "x.e30.y"],  # e30 = {} (no exp)
)
def test_jwt_expiry_is_none_for_anything_that_is_not_a_jwt_with_exp(token: str) -> None:
    assert jwt_expiry(token) is None


def test_jwt_expiry_rejects_non_numeric_exp() -> None:
    import base64

    payload = base64.urlsafe_b64encode(json.dumps({"exp": "soon"}).encode()).decode().rstrip("=")
    assert jwt_expiry(f"h.{payload}.s") is None
