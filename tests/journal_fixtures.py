"""Reload intentional source edits used by lossless-edit fixtures."""

from collections.abc import Callable

from app.infrastructure.beancount_journal import render
from app.journal import Journal


def decorate_transaction(journal: Journal, identifier: str, transform: Callable[[str], str]) -> str:
    assert journal.codec is not None
    entry = journal._transaction(journal.codec.document(journal.source), identifier)
    before = render(entry)
    position = entry.token_store.get_position(entry.first_token)
    start = sum(len(line) for line in journal.source.splitlines(keepends=True)[: position.line])
    start += position.column
    return journal.source[:start] + transform(before) + journal.source[start + len(before) :]


async def review_external_fixture(journal: Journal) -> None:
    await journal.load()
