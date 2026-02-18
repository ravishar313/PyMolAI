from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "modules"))

from pmg_qt.ai_chat_store import AiChatStore


def _store(tmp_path):
    return AiChatStore(root_dir=str(tmp_path / "chats"), flush_delay_sec=0.0, checkpoint_delay_sec=0.0)


def _save_stub(path: str):
    Path(path).write_bytes(b"PSE")


def test_create_bundle_append_and_checkpoint(tmp_path):
    store = _store(tmp_path)
    chat_id = store.create_chat("show aspirin")

    appended = store.append_events(
        chat_id,
        [
            {"role": "user", "text": "show aspirin", "ok": None, "metadata": {}},
            {
                "role": "tool_result",
                "text": "Executed: fetch aspirin",
                "ok": True,
                "metadata": {
                    "tool_name": "run_pymol_command",
                    "tool_args": {"command": "fetch aspirin"},
                    "tool_result_json": {"ok": True, "command": "fetch aspirin"},
                },
            },
        ],
    )
    assert appended == 2

    store.set_runtime_state(
        chat_id,
        {
            "input_mode": "ai",
            "history": [{"role": "user", "content": "show aspirin"}],
            "model_info": {"model": "openai/test"},
        },
    )
    store.mark_scene_dirty(chat_id, reason="command_ok")
    store.schedule_checkpoint(chat_id)
    store.pump(_save_stub)

    payload = store.load_chat(chat_id)
    assert payload is not None
    assert payload["session_exists"] is True
    assert len(payload["events"]) == 2
    assert payload["manifest"]["runtime_state"]["input_mode"] == "ai"


def test_list_search_and_pagination(tmp_path):
    store = _store(tmp_path)

    chat_a = store.create_chat("aspirin")
    store.append_events(chat_a, [{"role": "user", "text": "aspirin query", "metadata": {}}])
    store.flush_now()

    chat_b = store.create_chat("fmn")
    store.append_events(chat_b, [{"role": "user", "text": "fmn binding", "metadata": {}}])
    store.flush_now()

    chat_c = store.create_chat("5del")
    store.append_events(chat_c, [{"role": "user", "text": "open 5del", "metadata": {}}])
    store.flush_now()

    rows = store.list_chats(query="aspirin", offset=0, limit=10)
    assert any(r.get("chat_id") == chat_a for r in rows)

    page1 = store.list_chats(query="", offset=0, limit=2)
    page2 = store.list_chats(query="", offset=2, limit=2)
    ids = {r.get("chat_id") for r in page1 + page2}
    assert {chat_a, chat_b, chat_c}.issubset(ids)


def test_load_chat_with_missing_session_keeps_transcript(tmp_path):
    store = _store(tmp_path)
    chat_id = store.create_chat("test")
    store.append_events(chat_id, [{"role": "user", "text": "hello", "metadata": {}}])
    store.flush_now()

    payload = store.load_chat(chat_id)
    assert payload is not None
    assert payload["session_exists"] is False
    assert payload["events"][0]["text"] == "hello"


def test_delete_oldest_reduces_count(tmp_path):
    store = _store(tmp_path)
    store.create_chat("a")
    store.create_chat("b")
    store.create_chat("c")

    before = store.count_chats()
    deleted = store.delete_oldest(2)
    after = store.count_chats()

    assert before == 3
    assert deleted == 2
    assert after == 1


def test_runtime_state_is_truncated_and_mode_sanitized(tmp_path):
    store = _store(tmp_path)
    chat_id = store.create_chat("state")
    history = [{"role": "user", "content": str(i)} for i in range(120)]

    store.set_runtime_state(chat_id, {"input_mode": "CLI", "history": history, "model_info": "bad"})
    store.flush_now()

    payload = store.load_chat(chat_id)
    state = payload["manifest"]["runtime_state"]
    assert state["input_mode"] == "cli"
    assert len(state["history"]) == 80
    assert isinstance(state["model_info"], dict)
