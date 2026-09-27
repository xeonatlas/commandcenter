import pytest

from render_config import main, parse_env, render


def test_parse_env_skips_comments_and_blank_lines():
    assert parse_env("# secret\n\nA=1\n  B = two  \n") == {"A": "1", "B": "two"}


def test_parse_env_strips_matching_quotes_only():
    text = "A=\"x y\"\nB='z'\nC=\"unbalanced'\n"
    assert parse_env(text) == {"A": "x y", "B": "z", "C": "\"unbalanced'"}


def test_parse_env_rejects_lines_without_equals():
    with pytest.raises(ValueError, match="line 2"):
        parse_env("A=1\nnot a pair\n")


def test_render_fills_placeholders():
    assert render("user: ${USER_KEY}\n", {"USER_KEY": "u123"}) == "user: u123\n"


def test_render_reports_every_missing_variable():
    with pytest.raises(ValueError) as exc:
        render("a: ${FIRST}\nb: ${SECOND}\n", {})
    assert "${FIRST}" in str(exc.value)
    assert "${SECOND}" in str(exc.value)


def test_render_treats_empty_value_as_missing():
    with pytest.raises(ValueError, match=r"\$\{TOKEN\}"):
        render("token: ${TOKEN}", {"TOKEN": ""})


def test_render_does_not_re_expand_dollars_inside_values():
    assert render("url: ${URL}", {"URL": "https://hc-ping.com/a$b"}) == "url: https://hc-ping.com/a$b"


def test_main_writes_a_private_file(tmp_path):
    (tmp_path / "t").write_text("k: ${K}\n")
    (tmp_path / "e").write_text("K=v\n")
    out = tmp_path / "out.yml"
    assert main(["render_config.py", str(tmp_path / "t"), str(tmp_path / "e"), str(out)]) == 0
    assert out.read_text() == "k: v\n"
    assert out.stat().st_mode & 0o777 == 0o600


def test_main_fails_without_writing_when_a_secret_is_missing(tmp_path, capsys):
    (tmp_path / "t").write_text("k: ${K}\n")
    (tmp_path / "e").write_text("OTHER=v\n")
    out = tmp_path / "out.yml"
    assert main(["render_config.py", str(tmp_path / "t"), str(tmp_path / "e"), str(out)]) == 1
    assert not out.exists()
    assert "${K}" in capsys.readouterr().err
