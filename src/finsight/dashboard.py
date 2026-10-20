import logging

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from finsight.analytics.overview import (
    get_analytics_overview,
    get_transactions_over_time,
)
from finsight.database.connection import SessionLocal
from finsight.database.models import DataQualityIssue, ImportBatch

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

st.set_page_config(
    page_title="FinSight | Financial Intelligence",
    page_icon="📊",
    layout="wide",
)

st.title("FinSight")
st.caption("Financial Data Intelligence Platform")

if st.sidebar.button("Refresh dashboard"):
    st.rerun()

interval = st.sidebar.selectbox(
    "Analysis interval",
    options=["day", "month"],
    format_func=lambda value: "Daily" if value == "day" else "Monthly",
)

try:
    overview = get_analytics_overview()
    time_series = get_transactions_over_time(interval)

    with SessionLocal() as session:
        recent_imports = session.scalars(
            select(ImportBatch).order_by(ImportBatch.started_at.desc()).limit(10)
        ).all()

        issue_counts = session.execute(
            select(
                DataQualityIssue.issue_code,
                func.count(DataQualityIssue.id),
            )
            .group_by(DataQualityIssue.issue_code)
            .order_by(func.count(DataQualityIssue.id).desc())
        ).all()

        recent_issues = session.execute(
            select(
                DataQualityIssue.row_number,
                DataQualityIssue.issue_code,
                DataQualityIssue.message,
                ImportBatch.filename,
            )
            .join(
                ImportBatch,
                DataQualityIssue.import_batch_id == ImportBatch.id,
            )
            .order_by(DataQualityIssue.created_at.desc())
            .limit(20)
        ).all()

        total_imports = (
            session.scalar(select(func.count()).select_from(ImportBatch)) or 0
        )

        total_issues = (
            session.scalar(select(func.count()).select_from(DataQualityIssue)) or 0
        )

        failed_imports = (
            session.scalar(
                select(func.count())
                .select_from(ImportBatch)
                .where(ImportBatch.status == "FAILED")
            )
            or 0
        )

except SQLAlchemyError:
    logger.exception("Dashboard data retrieval failed.")
    st.error(
        "FinSight could not retrieve dashboard data. "
        "Check the database connection and try again."
    )
    st.stop()

st.header("Financial overview")

col1, col2, col3 = st.columns(3)

col1.metric(
    "Total transactions",
    f"{overview['total_transactions']:,}",
)

col2.metric(
    "Unique customers",
    f"{overview['unique_customers']:,}",
)

col3.metric(
    "Total import batches",
    f"{total_imports:,}",
)

st.subheader("Transaction amounts by currency")

amounts = overview["total_amount_by_currency"]

if amounts:
    currency_columns = st.columns(min(len(amounts), 4))

    for index, (currency, amount) in enumerate(sorted(amounts.items())):
        currency_columns[index % len(currency_columns)].metric(
            f"Total amount ({currency})",
            f"{currency} {float(amount):,.2f}",
        )
else:
    st.info("No financial transactions have been imported yet.")

date_range = overview["date_range"]

if date_range["earliest"] and date_range["latest"]:
    st.caption(
        f"Transaction date range: {date_range['earliest']} to {date_range['latest']}"
    )

st.divider()

st.header("Transaction activity")

activity = time_series["data"]

if activity:
    activity_df = pd.DataFrame(activity)
    activity_df["date"] = pd.to_datetime(activity_df["date"])

    chart_col1, chart_col2 = st.columns(2)

    with chart_col1:
        st.subheader("Transaction volume")

        fig = px.bar(
            activity_df,
            x="date",
            y="transaction_count",
            title="Number of transactions",
            labels={
                "date": "Period",
                "transaction_count": "Transactions",
            },
        )
        st.plotly_chart(fig, use_container_width=True)

    with chart_col2:
        st.subheader("Transaction types")

        type_df = pd.DataFrame(
            [
                {"Transaction type": transaction_type, "Count": count}
                for transaction_type, count in overview[
                    "transaction_counts_by_type"
                ].items()
            ]
        )

        fig = px.pie(
            type_df,
            names="Transaction type",
            values="Count",
            title="Distribution by transaction type",
            hole=0.4,
        )
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("Transaction amounts over time")

    currency_codes = sorted(
        {currency for period in activity for currency in period["amount_by_currency"]}
    )

    if currency_codes:
        selected_currency = st.selectbox(
            "Currency",
            options=currency_codes,
        )

        amount_data = [
            {
                "date": pd.to_datetime(period["date"]),
                "amount": float(
                    period["amount_by_currency"].get(selected_currency, "0.00")
                ),
            }
            for period in activity
        ]

        amount_df = pd.DataFrame(amount_data)

        fig = px.line(
            amount_df,
            x="date",
            y="amount",
            markers=True,
            title=f"Transaction amounts ({selected_currency})",
            labels={
                "date": "Period",
                "amount": f"Amount ({selected_currency})",
            },
        )

        st.plotly_chart(fig, use_container_width=True)

    with st.expander("View transaction activity data"):
        st.dataframe(activity_df, use_container_width=True)

else:
    st.info("No transaction activity is available for the selected interval.")

st.divider()

st.header("Import monitoring")

import_col1, import_col2, import_col3 = st.columns(3)

import_col1.metric("Total imports", f"{total_imports:,}")
import_col2.metric("Failed imports", f"{failed_imports:,}")
import_col3.metric("Data-quality issues", f"{total_issues:,}")

if recent_imports:
    import_data = [
        {
            "Filename": batch.filename,
            "Status": batch.status.value,
            "Total rows": batch.total_rows,
            "Accepted": batch.accepted_rows,
            "Rejected": batch.rejected_rows,
            "Duplicates": batch.duplicate_rows,
            "Started": batch.started_at,
        }
        for batch in recent_imports
    ]

    st.dataframe(
        pd.DataFrame(import_data),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No CSV imports have been recorded yet.")

st.divider()

st.header("Data quality")

if issue_counts:
    issue_df = pd.DataFrame(
        [
            {"Issue type": issue_code, "Occurrences": count}
            for issue_code, count in issue_counts
        ]
    )

    col1, col2 = st.columns([1, 2])

    with col1:
        st.subheader("Issues by type")

        fig = px.bar(
            issue_df,
            x="Occurrences",
            y="Issue type",
            orientation="h",
            labels={"Occurrences": "Occurrences"},
        )
        st.plotly_chart(fig, use_container_width=True)

    with col2:
        st.subheader("Recent quality issues")

        issue_data = [
            {
                "File": filename,
                "Row": row_number,
                "Issue": issue_code,
                "Details": message,
            }
            for row_number, issue_code, message, filename in recent_issues
        ]

        st.dataframe(
            pd.DataFrame(issue_data),
            use_container_width=True,
            hide_index=True,
        )
else:
    st.success("No data-quality issues have been recorded.")

st.caption("FinSight | Financial Data Intelligence")
