from __future__ import annotations

# Importing the eval runner puts backend/app on sys.path, which makes the demo
# review engine importable by module name (mirrors tests/test_eval_harness.py).
import evals.run_local  # noqa: F401
from llm_reviewer import _estimate_risk, _risk_tokens

RUNTIME_FILES = ["backend/app/service.py"]


def test_camelcase_identifiers_are_high_risk() -> None:
    assert _estimate_risk("sessionAuthToken = build(user)", RUNTIME_FILES) == "High"
    assert _estimate_risk("return deleteUser(payload.id)", RUNTIME_FILES) == "High"
    assert _estimate_risk("result = runSql(params.query)", RUNTIME_FILES) == "High"
    assert _estimate_risk("userPassword = value", RUNTIME_FILES) == "High"


def test_pascalcase_identifier_is_high_risk() -> None:
    assert _estimate_risk("class PaymentProcessor:", ["backend/app/billing.py"]) == "High"
    assert _estimate_risk("result = RunSQL(query)", RUNTIME_FILES) == "High"


def test_substring_lookalikes_stay_low_risk() -> None:
    assert _estimate_risk("def tokenize(value):", ["backend/app/tokenizer.py"]) == "Low"
    assert _estimate_risk("display_author_name(author)", ["backend/app/author_profile.py"]) == "Low"
    assert _estimate_risk("render_card(authorProfile)", ["backend/app/author_widget.py"]) == "Low"
    assert _estimate_risk("format_deleted_at(value)", ["backend/app/formatters.py"]) == "Low"


def test_non_runtime_guard_keeps_risk_words_low() -> None:
    # Risk wording confined to docs/tests must not raise runtime risk.
    assert _estimate_risk("Payment and token glossary", ["docs/payment_terms.md"]) == "Low"
    assert (
        _estimate_risk(
            "def test_token():",
            ["tests/test_payment_copy.py", "tests/fixtures/payment_token.json"],
        )
        == "Low"
    )


def test_risk_tokens_splits_identifier_styles() -> None:
    assert {"auth", "token"} <= _risk_tokens("sessionAuthToken")
    assert {"delete", "user"} <= _risk_tokens("deleteUser")
    assert {"run", "sql"} <= _risk_tokens("runSQL")
    assert {"payment", "processor"} <= _risk_tokens("PaymentProcessor")


def test_risk_tokens_keep_lookalikes_intact() -> None:
    assert "auth" not in _risk_tokens("author")
    assert "auth" not in _risk_tokens("authorProfile")
    assert "token" not in _risk_tokens("tokenizer")
    assert "delete" not in _risk_tokens("deletedAt")
