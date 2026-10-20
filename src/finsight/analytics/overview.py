from decimal import Decimal

from sqlalchemy import Date, cast, distinct, func, select

from finsight.database.connection import SessionLocal
from finsight.database.models import FinancialTransaction, TransactionType


def get_analytics_overview() -> dict:
    """Return aggregate transaction metrics, keeping currencies separate."""
    with SessionLocal() as session:
        total_transactions = (
            session.scalar(select(func.count()).select_from(FinancialTransaction)) or 0
        )

        unique_customers = (
            session.scalar(
                select(func.count(distinct(FinancialTransaction.customer_id)))
            )
            or 0
        )

        amount_rows = session.execute(
            select(
                FinancialTransaction.currency,
                func.sum(FinancialTransaction.amount),
            ).group_by(FinancialTransaction.currency)
        ).all()

        count_rows = session.execute(
            select(
                FinancialTransaction.transaction_type,
                func.count(),
            ).group_by(FinancialTransaction.transaction_type)
        ).all()

        earliest_transaction = session.scalar(
            select(func.min(FinancialTransaction.transaction_date))
        )

        latest_transaction = session.scalar(
            select(func.max(FinancialTransaction.transaction_date))
        )

        amounts_by_currency = {
            currency: format(Decimal(amount), ".2f") for currency, amount in amount_rows
        }

        counts_by_type = {
            transaction_type.value: count for transaction_type, count in count_rows
        }

        # Include all defined types, even when their count is zero.
        for transaction_type in TransactionType:
            counts_by_type.setdefault(transaction_type.value, 0)

        return {
            "total_transactions": total_transactions,
            "unique_customers": unique_customers,
            "total_amount_by_currency": amounts_by_currency,
            "transaction_counts_by_type": counts_by_type,
            "date_range": {
                "earliest": (
                    earliest_transaction.isoformat()
                    if earliest_transaction is not None
                    else None
                ),
                "latest": (
                    latest_transaction.isoformat()
                    if latest_transaction is not None
                    else None
                ),
            },
        }


def get_transactions_over_time(interval: str = "day") -> dict:
    """Return transaction counts and amounts grouped by day or month."""

    if interval not in {"day", "month"}:
        raise ValueError("Interval must be 'day' or 'month'.")

    with SessionLocal() as session:
        if interval == "day":
            period_expression = cast(FinancialTransaction.transaction_date, Date)
        else:
            period_expression = func.date_trunc(
                "month", FinancialTransaction.transaction_date
            )

        count_rows = session.execute(
            select(
                period_expression.label("period"),
                func.count().label("transaction_count"),
            )
            .group_by(period_expression)
            .order_by(period_expression)
        ).all()

        amount_rows = session.execute(
            select(
                period_expression.label("period"),
                FinancialTransaction.currency,
                func.sum(FinancialTransaction.amount).label("total_amount"),
            )
            .group_by(period_expression, FinancialTransaction.currency)
            .order_by(period_expression, FinancialTransaction.currency)
        ).all()

        type_rows = session.execute(
            select(
                period_expression.label("period"),
                FinancialTransaction.transaction_type,
                func.count().label("transaction_count"),
            )
            .group_by(period_expression, FinancialTransaction.transaction_type)
            .order_by(period_expression)
        ).all()

        periods = {
            row.period: {
                "date": (
                    row.period.isoformat()
                    if hasattr(row.period, "isoformat")
                    else str(row.period)
                ),
                "transaction_count": row.transaction_count,
                "amount_by_currency": {},
                "transaction_counts_by_type": {
                    transaction_type.value: 0 for transaction_type in TransactionType
                },
            }
            for row in count_rows
        }

        for row in amount_rows:
            periods[row.period]["amount_by_currency"][row.currency] = format(
                Decimal(row.total_amount), ".2f"
            )

        for row in type_rows:
            periods[row.period]["transaction_counts_by_type"][
                row.transaction_type.value
            ] = row.transaction_count

        return {
            "interval": interval,
            "data": list(periods.values()),
        }
