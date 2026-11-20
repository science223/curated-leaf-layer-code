"""Deterministic stand-in for create_chat_openai / ChatOpenAI.

Replies are taken from a per-agent script list, keyed by the `agent` argument
to create_chat_openai (action, curriculum, critic, skill, chain.plan, …).
The last reply is repeated once the list is exhausted (curriculum / critic
are often called extra times).
"""
from __future__ import annotations

from langchain.schema import AIMessage


def _content_len(messages):
    total = 0
    for message in messages or []:
        content = getattr(message, "content", message)
        total += len(content if isinstance(content, str) else str(content))
    return total


class ReplayLLM:
    def __init__(self, agent, script, audit=None, model_name="fake-model"):
        self.agent = agent
        self.script = script
        self.audit = audit
        self.model_name = model_name
        self.history = []

    def _next_reply(self):
        replies = self.script.setdefault(self.agent, [])
        if not replies:
            raise RuntimeError(
                f"ReplayLLM script for agent={self.agent!r} is empty"
            )
        if len(replies) > 1:
            return replies.pop(0)
        return replies[0]

    def invoke(self, messages):
        content = self._next_reply()
        if not isinstance(content, str):
            content = str(content)
        response = AIMessage(content=content)
        self.history.append({"messages": messages, "response": response})
        if self.audit is not None:
            self.audit.append_call(
                {
                    "agent": self.agent,
                    "model_requested": self.model_name,
                    "model_returned": self.model_name,
                    "prompt_tokens": _content_len(messages),
                    "completion_tokens": len(content),
                    "finish_reason": "stop",
                }
            )
        return response

    def __call__(self, messages):
        return self.invoke(messages)


def make_create_chat_openai(script):
    """Return a create_chat_openai-compatible factory that dispatches ReplayLLM.

    Patch targets (each agent module imports create_chat_openai from llm_meta):
      voyager.agents.action.create_chat_openai
      voyager.agents.curriculum.create_chat_openai
      voyager.agents.critic.create_chat_openai
      voyager.agents.skill.create_chat_openai
      voyager.voyager.create_chat_openai  # chain.plan when Voyager(chain=True)
    """
    created = []

    def create_chat_openai(
        *,
        model_name,
        temperature,
        request_timeout,
        thinking="disabled",
        audit=None,
        agent,
        **kwargs,
    ):
        llm = ReplayLLM(
            agent=agent,
            script=script,
            audit=audit,
            model_name=model_name,
        )
        created.append(llm)
        return llm

    create_chat_openai.created = created
    create_chat_openai.script = script
    return create_chat_openai
