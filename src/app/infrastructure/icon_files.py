"""Icon-specific policy for the shared immutable-record adapters."""

from app.icons import MAX_RECORD, MAX_RECORDS, IconError
from app.infrastructure.android_storage import AndroidFolder
from app.infrastructure.desktop_storage import DesktopFolder
from app.infrastructure.record_files import AndroidRecordFiles, DesktopRecordFiles, RecordPolicy

POLICY = RecordPolicy(
    "textmoney-icons",
    "icons",
    MAX_RECORD,
    MAX_RECORDS,
    16 * 1024 * 1024,
    IconError,
    "icon_records_invalid",
    "icon_size",
    "icon_storage",
)


class DesktopIconFiles(DesktopRecordFiles):
    def __init__(self, folder: DesktopFolder) -> None:
        super().__init__(folder, POLICY)


class AndroidIconFiles(AndroidRecordFiles):
    def __init__(self, folder: AndroidFolder) -> None:
        super().__init__(folder, POLICY)
