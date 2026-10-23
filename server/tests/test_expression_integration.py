import asyncio
import pytest
from types import SimpleNamespace
from app.agent.runner import AgentTurn
from app.chat.expression import ReplyExpression
from app.chat.reply_service import ChatReplyService
from app.chat.reply_store import ChatReplyStore
from app.memory.models import MemoryScope
from app.persona.emotion import EmotionStore
from app.services.agent_service import AgentService
from app.services.session_service import SessionService
from app.session.models import SessionCreate
from app.session.store import FileSessionStore


@pytest.mark.parametrize("fail_emotion", [False, True])
def test_history_restart_and_one_notification(tmp_dir, fail_emotion):
    async def run():
        sessions = SessionService(FileSessionStore(tmp_dir / 'sessions'))
        session = await sessions.create(SessionCreate(user_id='u'))
        await sessions.append_message(session.id, {'id':'u1', 'role':'user', 'content':'我通过了'})
        store = ChatReplyStore(tmp_dir / 'replies.db')
        store.enqueue(session_id=session.id, user_id='u', agent_id='default', message_id='u1',
                      debounce_seconds=0, capabilities=['message_sequence_v1', 'stickers_v1'])
        planning = store.claim_due()
        store.finish_planning(planning.id, outcome='reply')
        job = store.claim_due()
        calls, pushes = [], []
        class Emotions(EmotionStore):
            def apply(self, *a, **kw):
                if fail_emotion:
                    raise RuntimeError('simulated state-store error')
                return super().apply(*a, **kw)
        moods = Emotions(tmp_dir / 'emotion.db')
        class Persona:
            emotions = moods
            def resolve_presentation(self, _): return 'female'
            def on_assistant_message(self, *a, **kw): pass
        class Agent:
            settings = SimpleNamespace(chat_expression_enabled=True, chat_message_gap_seconds=1)
            persona_service = Persona()
            dense_onboarding_hook = None
            _content_text = staticmethod(AgentService._content_text)
            tool_factory = staticmethod(lambda scope: [])
            async def reply_to_messages(self, *args, **kw):
                calls.append(1)
                assert kw['defer_delivery']
                plan = ReplyExpression(['你过了？', '太好了。'], {'name':'happy', 'intensity':.4, 'reason':'通过考试'})
                return AgentTurn(text=plan.text, expression=plan), MemoryScope(user_id='u'), session, {'content':plan.text}
        class Push:
            async def send(self, *args): pushes.append(1)
        def service(store):
            return ChatReplyService(store=store, planner=None, agent_service=Agent(), session_service=sessions,
                presence=SimpleNamespace(device_tokens=lambda *a: []), push_sender=Push())
        svc = service(store)
        await svc._reply(job)
        assert len((await sessions.get(session.id)).messages) == 1
        assert moods.current('u')['name'] == 'calm'
        await svc.deliver_one()
        assert [m['content'] for m in (await sessions.get(session.id)).messages] == ['我通过了', '你过了？']
        assert moods.current('u')['name'] == ('calm' if fail_emotion else 'happy')
        with store._connect() as db:
            db.execute('UPDATE chat_reply_parts SET due_at=0')
        await service(ChatReplyStore(store.path)).process_one()
        assert [m['content'] for m in (await sessions.get(session.id)).messages] == ['我通过了', '你过了？', '太好了。']
        assert len(calls) == 1 and len(pushes) == 1
    asyncio.run(run())
