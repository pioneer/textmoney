"""Explicitly review intentional source edits used by lossless-edit fixtures."""

from app.journal import Journal
from app.synchronization import SyncConflict, file_hash


async def review_external_fixture(journal: Journal) -> None:
    try:
        await journal.load()
    except SyncConflict:
        state = journal.sync_state
        assert state is not None
        for _ in range(len(state.versions)):
            state = journal.sync_state
            assert state is not None
            if not state.versions:
                break
            current = await journal.storage.folder.read()
            assert current is not None
            await journal.resolve_sync(
                next(iter(state.versions)), state, current.decode(), local_hash=file_hash(current)
            )
    assert journal.ready
