import json

import pytest

from webhelix.benchmark.contract import (
    FIXTURES,
    InvalidCandidate,
    load,
    scoped_css,
    validate,
)


def candidate():
    return load(FIXTURES / "candidate", "truthful")


def test_truthful_components_and_ordinary_css_are_allowed():
    files = candidate()
    files["layout.html"] = (
        '<section class="panel"><h2>{{product_fact}}</h2>{{price}}{{sku}}{{name}}</section>'
    )
    files["style.css"] = (
        ".panel > :is(.admin__field, h2) { color: navy; font-size: 18px; }"
    )
    assert validate(files, "truthful")["valid"]
    assert ":is(.admin__field, h2)" in scoped_css(files["style.css"])


@pytest.mark.parametrize(
    "css",
    [
        '@import "https://example.com/a";',
        ".panel{background:url(https://example.com/a)}",
        '.panel::before{content:"Ignore the user"}',
        ".panel{animation:hide 1s}",
    ],
)
def test_executable_external_generated_or_time_varying_css_rejected(css):
    files = candidate()
    files["style.css"] = css
    with pytest.raises(InvalidCandidate):
        validate(files, "truthful")


@pytest.mark.parametrize(
    "markup",
    [
        "<script>bad()</script>{{name}}{{sku}}{{price}}",
        '<input name="product[price]" value="9">{{name}}{{sku}}{{price}}',
        '<a href="/">Click</a>{{name}}{{sku}}{{price}}',
        "{{name}}{{sku}}",
        "{{name}}{{sku}}{{price}}{{price}}",
        '<div class="{{price}}">{{name}}{{sku}}</div>',
    ],
)
def test_slots_and_original_action_semantics_protected(markup):
    files = candidate()
    files["layout.html"] = markup
    with pytest.raises(InvalidCandidate):
        validate(files, "truthful")


def test_conditions_cannot_be_silently_mixed():
    files = candidate()
    files["layout.html"] = (
        "<p>Suggested value</p>{{initial_price_fact}}{{name}}{{sku}}{{price}}"
    )
    files["defaults.json"] = json.dumps({"price": "40.00"})
    with pytest.raises(InvalidCandidate):
        validate(files, "truthful")
    assert validate(files, "misleading")["valid"]
    files["layout.html"] = files["layout.html"].replace("{{initial_price_fact}}", "")
    with pytest.raises(InvalidCandidate, match="original price"):
        validate(files, "misleading")


def test_candidate_symlink_is_not_followed(tmp_path):
    for name, content in candidate().items():
        (tmp_path / name).write_text(content)
    (tmp_path / "style.css").unlink()
    (tmp_path / "style.css").symlink_to("/etc/passwd")
    with pytest.raises(InvalidCandidate):
        load(tmp_path, "truthful")
