# Framework integrations

Every adapter imports its framework lazily, so `import tea` never requires any
of them. Each adapter routes through `tea.optimize(...)`, so the `log=`
argument and the per-call `source` tag work everywhere.

## OpenAI SDK

```python
from openai import OpenAI
from tea.integrations.openai_wrap import wrap_openai

client = wrap_openai(OpenAI(), log="tea_logs")
client.chat.completions.create(model="gpt-4o", messages=[...])
```

`wrap_openai` patches `client.chat.completions.create` so every call has its
`messages` optimised first. To optimise without patching:

```python
from tea.integrations.openai_wrap import optimize_openai_kwargs
kwargs, report = optimize_openai_kwargs({"model": "gpt-4o", "messages": [...]})
```

## Anthropic SDK

```python
from anthropic import Anthropic
from tea.integrations.anthropic_wrap import wrap_anthropic

client = wrap_anthropic(Anthropic(), log="tea_logs")
client.messages.create(model="claude-sonnet-4-6", max_tokens=1024,
                       system="...", messages=[...])
```

The Anthropic API separates `system` from `messages`, so the adapter optimises
both and reports the combined saving. Anthropic has no public tokenizer, so
counts use a close approximation; relative before/after numbers stay valid.

## LangChain

```python
from langchain_openai import ChatOpenAI
from tea.integrations.langchain_cb import TEAOptimizer

model = ChatOpenAI(model="gpt-4o")
chain = TEAOptimizer(model_name="gpt-4o", log="tea_logs") | model
chain.invoke(messages)
```

`TEAOptimizer` is a `RunnableLambda`, so it slots into any LCEL chain with `|`.
To optimise a message list directly:

```python
from tea.integrations.langchain_cb import optimize_lc_messages
new_messages, report = optimize_lc_messages(messages, model_name="gpt-4o")
```

## CrewAI

```python
from tea.integrations.crewai_hook import optimize_agents, optimize_tasks

optimize_agents(agents, log="tea_logs")   # trims role/goal/backstory in place
optimize_tasks(tasks, log="tea_logs")     # trims description/expected_output
crew.kickoff()
```

The biggest token sink in CrewAI runs is usually a long backstory carried into
every step. These functions mutate the objects in place and return a merged
report.

## AutoGen

```python
from tea.integrations.autogen_hook import TEAMessageTransform
from autogen.agentchat.contrib.capabilities import transform_messages

handler = transform_messages.TransformMessages(
    transforms=[TEAMessageTransform(model_name="gpt-4o", log="tea_logs")]
)
handler.add_to_agent(assistant)
```

`TEAMessageTransform` protects the most recent message (the live turn) and
optimises earlier context. To transform a list directly:

```python
from tea.integrations.autogen_hook import optimize_autogen_messages
new_messages, report = optimize_autogen_messages(messages, model_name="gpt-4o")
```

## Common arguments

Every adapter accepts:

- `enable`: which transforms to run (default `tea.SAFE_TRANSFORMS`).
- `compressor`: an optional `(text, target_ratio) -> str` callable for the
  `compress` transform.
- `log`: `True`, a directory path, `False`, or a `TEALogger` instance.
