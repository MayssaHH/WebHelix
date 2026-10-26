"""Shared request/episode ledger and conservative reservations before paid calls."""

from decimal import Decimal, InvalidOperation
import time

from webhelix.evaluate import lock
from webhelix.v2.contract import read, save


class BudgetBlocked(RuntimeError):
    pass


def validate_plan(plan, models):
    """Validate both roles before a browser reset or generation request."""
    try:
        amount = Decimal(str(plan.get("authorized_usd")))
        if not amount.is_finite() or amount <= 0 or not plan.get("verified_on"):
            raise ValueError
        for role, model in models.items():
            spec = plan["roles"][role]
            if (
                spec["model"] != model
                or not spec["pricing_source"]
                or not spec["context_source"]
            ):
                raise ValueError
            for field in ("input_usd_per_million", "output_usd_per_million"):
                value = Decimal(str(spec[field]))
                if not value.is_finite() or value <= 0:
                    raise ValueError
            context = spec["maximum_context_input_tokens"]
            if type(context) is not int or context <= 0:
                raise ValueError
    except (InvalidOperation, ValueError, KeyError, TypeError) as exc:
        raise BudgetBlocked(
            "Paid calls require a positive authorized amount and verified pricing/context bounds "
            "for each exact model; fill spending-plan.example.json first"
        ) from exc


class Budget:
    def __init__(self, root, settings):
        self.root, self.settings = root, settings

    def _ledger(self):
        path = self.root / "budget.json"
        return (
            read(path)
            if path.exists()
            else {
                "episodes": [],
                "calls": [],
                "max_real_episodes": self.settings["max_real_episodes"],
            }
        )

    def _check_deadline(self):
        if time.time() >= self.settings.get("run_deadline_epoch", float("inf")):
            raise BudgetBlocked("Experiment wall-time budget exhausted")

    def episode(self, label):
        self._check_deadline()
        with lock(self.root / "budget.lock"):
            data = self._ledger()
            if len(data["episodes"]) >= self.settings["max_real_episodes"]:
                raise BudgetBlocked(
                    "Real episode limit reached; failed attempts also count"
                )
            data["episodes"].append({"label": label, "started": time.time()})
            save(self.root / "budget.json", data)

    def reserve(self, role, model, max_output):
        self._check_deadline()
        if role not in {"optimizer", "victim"}:
            raise BudgetBlocked("Unknown model role")
        plan = read(self.root / "spending-plan.json")
        validate_plan(plan, {role: model})
        spec = plan.get("roles", {}).get(role, {})
        inp, out = (
            Decimal(str(spec["input_usd_per_million"])),
            Decimal(str(spec["output_usd_per_million"])),
        )
        context = spec["maximum_context_input_tokens"]
        # Reserve the FULL verified model context, including images, not an
        # optimistic text token estimate. Cached input discounts are not assumed.
        worst = (context * inp + max_output * out) / Decimal(1000000)
        with lock(self.root / "budget.lock"):
            data = self._ledger()
            roles = [c for c in data["calls"] if c["role"] == role]
            maximum = (
                self.settings.get("max_optimizer_calls", 2)
                if role == "optimizer"
                else self.settings["max_real_episodes"] * self.settings["max_actions"]
            )
            if len(roles) >= maximum:
                raise BudgetBlocked(role + " request limit reached")
            charged = sum(Decimal(c["accounted_usd"]) for c in data["calls"])
            if charged + worst > Decimal(str(plan["authorized_usd"])):
                raise BudgetBlocked(
                    "Insufficient remaining budget for conservative request reservation"
                )
            item = {
                "id": len(data["calls"]),
                "role": role,
                "model": model,
                "reserved_usd": str(worst),
                "accounted_usd": str(worst),
                "maximum_input_tokens": context,
                "maximum_output_tokens": max_output,
                "status": "reserved",
                "started": time.time(),
            }
            data["calls"].append(item)
            save(self.root / "budget.json", data)
            return item["id"]

    def settle(self, number, usage, started=True):
        with lock(self.root / "budget.lock"):
            data = self._ledger()
            item = data["calls"][number]
            item["usage"] = usage
            plan = read(self.root / "spending-plan.json")["roles"][item["role"]]
            inp = usage.get("input_tokens", usage.get("prompt_tokens"))
            out = usage.get("output_tokens", usage.get("completion_tokens"))
            if not started:
                item.update(accounted_usd="0", status="not_sent")
            elif type(inp) is int and type(out) is int and min(inp, out) >= 0:
                amount = (
                    inp * Decimal(str(plan["input_usd_per_million"]))
                    + out * Decimal(str(plan["output_usd_per_million"]))
                ) / Decimal(1000000)
                item.update(accounted_usd=str(amount), status="usage_reported")
                if (
                    inp > item["maximum_input_tokens"]
                    or out > item["maximum_output_tokens"]
                ):
                    item["status"] = "verified_bound_violated"
            else:
                item["status"] = "usage_missing_full_reservation_retained"
            save(self.root / "budget.json", data)
            if item["status"] == "verified_bound_violated":
                raise BudgetBlocked(
                    "Provider exceeded the supplied verified bounds; stop and investigate billing"
                )
