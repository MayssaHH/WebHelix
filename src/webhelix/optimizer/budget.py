"""Explicit import of historical reservations; continuations share a single ledger."""

import hashlib
from pathlib import Path

from webhelix.evaluate import lock
from webhelix.v2.contract import read, save


def check_budget(directory):
    ledger = read(Path(directory) / "budget.json")
    if (
        ledger.get("maximum") != 30
        or not isinstance(ledger.get("reservations"), list)
        or len(ledger["reservations"]) > 30
        or [x.get("number") for x in ledger["reservations"]]
        != list(range(1, len(ledger["reservations"]) + 1))
    ):
        raise ValueError("Invalid reservation ledger")
    return ledger


def import_budget(source, directory):
    source, directory = Path(source).resolve(), Path(directory).resolve()
    ledger = check_budget(source.parent)
    if source.name != "budget.json":
        raise ValueError("Use the original budget.json artifact")
    directory.mkdir(parents=True, exist_ok=True)
    with lock(directory / "budget.lock"):
        if (directory / "budget.json").exists():
            raise ValueError("Shared ledger already exists; reuse it, do not reset it")
        save(
            directory / "budget-import.json",
            {
                "source": str(source),
                "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "imported_reservations": len(ledger["reservations"]),
                "instruction": "All continuation runs must use this directory; do not fork the ledger.",
            },
        )
        save(directory / "budget.json", ledger)
