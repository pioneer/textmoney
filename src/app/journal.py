"""Single-file journal operations through the tested backup/conflict policy."""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4

from autobean_refactor import models

from app.domain.ledger import (
    ADJUSTMENTS,
    BANK_FEES,
    EPOCH,
    EQUITY,
    ZERO,
    Account,
    Category,
    Ledger,
    LedgerError,
    Posting,
    Transaction,
    TransferDetails,
    account_path,
    currency_code,
    description_text,
    display_name,
    money,
)
from app.domain.storage import ConflictError
from app.infrastructure.beancount_journal import (
    JournalCodec,
    append,
    equity_declaration,
    open_account,
    open_category,
    render,
    replace_directive,
    transaction_text,
)
from app.storage import Folder, StorageError, StorageSession

FILENAME = "txncount.beancount"


class Journal:
    def __init__(self, folder: Folder, *, today: Callable[[], date] = date.today) -> None:
        self.storage = StorageSession(folder, missing_text="")
        self.ledger = Ledger()
        self.source = ""
        self.ready = False
        self.existed = False
        self.codec: JournalCodec | None = None
        self.today = today

    def _parse(self, source: str) -> Ledger:
        if self.codec is None:
            self.codec = JournalCodec(today=self.today)
        return self.codec.parse(source)

    async def load(self) -> None:
        self.ready = False
        exists = await self.storage.read()
        if self.existed and not exists:
            self.storage.loaded = False
            raise StorageError(
                "The journal disappeared. Restore it before reloading; forms retained."
            )
        try:
            ledger = await asyncio.to_thread(self._parse, self.storage.draft)
        except LedgerError:
            self.storage.loaded = False
            raise
        self.source = self.storage.draft
        self.ledger = ledger
        self.existed = exists
        self.ready = True

    async def poll(self) -> bool:
        if not self.storage.loaded:
            return False
        if await self.storage.poll():
            await self.load()
            return True
        return False

    def _require_ready(self) -> None:
        if not self.ready or not self.storage.loaded:
            raise LedgerError("Reload a valid journal first. Unsaved form input is retained.")

    async def _save(self, addition: str) -> str | None:
        self._require_ready()
        candidate = append(self.source, addition)
        return await self._commit(candidate)

    async def _commit(self, candidate: str) -> str | None:
        self._require_ready()
        ledger = await asyncio.to_thread(self._parse, candidate)
        self.storage.draft = candidate
        try:
            backup = await self.storage.write()
        except (StorageError, ConflictError, TimeoutError, asyncio.CancelledError):
            self.ready = False
            self.storage.loaded = False
            raise
        self.source = candidate
        self.ledger = ledger
        self.existed = True
        return backup

    async def _change(
        self, operation: Callable[[models.File], str], expected_source: str
    ) -> str | None:
        self._require_ready()
        if expected_source != self.source:
            raise ConflictError("crud_stale")

        def prepare() -> str:
            if self.codec is None:
                self.codec = JournalCodec(today=self.today)
            return operation(self.codec.document(self.source))

        return await self._commit(await asyncio.to_thread(prepare))

    @staticmethod
    def _transaction(document: models.File, identifier: str) -> models.Transaction:
        matches = [
            entry
            for entry in document.raw_directives
            if isinstance(entry, models.Transaction) and entry.meta.get("id") == identifier
        ]
        if len(matches) != 1:
            raise LedgerError("crud_transaction_readonly")
        return matches[0]

    @staticmethod
    def _declaration(document: models.File, identifier: str) -> models.Open:
        matches = [
            entry
            for entry in document.raw_directives
            if isinstance(entry, models.Open) and entry.account == identifier
        ]
        if len(matches) != 1:
            raise LedgerError("crud_missing")
        return matches[0]

    def expense_for_edit(self, identifier: str) -> Transaction:
        transaction = self.transaction_for_edit(identifier)
        if transaction.kind != "expense":
            raise LedgerError("crud_transaction_readonly")
        return transaction

    def transaction_for_edit(self, identifier: str) -> Transaction:
        self._require_ready()
        transaction = next((t for t in self.ledger.transactions if t.id == identifier), None)
        accounts = {a.id for a in self.ledger.accounts}
        categories = {c.id for c in self.ledger.categories}
        if transaction is None or not transaction.persistent_id:
            raise LedgerError("crud_transaction_readonly")
        if transaction.kind == "transfer":
            self.transfer_details(transaction)
            return transaction
        if (
            transaction.kind not in {"expense", "income", "reimbursement", "adjustment"}
            or len(transaction.postings) != 2
            or any(p.total_price is not None for p in transaction.postings)
        ):
            raise LedgerError("crud_transaction_readonly")
        direction = -1 if transaction.kind == "expense" else 1
        asset = [
            p for p in transaction.postings if p.account in accounts and p.amount * direction > ZERO
        ]
        root = "Income:" if transaction.kind == "income" else "Expenses:"
        expense = [
            p
            for p in transaction.postings
            if (
                p.account == ADJUSTMENTS
                if transaction.kind == "adjustment"
                else p.account in categories and p.account.startswith(root)
            )
            and p.amount * direction < ZERO
        ]
        if len(asset) != 1 or len(expense) != 1:
            raise LedgerError("crud_transaction_readonly")
        return transaction

    def transfer_details(self, transaction: Transaction) -> TransferDetails:
        return self.ledger.transfer_details(transaction)

    async def edit_expense(
        self,
        identifier: str,
        account: str,
        category: str,
        amount: str,
        payee: str,
        occurred: str,
        timestamp: str,
        description: str,
        expected_source: str,
    ) -> str | None:
        self.expense_for_edit(identifier)
        return await self.edit_transaction(
            identifier,
            account,
            category,
            amount,
            payee,
            occurred,
            timestamp,
            description,
            expected_source,
        )

    async def edit_transaction(
        self,
        identifier: str,
        account: str,
        category: str,
        amount: str,
        payee: str,
        occurred: str,
        timestamp: str,
        description: str,
        expected_source: str,
    ) -> str | None:
        original = self.transaction_for_edit(identifier)
        try:
            day = date.fromisoformat(occurred)
            stamp = datetime.fromisoformat(timestamp)
        except ValueError as exc:
            raise LedgerError("crud_datetime") from exc
        if stamp.utcoffset() is None:
            raise LedgerError("crud_datetime")
        description = description_text(description)
        ledger = replace(
            self.ledger,
            transactions=tuple(t for t in self.ledger.transactions if t.id != identifier),
        )
        updated = (
            ledger.expense(
                account, category, money(amount), payee, day, stamp, identifier, today=self.today()
            )
            if original.kind == "expense"
            else ledger.incoming(
                account,
                category,
                money(amount),
                payee,
                day,
                stamp,
                identifier,
                original.kind,
                today=self.today(),
            )
        )

        def edit(document: models.File) -> str:
            entry = self._transaction(document, original.id)
            original_asset = next(
                p.account
                for p in original.postings
                if p.account in {a.id for a in self.ledger.accounts}
            )

            def update() -> None:
                entry.date = updated.date
                entry.payee = updated.payee or None
                entry.narration = description
                if timestamp != original.timestamp:
                    entry.meta["timestamp"] = updated.timestamp
                for posting in entry.postings:
                    if not isinstance(posting, models.Posting):
                        continue
                    asset = posting.account == original_asset
                    posting.account = account if asset else category
                    direction = -1 if original.kind == "expense" else 1
                    posting.number = money(amount) * (direction if asset else -direction)
                    posting.currency = updated.postings[0].currency

            return replace_directive(self.source, entry, update)

        return await self._change(edit, expected_source)

    async def delete_expense(self, identifier: str, expected_source: str) -> str | None:
        self.expense_for_edit(identifier)
        return await self.delete_transaction(identifier, expected_source)

    async def delete_transaction(self, identifier: str, expected_source: str) -> str | None:
        self.transaction_for_edit(identifier)
        return await self._change(
            lambda document: replace_directive(
                self.source, self._transaction(document, identifier), None
            ),
            expected_source,
        )

    async def set_operating_currencies(self, currencies: str, expected_source: str) -> str | None:
        values = tuple(dict.fromkeys(currency_code(c) for c in currencies.split()))
        if not values:
            raise LedgerError("crud_currencies")

        def edit(document: models.File) -> str:
            entries = [
                entry
                for entry in document.raw_directives
                if isinstance(entry, models.Option) and entry.key == "operating_currency"
            ]
            source = self.source
            # Work backwards so original token positions remain valid.
            for index in range(len(entries) - 1, -1, -1):
                entry = entries[index]
                if index < len(values):

                    def update(entry=entry, value=values[index]) -> None:
                        entry.value = value

                    source = replace_directive(source, entry, update)
                else:
                    source = replace_directive(source, entry, None)
            for value in values[len(entries) :]:
                source = append(
                    source, render(models.Option.from_value("operating_currency", value))
                )
            return source

        return await self._change(edit, expected_source)

    def opening_balance(self, identifier: str) -> Decimal:
        openings = [
            t
            for t in self.ledger.transactions
            if t.kind == "opening" and any(p.account == identifier for p in t.postings)
        ]
        if len(openings) > 1:
            raise LedgerError("crud_opening_complex")
        if not openings:
            return ZERO
        transaction = openings[0]
        if (
            not transaction.persistent_id
            or len(transaction.postings) != 2
            or {p.account for p in transaction.postings}
            != {
                identifier,
                EQUITY,
            }
        ):
            raise LedgerError("crud_opening_complex")
        amount = next(p.amount for p in transaction.postings if p.account == identifier)
        account = next(a for a in self.ledger.accounts if a.id == identifier)
        return -amount if account.liability else amount

    async def edit_account(
        self, identifier: str, name: str, opening: str | None, active: bool, expected_source: str
    ) -> str | None:
        account = next((a for a in self.ledger.accounts if a.id == identifier), None)
        if account is None:
            raise LedgerError("crud_missing")
        label = display_name(name)
        balance = None if opening is None else money(opening)
        old_balance = None if opening is None else self.opening_balance(identifier)
        if balance is not None and account.liability:
            balance = -balance
            old_balance = -old_balance if old_balance is not None else None

        def edit(document: models.File) -> str:
            entry = self._declaration(document, identifier)

            def update() -> None:
                entry.meta["display-name"] = label
                entry.meta["txncount-active"] = active

            source = replace_directive(self.source, entry, update)
            if balance is None or balance == old_balance:
                return source
            # Reparse after the first splice; token positions may have changed.
            assert self.codec is not None
            document = self.codec.document(source)
            opening_transaction = next(
                (
                    t
                    for t in self.ledger.transactions
                    if t.kind == "opening" and any(p.account == identifier for p in t.postings)
                ),
                None,
            )
            if opening_transaction is not None:
                transaction = self._transaction(document, opening_transaction.id)
                opening_amount = balance

                def update_opening() -> None:
                    for posting in transaction.postings:
                        if not isinstance(posting, models.Posting):
                            continue
                        posting.number = (
                            opening_amount if posting.account == identifier else -opening_amount
                        )

                return replace_directive(source, transaction, update_opening)
            if self.ledger.equity_opened is None:
                source = append(source, equity_declaration(account.opened))
            return append(
                source,
                transaction_text(
                    Transaction(
                        str(uuid4()),
                        account.opened,
                        datetime.now().astimezone().isoformat(),
                        "",
                        "Opening balance",
                        (
                            Posting(identifier, balance, account.currency),
                            Posting(EQUITY, -balance, account.currency),
                        ),
                        "opening",
                    )
                ),
            )

        return await self._change(edit, expected_source)

    async def edit_category(
        self, identifier: str, name: str, active: bool, expected_source: str
    ) -> str | None:
        label = display_name(name)

        def edit(document: models.File) -> str:
            entry = self._declaration(document, identifier)
            if not identifier.startswith(("Expenses:", "Income:")):
                raise LedgerError("crud_missing")

            def update() -> None:
                entry.meta["display-name"] = label
                entry.meta["txncount-active"] = active

            return replace_directive(self.source, entry, update)

        return await self._change(edit, expected_source)

    async def delete_account(self, identifier: str, expected_source: str) -> str | None:
        return await self._delete_declaration(identifier, expected_source, category=False)

    async def set_default_fee_category(self, identifier: str, expected_source: str) -> str | None:
        implicit = identifier == BANK_FEES and not any(
            c.id == BANK_FEES for c in self.ledger.categories
        )
        if not implicit and (
            not identifier.startswith("Expenses:") or not self.ledger.category_active(identifier)
        ):
            raise LedgerError("incoming_category")

        def edit(document: models.File) -> str:
            source = self.source
            previous = [
                c.id
                for c in self.ledger.categories
                if self._declaration(document, c.id).meta.get(
                    "txncount-default-transfer-fee", False
                )
            ]
            paths: list[str] = previous + ([identifier] if not implicit else [])
            for path in dict.fromkeys(paths):
                assert self.codec is not None
                entry = self._declaration(self.codec.document(source), path)

                def update(entry: models.Open = entry, path: str = path) -> None:
                    entry.meta["txncount-default-transfer-fee"] = path == identifier

                source = replace_directive(source, entry, update)
            return source

        return await self._change(edit, expected_source)

    async def delete_category(self, identifier: str, expected_source: str) -> str | None:
        return await self._delete_declaration(identifier, expected_source, category=True)

    async def _delete_declaration(
        self, identifier: str, expected_source: str, *, category: bool
    ) -> str | None:
        allowed = self.ledger.categories if category else self.ledger.accounts
        if identifier not in {item.id for item in allowed}:
            raise LedgerError("crud_missing")
        if category and any(c.id.startswith(identifier + ":") for c in self.ledger.categories):
            raise LedgerError("crud_children")
        # Even an opening entry is history: use deactivation rather than discarding it.
        if any(p.account == identifier for t in self.ledger.transactions for p in t.postings):
            raise LedgerError("crud_has_history")

        def edit(document: models.File) -> str:
            entry = self._declaration(document, identifier)
            for other in document.raw_directives:
                if other is not entry and any(
                    isinstance(token, (models.Account, models.EscapedString))
                    and token.value == identifier
                    for token in other.tokens
                ):
                    raise LedgerError("crud_referenced")
            return replace_directive(self.source, entry, None)

        return await self._change(edit, expected_source)

    async def create_account(
        self, identifier: str, name: str, currency: str, opening: str = "", *, root: str = "Assets"
    ) -> str | None:
        self._require_ready()
        if root not in {"Assets", "Liabilities"}:
            raise LedgerError("transfer_accounts")
        account = Account(
            account_path(identifier, root), display_name(name), currency_code(currency)
        )
        if account.id in {a.id for a in self.ledger.accounts}:
            raise LedgerError("That technical account ID already exists.")
        balance = money(opening.strip() or "0")
        if account.liability:
            balance = -balance
        text = open_account(account)
        if account.currency not in self.ledger.operating_currencies:
            text = (
                render(models.Option.from_value("operating_currency", account.currency))
                + "\n\n"
                + text
            )
        if balance != ZERO:
            if self.ledger.equity_opened is None:
                text += "\n" + equity_declaration(EPOCH)
            text += "\n" + transaction_text(
                Transaction(
                    str(uuid4()),
                    EPOCH,
                    datetime.now().astimezone().isoformat(),
                    "",
                    "Opening balance",
                    (
                        Posting(account.id, balance, account.currency),
                        Posting(EQUITY, -balance, account.currency),
                    ),
                    "opening",
                )
            )
        return await self._save(text)

    async def create_category(
        self, identifier: str, name: str, *, root: str = "Expenses"
    ) -> str | None:
        self._require_ready()
        if root not in {"Expenses", "Income"}:
            raise LedgerError("incoming_category")
        path = account_path(identifier, root)
        existing = {c.id for c in self.ledger.categories}
        if path in existing:
            raise LedgerError("That category already exists.")
        label = display_name(name)
        parts = path.split(":")
        additions = []
        for length in range(2, len(parts) + 1):
            current = ":".join(parts[:length])
            if current not in existing:
                additions.append(
                    open_category(
                        Category(current, label if current == path else parts[length - 1])
                    )
                )
        return await self._save("\n".join(additions))

    async def add_incoming(
        self,
        account: str,
        category: str,
        amount: str,
        payee: str,
        occurred: str,
        kind: str,
        description: str = "",
        timestamp: str | None = None,
        related_expense: str | None = None,
        expected_source: str | None = None,
    ) -> str | None:
        self._require_ready()
        if expected_source is not None and expected_source != self.source:
            raise ConflictError("crud_stale")
        try:
            day = date.fromisoformat(occurred)
            stamp = (
                datetime.now().astimezone()
                if timestamp is None
                else datetime.fromisoformat(timestamp)
            )
        except ValueError as exc:
            raise LedgerError("crud_datetime") from exc
        if related_expense is not None:
            if kind != "reimbursement":
                raise LedgerError("incoming_reference")
            original = self.expense_for_edit(related_expense)
            original_category = next(
                p for p in original.postings if p.account.startswith("Expenses:")
            )
            receiving = next((a for a in self.ledger.accounts if a.id == account), None)
            if (
                original_category.account != category
                or receiving is None
                or original_category.currency != receiving.currency
                or day < original.date
            ):
                raise LedgerError("incoming_reference")
        ledger = self.ledger
        addition = ""
        if kind == "adjustment" and ledger.adjustments_opened is None:
            ledger = replace(ledger, adjustments_opened=EPOCH)
            addition = render(models.Open.from_value(EPOCH, ADJUSTMENTS, [])) + "\n"
        transaction = ledger.incoming(
            account,
            category,
            money(amount),
            payee,
            day,
            stamp,
            str(uuid4()),
            kind,
            today=self.today(),
        )
        transaction = replace(
            transaction, narration=description_text(description), related_expense=related_expense
        )
        return await self._save(addition + transaction_text(transaction))

    async def save_transfer(
        self,
        source_account: str,
        destination: str,
        amount: str,
        fee: str,
        fee_category: str,
        fee_mode: str,
        occurred: str,
        timestamp: str,
        description: str,
        payee: str,
        expected_source: str,
        *,
        identifier: str | None = None,
        default_fee_name: str = "Bank fees",
        destination_amount: str | None = None,
        fee_currency: str | None = None,
    ) -> str | None:
        self._require_ready()
        if expected_source != self.source:
            raise ConflictError("crud_stale")
        original = self.transaction_for_edit(identifier) if identifier else None
        if original and original.kind != "transfer":
            raise LedgerError("crud_transaction_readonly")
        try:
            day, stamp = date.fromisoformat(occurred), datetime.fromisoformat(timestamp)
        except ValueError as exc:
            raise LedgerError("crud_datetime") from exc
        fee_amount = money(fee or "0")
        ledger = replace(
            self.ledger,
            transactions=tuple(t for t in self.ledger.transactions if t.id != identifier),
        )
        addition = ""
        if (
            fee_amount
            and fee_category == BANK_FEES
            and not any(c.id == BANK_FEES for c in ledger.categories)
        ):
            category = Category(BANK_FEES, display_name(default_fee_name))
            ledger = replace(ledger, categories=(*ledger.categories, category))
            addition = open_category(category)
        updated = ledger.transfer(
            source_account,
            destination,
            money(amount),
            fee_amount,
            fee_category,
            fee_mode,
            day,
            stamp,
            identifier or str(uuid4()),
            today=self.today(),
            destination_amount=money(destination_amount)
            if destination_amount is not None
            else None,
            fee_currency=fee_currency,
        )
        updated = replace(
            updated,
            narration=description_text(description),
            payee=display_name(payee) if payee.strip() else "",
        )
        if original is None:
            return await self._save(addition + "\n" + transaction_text(updated))

        def edit(document: models.File) -> str:
            entry = self._transaction(document, original.id)
            details = self.transfer_details(original)
            old_postings = [p for p in entry.postings if isinstance(p, models.Posting)]
            old_source = next(
                p
                for p in old_postings
                if p.account == details.source.account
                and p.number == details.source.amount
                and (not details.exchange or p.price is not None)
            )
            fees = [p for p in old_postings if p.account.startswith("Expenses:")]
            extra = [
                p
                for p in old_postings
                if p.account == details.source.account and p is not old_source
            ]
            roles: list[models.Posting | None] = [
                old_source,
                next(p for p in old_postings if p.account == details.target.account),
                fees[0] if fees else None,
                extra[0] if extra else None,
            ]

            def update() -> None:
                entry.date = updated.date
                entry.payee = updated.payee or None
                entry.narration = updated.narration
                if timestamp != original.timestamp:
                    entry.meta["timestamp"] = updated.timestamp
                entry.meta["txncount-fee-mode"] = fee_mode
                exchange = any(p.total_price is not None for p in updated.postings)
                for index, posting in enumerate(updated.postings):
                    number = posting.amount.quantize(Decimal(".01")) if exchange else posting.amount
                    node = roles[index]
                    if node is not None:
                        node.account, node.number, node.currency = (
                            posting.account,
                            number,
                            posting.currency,
                        )
                        node.price = (
                            models.TotalPrice.from_value(
                                posting.total_price.quantize(Decimal(".01")), posting.price_currency
                            )
                            if posting.total_price is not None
                            else None
                        )
                    else:
                        entry.postings.append(
                            models.Posting.from_value(
                                posting.account,
                                number,
                                posting.currency,
                                price=models.TotalPrice.from_value(
                                    posting.total_price.quantize(Decimal(".01")),
                                    posting.price_currency,
                                )
                                if posting.total_price is not None
                                else None,
                            )
                        )
                for index, node in enumerate(roles[len(updated.postings) :], len(updated.postings)):
                    if node is None:
                        continue
                    if node.meta or node.inline_comment:
                        node.number = ZERO
                        node.currency = updated.postings[0].currency
                        node.price = None
                        if index == 3:
                            node.account = updated.postings[0].account
                    else:
                        entry.postings.remove(node)

            result = replace_directive(self.source, entry, update)
            return append(result, addition) if addition else result

        return await self._change(edit, expected_source)

    async def add_expense(
        self,
        account: str,
        category: str,
        amount: str,
        payee: str,
        occurred: str,
        description: str = "Expense",
        timestamp: str | None = None,
    ) -> str | None:
        self._require_ready()
        try:
            day = date.fromisoformat(occurred)
            stamp = (
                datetime.now().astimezone()
                if timestamp is None
                else datetime.fromisoformat(timestamp)
            )
        except ValueError as exc:
            raise LedgerError("Date must be YYYY-MM-DD.") from exc
        transaction = self.ledger.expense(
            account,
            category,
            money(amount),
            payee,
            day,
            stamp,
            str(uuid4()),
            today=self.today(),
        )
        description = description_text(description)
        return await self._save(transaction_text(replace(transaction, narration=description)))
