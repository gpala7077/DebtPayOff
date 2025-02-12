import streamlit as st
import pandas as pd
import numpy as np
import altair as alt
from fpdf import FPDF
from io import BytesIO
import json
import os

# ---- Streamlit Authenticator ----
import streamlit_authenticator as stauth

########################################
# Helper Functions for Multi-User Data
########################################

DATA_FILE = "user_data.json"

def load_user_data():
    """
    Load user data from JSON file into a Python dictionary.
    Returns an empty dict if file does not exist or is invalid.
    """
    if not os.path.isfile(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r") as f:
            data = json.load(f)
        return data
    except:
        return {}

def save_user_data(user_data):
    """
    Save the entire user data dictionary to the JSON file.
    """
    with open(DATA_FILE, "w") as f:
        json.dump(user_data, f, indent=4)

def initialize_user_session(username):
    """
    Load user-specific data into st.session_state once they're authenticated.
    """
    all_users_data = load_user_data()

    # If user not in file, initialize with defaults
    if username not in all_users_data:
        all_users_data[username] = {
            "cards": [],
            "monthly_income": 0.0,
            "fixed_expenses": 0.0,
            "variable_expenses": 0.0,
            "lump_sums": [],
            "custom_order": []
        }
        save_user_data(all_users_data)

    # Load user data into session
    user_dict = all_users_data[username]
    st.session_state["username"] = username
    st.session_state["cards"] = user_dict.get("cards", [])
    st.session_state["monthly_income"] = user_dict.get("monthly_income", 0.0)
    st.session_state["fixed_expenses"] = user_dict.get("fixed_expenses", 0.0)
    st.session_state["variable_expenses"] = user_dict.get("variable_expenses", 0.0)
    st.session_state["lump_sums"] = user_dict.get("lump_sums", [])
    st.session_state["custom_order"] = user_dict.get("custom_order", [])

def persist_user_session():
    """
    Save the current user's session data back to the JSON file.
    """
    if "username" not in st.session_state or not st.session_state["username"]:
        return

    all_users_data = load_user_data()
    username = st.session_state["username"]

    # Update user_data with current session data
    all_users_data[username] = {
        "cards": st.session_state["cards"],
        "monthly_income": st.session_state["monthly_income"],
        "fixed_expenses": st.session_state["fixed_expenses"],
        "variable_expenses": st.session_state["variable_expenses"],
        "lump_sums": st.session_state["lump_sums"],
        "custom_order": st.session_state["custom_order"],
    }
    save_user_data(all_users_data)

########################################
# Debt Payoff Logic
########################################

def compute_disposable_income(monthly_income, fixed_expenses, variable_expenses):
    return monthly_income - fixed_expenses - variable_expenses

def calculate_interest(balance, apr):
    monthly_interest_rate = apr / 100 / 12
    return balance * monthly_interest_rate

def apply_lump_sums(balance, lumpsum_list, current_month):
    lumpsum_applied = 0
    for lump in lumpsum_list:
        if lump["month"] == current_month:
            lumpsum_applied += lump["amount"]
    new_balance = max(0, balance - lumpsum_applied)
    return new_balance, lumpsum_applied

def get_next_card_index(cards, method):
    non_zero_cards = [(i, c) for i, c in enumerate(cards) if c["balance"] > 0]
    if not non_zero_cards:
        return None

    if method == "Avalanche":
        # Sort by APR desc
        non_zero_cards.sort(key=lambda x: x[1]["apr"], reverse=True)
    elif method == "Snowball":
        # Sort by balance asc
        non_zero_cards.sort(key=lambda x: x[1]["balance"])
    elif method == "Custom":
        # Sort by custom priority in st.session_state["custom_order"]
        order_map = {name: i for i, name in enumerate(st.session_state.get("custom_order", []))}
        non_zero_cards.sort(key=lambda x: order_map.get(x[1]["name"], 999999))
    else:
        # "Minimum" - no extra payment
        return None

    return non_zero_cards[0][0]

def simulate_debt_payoff(cards_data, monthly_budget, lumpsum_data, method, extra_payment=0.0):
    cards = []
    for c in cards_data:
        cards.append({
            "name": c["name"],
            "balance": c["balance"],
            "apr": c["apr"],
            "min_payment": c["min_payment"],
            "limit": c["limit"]
        })

    lumpsums = lumpsum_data[:]
    lumpsums.sort(key=lambda x: x["month"])

    records = []
    month = 1
    total_balance = sum([c["balance"] for c in cards])

    while total_balance > 0:
        row = {"Month": month}
        monthly_interest_paid = 0
        monthly_principal_paid = 0
        monthly_payments_breakdown = {}

        # Calculate monthly interest for each card
        for card in cards:
            if card["balance"] > 0:
                interest = calculate_interest(card["balance"], card["apr"])
                monthly_interest_paid += interest

        # Total min payments
        total_min_payments = sum(card["min_payment"] for card in cards if card["balance"] > 0)

        # If monthly_budget < total_min_payments, clamp it
        if monthly_budget < total_min_payments:
            total_min_payments = monthly_budget

        leftover_for_extra = max(
            0,
            monthly_budget - total_min_payments + (extra_payment if method != "Minimum" else 0.0)
        )

        # Pay each card's minimum
        for card in cards:
            if card["balance"] > 0:
                payment = min(card["balance"], card["min_payment"])
                card["balance"] -= payment
                monthly_principal_paid += payment
                monthly_payments_breakdown[card["name"]] = monthly_payments_breakdown.get(card["name"], 0) + payment

        # Apply leftover_for_extra
        card_index = get_next_card_index(cards, method)
        if card_index is not None and leftover_for_extra > 0:
            extra_pay = min(cards[card_index]["balance"], leftover_for_extra)
            cards[card_index]["balance"] -= extra_pay
            monthly_principal_paid += extra_pay
            monthly_payments_breakdown[cards[card_index]["name"]] += extra_pay

        # Apply monthly interest
        total_interest_for_this_month = 0
        for card in cards:
            if card["balance"] > 0:
                interest = calculate_interest(card["balance"], card["apr"])
                card["balance"] += interest
                total_interest_for_this_month += interest
        monthly_interest_paid = total_interest_for_this_month

        # Apply lumpsum if scheduled
        for card in cards:
            if card["balance"] > 0:
                new_balance, lumpsum_applied = apply_lump_sums(card["balance"], lumpsums, month)
                if lumpsum_applied > 0:
                    monthly_payments_breakdown[card["name"]] += lumpsum_applied
                card["balance"] = new_balance

        total_balance = sum([c["balance"] for c in cards if c["balance"] > 0])

        row["Total Balance"] = total_balance
        row["Interest Paid"] = monthly_interest_paid
        row["Principal Paid"] = monthly_principal_paid
        row["Monthly Breakdown"] = dict(monthly_payments_breakdown)

        records.append(row)
        month += 1
        if month > 600:  # safety limit
            break

    return pd.DataFrame(records)

def generate_summary(df):
    if df.empty:
        return {
            "Total Months": 0,
            "Total Interest Paid": 0.0,
        }
    total_interest = df["Interest Paid"].sum()
    total_months = df["Month"].iloc[-1]
    return {
        "Total Months": total_months,
        "Total Interest Paid": total_interest
    }

########################################
# PDF Export
########################################

class PDFReport(FPDF):
    pass

def create_pdf_report(df_list, method_list, summary_list):
    pdf = PDFReport()
    pdf.add_page()
    pdf.set_font("Arial", "B", 16)
    pdf.cell(190, 10, "Debt Payoff Report", ln=True, align="C")
    pdf.ln(5)

    # Summaries table
    pdf.set_font("Arial", "", 12)
    pdf.cell(190, 10, "Comparison Summary", ln=True)
    pdf.set_font("Arial", "", 10)

    col_width = 63
    pdf.cell(col_width, 10, "Method", border=1)
    pdf.cell(col_width, 10, "Total Months", border=1)
    pdf.cell(col_width, 10, "Total Interest", border=1)
    pdf.ln(10)

    for method, summ in zip(method_list, summary_list):
        pdf.cell(col_width, 10, method, border=1)
        pdf.cell(col_width, 10, str(round(summ["Total Months"], 2)), border=1)
        pdf.cell(col_width, 10, f"${round(summ['Total Interest Paid'], 2)}", border=1)
        pdf.ln(10)

    # Add each method’s detail
    for method_name, df in zip(method_list, df_list):
        pdf.add_page()
        pdf.set_font("Arial", "B", 14)
        pdf.cell(190, 10, f"{method_name} - Monthly Breakdown", ln=True, align="L")
        pdf.ln(5)
        pdf.set_font("Arial", "", 8)

        for _, row in df.iterrows():
            pdf.cell(
                190,
                5,
                f"Month: {int(row['Month'])}, "
                f"Balance: {round(row['Total Balance'], 2)}, "
                f"Interest: {round(row['Interest Paid'], 2)}, "
                f"Principal: {round(row['Principal Paid'], 2)}",
                ln=True,
            )

    return pdf

########################################
# Main App with Authentication
########################################

def main():
    st.title("Interactive Debt Payoff Calculator (Multi-User)")
    st.write("Analyze and compare various strategies to pay off credit card debt. Now with user authentication.")

    #
    # 1. Credentials with Pre-Hashed Passwords
    #
    # Below are example hashed passwords for "123" and "456" created offline.
    # Replace with your own hashed passwords as needed.
    hashed_pw_john = "$2b$12$ZgshxJF6/wQ6QEpyxi/LBOVpONnBoFAD8F8Xu6MRxkz2cHBWIl7xy"  # "123"
    hashed_pw_jane = "$2b$12$SBCf/BQNzm9Dp0gXk4OPYO/NvbfDh2TgehrGdDlNDmg1LomFaFaTS"  # "456"

    credentials = {
        "usernames": {
            "john": {
                "name": "John",
                "password": hashed_pw_john
            },
            "jane": {
                "name": "Jane",
                "password": hashed_pw_jane
            }
        }
    }

    authenticator = stauth.Authenticate(
        credentials,
        "my_app_cookie_name",  # Cookie name
        "my_signature_key",    # Signature key/secret (should be a secure random string)
        cookie_expiry_days=30
    )

    name, authentication_status, username = authenticator.login("Login", "main")

    if authentication_status is False:
        st.error("Username/password is incorrect")
        return
    elif authentication_status is None:
        st.warning("Please enter your username and password")
        return
    else:
        st.sidebar.write(f"Welcome, **{name}**")
        logout_button = authenticator.logout("Logout", "sidebar")

        if "username" not in st.session_state:
            initialize_user_session(username)

        # Once logged in, run the main debt app
        run_debt_app()

        # If user logs out, persist data, then clear session
        if logout_button:
            persist_user_session()
            st.session_state.clear()

def run_debt_app():
    """
    Same debt payoff logic as before, but using st.session_state for the logged-in user.
    """
    st.subheader("1. Credit Card Information")
    with st.expander("Add / Manage Credit Cards", expanded=True):
        c1, c2, c3, c4, c5 = st.columns([2, 2, 2, 2, 2])
        new_name = c1.text_input("Credit Card Name", "")
        new_balance = c2.number_input("Current Balance", 0.0, 1e9, 0.0, 100.0)
        new_apr = c3.number_input("APR (%)", 0.0, 100.0, 15.0, 0.1)
        new_min_payment = c4.number_input("Minimum Payment", 0.0, 1e9, 50.0, 10.0)
        new_limit = c5.number_input("Credit Limit", 0.0, 1e9, 5000.0, 100.0)

        if st.button("Add Credit Card"):
            if new_name.strip() == "":
                st.warning("Please provide a valid credit card name.")
            else:
                st.session_state["cards"].append({
                    "name": new_name,
                    "balance": new_balance,
                    "apr": new_apr,
                    "min_payment": new_min_payment,
                    "limit": new_limit
                })
                st.success(f"Added card: {new_name}")

        if st.session_state["cards"]:
            st.write("**Current Credit Cards**")
            for i, card in enumerate(st.session_state["cards"]):
                col1, col2, col3 = st.columns([4, 4, 1])
                col1.write(f"**{card['name']}** - Balance: ${card['balance']:.2f}, APR: {card['apr']}%")
                col2.write(f"Min Payment: ${card['min_payment']:.2f}, Limit: ${card['limit']:.2f}")
                if col3.button("Remove", key=f"rm_{i}"):
                    st.session_state["cards"].pop(i)
                    st.experimental_rerun()
        else:
            st.info("No credit cards added yet.")

    st.subheader("2. Monthly Budget")
    with st.expander("Enter Your Budget Details", expanded=True):
        st.session_state["monthly_income"] = st.number_input(
            "Monthly Income",
            0.0, 1e9, st.session_state["monthly_income"], 100.0
        )
        st.session_state["fixed_expenses"] = st.number_input(
            "Total Fixed Expenses",
            0.0, 1e9, st.session_state["fixed_expenses"], 50.0
        )
        st.session_state["variable_expenses"] = st.number_input(
            "Total Variable Expenses",
            0.0, 1e9, st.session_state["variable_expenses"], 50.0
        )

        disposable_income = compute_disposable_income(
            st.session_state["monthly_income"],
            st.session_state["fixed_expenses"],
            st.session_state["variable_expenses"]
        )
        st.write(f"**Disposable Income for Debt Repayment**: ${disposable_income:,.2f}")

    st.subheader("3. Lump Sum Payments")
    with st.expander("Schedule Lump Sum Payments", expanded=False):
        lump_col1, lump_col2, lump_col3 = st.columns([2, 2, 2])
        lump_month = lump_col1.number_input("Month Number (1-based)", 1, 360, 1, 1)
        lump_amount = lump_col2.number_input("Lump Sum Amount", 0.0, 1e9, 0.0, 100.0)
        lump_desc = lump_col3.text_input("Description", "")

        if st.button("Add Lump Sum"):
            if lump_amount <= 0:
                st.warning("Enter a valid lump sum amount.")
            else:
                st.session_state["lump_sums"].append({
                    "month": lump_month,
                    "amount": lump_amount,
                    "description": lump_desc
                })
                st.success(f"Lump sum of ${lump_amount} added at month {lump_month}.")

        if st.session_state["lump_sums"]:
            st.write("**Scheduled Lump Sums**")
            for i, ls in enumerate(st.session_state["lump_sums"]):
                lcol1, lcol2, lcol3 = st.columns([4, 4, 1])
                lcol1.write(f"Month: {ls['month']}, Amount: ${ls['amount']}")
                lcol2.write(f"Desc: {ls['description']}")
                if lcol3.button("Remove", key=f"rml_{i}"):
                    st.session_state["lump_sums"].pop(i)
                    st.experimental_rerun()
        else:
            st.info("No lump sum payments scheduled.")

    st.subheader("4. Debt Repayment Strategy & Parameters")
    strategy_options = ["Minimum", "Snowball", "Avalanche", "Custom"]
    chosen_strategy = st.selectbox("Choose your primary repayment strategy", strategy_options)

    extra_payment = st.number_input("Extra Monthly Payment (beyond total minimums)", 0.0, 1e9, 0.0, 10.0)

    if chosen_strategy == "Custom":
        st.write("**Set your own priority order for repayment**")
        all_card_names = [c["name"] for c in st.session_state["cards"]]
        st.session_state["custom_order"] = st.multiselect(
            "Select cards in the order you want them paid first",
            all_card_names,
            default=all_card_names
        )

    st.subheader("5. Simulation & Comparison")

    if st.button("Run Simulation"):
        if not st.session_state["cards"]:
            st.warning("Please add at least one credit card before running the simulation.")
        else:
            sim_methods = ["Minimum", "Snowball", "Avalanche", "Custom"]
            results = {}
            summaries = []

            disposable_income = compute_disposable_income(
                st.session_state["monthly_income"],
                st.session_state["fixed_expenses"],
                st.session_state["variable_expenses"]
            )

            for m in sim_methods:
                df_result = simulate_debt_payoff(
                    st.session_state["cards"],
                    disposable_income,
                    st.session_state["lump_sums"],
                    method=m,
                    extra_payment=extra_payment if m != "Minimum" else 0.0
                )
                results[m] = df_result
                summaries.append(generate_summary(df_result))

            st.write("### Comparison Summary")
            summary_df = pd.DataFrame(summaries, index=sim_methods)
            summary_df = summary_df[["Total Months", "Total Interest Paid"]]
            st.dataframe(summary_df.style.format({"Total Interest Paid": "{:,.2f}"}))

            colA, colB = st.columns(2)
            method_choice = colA.selectbox("Select Method to View Details", sim_methods)
            df_chosen = results[method_choice]

            if not df_chosen.empty:
                df_plot = df_chosen[["Month", "Total Balance", "Interest Paid", "Principal Paid"]].melt("Month")
                chart = alt.Chart(df_plot).mark_line(point=True).encode(
                    x="Month:Q",
                    y="value:Q",
                    color="variable:N",
                    tooltip=["Month", "variable", "value"]
                ).properties(
                    width=600,
                    height=400,
                    title=f"{method_choice} - Debt Balance & Payments Over Time"
                )
                colB.altair_chart(chart, use_container_width=True)

                st.write(f"#### {method_choice} - Last 6 Rows of Payment Schedule")
                st.dataframe(df_chosen.tail(6))

                csv_data = df_chosen.to_csv(index=False).encode("utf-8")
                st.download_button(
                    label=f"Download {method_choice} CSV",
                    data=csv_data,
                    file_name=f"{method_choice}_repayment_schedule.csv",
                    mime="text/csv"
                )
            else:
                st.info("No data for this method.")

            if st.button("Generate Consolidated PDF Report"):
                pdf = create_pdf_report(
                    [results[m] for m in sim_methods],
                    sim_methods,
                    summaries
                )
                pdf_buffer = BytesIO()
                pdf.output(pdf_buffer, "F")
                pdf_bytes = pdf_buffer.getvalue()
                st.download_button(
                    label="Download PDF",
                    data=pdf_bytes,
                    file_name="Debt_Payoff_Report.pdf",
                    mime="application/pdf"
                )
    else:
        st.info("Click 'Run Simulation' to calculate and compare repayment strategies.")

    # Persist user’s data if anything changed
    persist_user_session()

if __name__ == "__main__":
    main()
