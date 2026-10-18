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
        self.original_timestamp = timestamp
        stamp = local_datetime(
            datetime.fromisoformat(timestamp) if timestamp else datetime.now().astimezone(), zone
        )
        self.occurred = ft.TextField(value=date_input(occurred or stamp.date(), language))
        self.stamp_date = ft.TextField(value=date_input(stamp.date(), language))
        self.clock = ft.TextField(value=stamp.strftime("%H:%M:%S"))
        self.original_fields = (self.stamp_date.value, self.clock.value)
        self.initial_values = self._field_values()
        self._labels()

    def _field_values(self) -> tuple[str | None, str | None, str | None]:
        return self.occurred.value, self.stamp_date.value, self.clock.value

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
        _, timestamp = self.values()
        stamp = local_datetime(datetime.fromisoformat(timestamp), zone)
        self.stamp_date.value = date_input(stamp.date(), self.language)
        self.clock.value = stamp.strftime("%H:%M:%S")
        if self.original_timestamp is not None:
            original = local_datetime(datetime.fromisoformat(self.original_timestamp), zone)
            self.original_fields = (
                date_input(original.date(), self.language),
                original.strftime("%H:%M:%S"),
            )
        self.zone = zone
        if pristine:
            self.initial_values = self._field_values()
        self._labels()

    def tr(self, key: str, **values: object) -> str:
        return translate(self.language, key, **values)

    def _labels(self) -> None:
        self.occurred.label = self.tr("date")
        self.stamp_date.label = self.tr("recorded_date")
        for field in (self.occurred, self.stamp_date):
            field.hint_text = self.tr("date_hint")
            field.helper = self.tr("date_hint")
        self.clock.label = self.tr("time")
        self.clock.hint_text = self.tr("time_hint")
        self.controls = [
            self._date_row(self.occurred),
            self._date_row(self.stamp_date),
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
            ft.Text(self.tr("timestamp_help"), size=12),
        ]
        self.clock.expand = True

    def set_language(self, language: str) -> None:
        if language == self.language:
            return
        pristine = self.pristine
        try:
            dates = [
                parse_date_input(field.value or "", self.language)
                for field in (self.occurred, self.stamp_date)
            ]
        except LedgerError:
            # A partially typed date retains its original format and hint.
            return
        original_stamp_date = parse_date_input(self.original_fields[0] or "", self.language)
        for field, value in zip((self.occurred, self.stamp_date), dates, strict=True):
            field.value = date_input(value, language)
        self.original_fields = (date_input(original_stamp_date, language), self.original_fields[1])
        self.language = language
        if pristine:
            self.initial_values = self._field_values()
        self._labels()

    def values(self) -> tuple[str, str]:
        occurred = parse_date_input(self.occurred.value or "", self.language)
        if (
            self.original_timestamp is not None
            and (self.stamp_date.value, self.clock.value) == self.original_fields
        ):
            return occurred.isoformat(), self.original_timestamp
        stamp_day = parse_date_input(self.stamp_date.value or "", self.language)
        return occurred.isoformat(), wall_timestamp(stamp_day, self.clock.value or "", self.zone)

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
