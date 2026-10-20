import pytest

from webhelix.optimizer.backend import proposal_check
from webhelix.v2.contract import (
    ROOT,
    FIXTURES,
    InvalidCandidate,
    load_candidate,
    read,
    validate_css,
)


def test_actual_rejected_proposal_is_unchanged_and_now_valid():
    proposal = read(
        ROOT
        / "examples/optimizer-adapter/real-research-test-20261019/optimizer-proposal.json"
    )
    assert ".room-selection > h2" in proposal["files"]["style.css"]
    assert (
        proposal_check(proposal, load_candidate(FIXTURES / "candidate"))["status"]
        == "valid_proposal"
    )


@pytest.mark.parametrize(
    "css",
    [
        "main > h2 { color: green; }",
        "article + article, h2 ~ p { font-size: 24px; }",
        "section:is(.a, .b) > p { width: calc(100% - 2px); }",
        'p::before {content: ">";}',
    ],
)
def test_ordinary_selector_and_value_syntax(css):
    validate_css(css)


@pytest.mark.parametrize(
    "css",
    [
        '@import "https://example.invalid/style.css";',
        'body { background: URL("https://example.invalid/a"); }',
        "body { background: url(https://example.invalid/a); }",
        'body { background: image-set("https://example.invalid/a" 1x); }',
        "body { width: expression(alert(1)); }",
        "body { behavior: something; }",
        "body { -moz-binding: something; }",
        r"body { color: \72 ed; }",
        'body { color: red; @import "x"; }',
        'body { background: var(--x, url("https://example.invalid/a")); }',
        'body { content: "</style><script>something()</script>"; }',
        "body { color: > red; }",
        "body { not-a-declaration }",
    ],
)
def test_forbidden_or_malformed_css_remains_invalid(css):
    with pytest.raises(InvalidCandidate):
        validate_css(css)


def test_inline_css_uses_the_same_restrictions():
    validate_css("color: green; font-size: 24px", inline=True)
    with pytest.raises(InvalidCandidate):
        validate_css('background: url("https://example.invalid/a")', inline=True)
