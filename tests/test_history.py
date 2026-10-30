from tama.history import estimate_tokens, trim


def msg(role, n):
    return {"role": role, "content": "x" * n}


def test_keeps_everything_when_short():
    messages = [msg("system", 100), msg("user", 10), msg("assistant", 10)]
    assert trim(messages, 2000) == messages


def test_drops_oldest_turns_but_keeps_system_and_latest():
    system = msg("system", 4000)  # la identidad no cuenta para el límite
    old = [msg("user", 3500), msg("assistant", 3500)]
    recent = [msg("user", 350), msg("assistant", 350), msg("user", 35)]
    result = trim([system, *old, *recent], max_tokens=300)

    assert result[0] is system
    assert result[1:] == recent


def test_dialog_starts_with_user_after_trim():
    messages = [msg("user", 3500), msg("assistant", 700), msg("user", 35)]
    result = trim(messages, max_tokens=250)
    assert result[0]["role"] == "user"


def test_last_message_survives_even_if_too_long():
    big = msg("user", 100_000)
    assert trim([big], max_tokens=10) == [big]


def test_estimate_is_roughly_chars_over_3_5():
    assert 280 <= estimate_tokens("a" * 1000) <= 300
