from app.ai.claude.agents.debugger import DebuggerAgent


class FakeUsage:
    input_tokens = 100
    output_tokens = 50
    cache_creation_input_tokens = 0
    cache_read_input_tokens = 0


class FakeResponse:
    usage = FakeUsage()


def test_track_tokens_increments_counter():
    agent = DebuggerAgent()
    agent._track_tokens(FakeResponse())
    assert agent.token_counter["input"] == 100
    assert agent.token_counter["output"] == 50
    assert agent.token_counter["calls"] == 1


def test_track_tokens_handles_no_usage():
    agent = DebuggerAgent()

    class NoUsageResp:
        usage = None

    agent._track_tokens(NoUsageResp())
    assert agent.token_counter["calls"] == 1


def test_track_tokens_handles_none():
    agent = DebuggerAgent()
    agent._track_tokens(None)
    assert agent.token_counter["calls"] == 1


def test_track_tokens_accumulates_across_calls():
    agent = DebuggerAgent()
    agent._track_tokens(FakeResponse())
    agent._track_tokens(FakeResponse())
    assert agent.token_counter["input"] == 200
    assert agent.token_counter["output"] == 100
    assert agent.token_counter["calls"] == 2
