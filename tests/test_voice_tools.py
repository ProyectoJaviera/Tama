"""Modo conversación por voz: el modelo puede terminarla con `terminar_conversacion`."""

from tama.voice_tools import END_CONVERSATION, VoiceTools
from tests.test_web import Tools


def test_the_end_tool_is_added_only_inside_the_wrapper():
    plain = Tools()
    names = [t.name for t in plain.available_tools(10)[0]]
    assert END_CONVERSATION not in names
    wrapped = VoiceTools(plain)
    names = [t.name for t in wrapped.available_tools(10)[0]]
    assert names[-1] == END_CONVERSATION and "delete_note" in names
    assert wrapped.find(END_CONVERSATION).destructive is False
    assert wrapped.find("delete_note") is plain.info


def test_calling_it_does_not_touch_the_other_tools():
    plain = Tools()
    outcome = VoiceTools(plain).call(END_CONVERSATION, {})
    assert outcome.ok and "termina" in outcome.text
    assert plain.executed == []


def test_it_works_without_any_mcp_tools():
    wrapped = VoiceTools(None)
    assert [t.name for t in wrapped.available_tools(10)[0]] == [END_CONVERSATION]
