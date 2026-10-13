import pytest

from app.router.classifier import classify


@pytest.mark.parametrize(
    "text, task",
    [
        # conversation
        ("Hi there!", "conversation"),
        ("Hello, how are you today?", "conversation"),
        ("Tell me a joke", "conversation"),
        ("thanks!", "conversation"),
        # coding
        ("Fix this bug in my Python function:\n```python\ndef f(x):\n    return x/0\n```", "coding"),
        ("Why do I get a TypeError: 'NoneType' object is not subscriptable in my script?", "coding"),
        ("Write a SQL query to find duplicate emails", "coding"),
        ("Refactor this class to use async/await in TypeScript", "coding"),
        # summarization
        ("Summarize this article in 3 bullet points", "summarization"),
        ("Give me a TL;DR of the following text", "summarization"),
        # extraction
        ("Extract all the names and dates from this invoice and return them as JSON", "extraction"),
        ("Parse this CSV and output a table", "extraction"),
        # reasoning
        ("Solve step by step: 12 * 7 + 5 = ?", "reasoning"),
        ("Prove that the square root of 2 is irrational", "reasoning"),
        ("What are the trade-offs between microservices and a monolith? Analyze pros and cons.", "reasoning"),
        ("Calculate the probability of rolling two sixes", "reasoning"),
    ],
)
def test_each_category_is_recognised(text, task):
    result = classify(text)
    assert result.task_type == task
    assert result.confidence in ("high", "medium")
    assert result.is_known and result.signals


@pytest.mark.parametrize(
    "text",
    [
        "What is the capital of France?",
        "Tell me about dogs",
        "Explain recursion",
        "Write a poem about autumn",
        "I have a bug",  # ambiguous on its own: stays unknown rather than guessing
        "java",  # a single weak signal is not enough
        "",
        "   \n ",
    ],
)
def test_thin_or_ambiguous_prompts_are_unknown(text):
    result = classify(text)
    assert result.task_type == "unknown" and result.confidence == "low" and not result.is_known


def test_ambiguous_tie_is_unknown():
    # a greeting (weight 3) and a code fence (weight 3): no honest winner
    assert classify("hello ```x```").task_type == "unknown"


def test_is_deterministic():
    text = "Summarize this Python code in 2 sentences"
    assert classify(text) == classify(text)


def test_signals_are_rule_names_not_prompt_text():
    secret = "ProjectFalcon-9F3A"
    result = classify(f"Debug this function for {secret}: ```x```")
    assert result.task_type == "coding"
    assert all(secret not in s for s in result.signals)
    assert all(s.replace("_", "").isalpha() for s in result.signals)


def test_classification_does_not_modify_or_depend_on_length_alone():
    long_plain = "word " * 1000
    assert classify(long_plain).task_type == "unknown"
