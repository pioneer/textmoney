"""Shared manual and picker date/time controls for expense forms."""

from datetime import date, datetime

import flet as ft

from app.domain.ledger import LedgerError
from app.localization import date_input, parse_date_input, translate
from app.timekeeping import local_datetime, wall_timestamp


class DateTimeFields(ft.Column):
    def __init__(
        self,
        language: str,
        zone: str,
        occurred: date | None = None,
        timestamp: str | None = None,
        *,
        device_zone: str | None = "system",
    ) -> None:
        super().__init__(spacing=8, width=320)
        self.language = language
        self.zone = zone
        self.device_zone = device_zone
        stamp = local_datetime(
            datetime.fromisoformat(timestamp) if timestamp else datetime.now().astimezone(), zone
        )
        self.original_occurred = occurred or stamp.date()
        self.original_timestamp = timestamp or stamp.isoformat()
        self.occurred = ft.TextField(value=date_input(stamp.date(), language))
        self.clock = ft.TextField(value=stamp.strftime("%H:%M:%S"))
        self.original_fields = self._field_values()
        self.initial_values = self._field_values()
        self._labels()

    def _field_values(self) -> tuple[str | None, str | None]:
        return self.occurred.value, self.clock.value

    @property
    def pristine(self) -> bool:
        return self._field_values() == self.initial_values

    def set_device_zone(self, zone: str | None) -> None:
        self.device_zone = zone
        self._labels()

    def set_zone(self, zone: str) -> None:
        if zone == self.zone:
            return
        pristine = self.pristine
        occurred, timestamp = self.values()
        stamp = local_datetime(datetime.fromisoformat(timestamp), zone)
        self.occurred.value = date_input(stamp.date(), self.language)
        self.clock.value = stamp.strftime("%H:%M:%S")
        self.original_occurred = date.fromisoformat(occurred)
        self.original_timestamp = timestamp
        self.original_fields = self._field_values()
        self.zone = zone
        if pristine:
            self.initial_values = self._field_values()
        self._labels()

    def tr(self, key: str, **values: object) -> str:
        return translate(self.language, key, **values)

    def _labels(self) -> None:
        self.occurred.label = self.tr("date")
        self.occurred.hint_text = self.tr("date_hint")
        self.occurred.helper = self.tr("date_hint")
        self.clock.label = self.tr("time")
        self.clock.hint_text = self.tr("time_hint")
        self.controls = [
            self._date_row(self.occurred),
            ft.Row(
                [
                    self.clock,
                    ft.IconButton(
                        icon=ft.Icons.SCHEDULE,
                        tooltip=self.tr("pick_time"),
                        on_click=self._pick_time,
                    ),
                ],
                spacing=0,
            ),
            ft.Text(
                self.tr(
                    "timezone_current",
                    zone=self.zone
                    if self.zone != "system"
                    else (f"{self.tr('timezone_system')} ({datetime.now().astimezone():%Z %z})"),
                ),
                size=12,
            ),
        ]
        self.clock.expand = True

    def set_language(self, language: str) -> None:
        if language == self.language:
            return
        pristine = self.pristine
        try:
            value = parse_date_input(self.occurred.value or "", self.language)
        except LedgerError:
            # A partially typed date retains its original format and hint.
            return
        original_date = parse_date_input(self.original_fields[0] or "", self.language)
        self.occurred.value = date_input(value, language)
        self.original_fields = (date_input(original_date, language), self.original_fields[1])
        self.language = language
        if pristine:
            self.initial_values = self._field_values()
        self._labels()

    def values(self) -> tuple[str, str]:
        if self._field_values() == self.original_fields:
            return self.original_occurred.isoformat(), self.original_timestamp
        occurred = parse_date_input(self.occurred.value or "", self.language)
        return occurred.isoformat(), wall_timestamp(occurred, self.clock.value or "", self.zone)

    def _date_row(self, field: ft.TextField) -> ft.Row:
        field.expand = True

        def pick() -> None:
            try:
                value = parse_date_input(field.value or "", self.language)
            except LedgerError:
                value = local_datetime(datetime.now().astimezone(), self.zone).date()

            def selected(event: ft.Event[ft.DatePicker]) -> None:
                if isinstance(picker.value, (date, datetime)):
                    chosen = picker.value
                    field.value = date_input(
                        local_datetime(chosen, self.device_zone or "system").date()
                        if isinstance(chosen, datetime)
                        else chosen,
                        self.language,
                    )
                    self.page.update()

            if self.device_zone is None:
                raise LedgerError("crud_timezone_unavailable")
            picker = ft.DatePicker(
                # Flet transports dates as instants; use client-local noon to avoid day shifts.
                value=datetime.fromisoformat(wall_timestamp(value, "12:00", self.device_zone)),
                first_date=datetime.fromisoformat(
                    wall_timestamp(date(1970, 1, 1), "12:00", self.device_zone)
                ),
                last_date=datetime.fromisoformat(
                    wall_timestamp(date(2100, 12, 31), "12:00", self.device_zone)
                ),
                locale=ft.Locale(language_code=self.language),
                on_change=selected,
            )
            self.page.show_dialog(picker)

        return ft.Row(
            [
                field,
                ft.IconButton(
                    icon=ft.Icons.CALENDAR_TODAY,
                    tooltip=self.tr(
                        "pick_date" if self.device_zone is not None else "crud_timezone_unavailable"
                    ),
                    on_click=pick,
                    disabled=self.device_zone is None,
                ),
            ],
            spacing=0,
        )

    def _pick_time(self) -> None:
        try:
            value = datetime.strptime(self.clock.value or "", "%H:%M:%S").time()
        except ValueError:
            try:
                value = datetime.strptime(self.clock.value or "", "%H:%M").time()
            except ValueError:
                value = local_datetime(datetime.now().astimezone(), self.zone).time()

        def selected() -> None:
            if picker.value is not None:
                self.clock.value = picker.value.strftime("%H:%M:%S")
                self.page.update()

        picker = ft.TimePicker(
            value=value,
            hour_format=ft.TimePickerHourFormat.H24,
            locale=ft.Locale(language_code=self.language),
            on_change=selected,
        )
        self.page.show_dialog(picker)
