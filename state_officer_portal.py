import streamlit as st
import pandas as pd
import bcrypt
from sqlalchemy import text
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
import os
from datetime import datetime

# ============================================================
# POSTGRES CONFIG
# ============================================================

# PG_HOST = "102.164.37.69"
# PG_PORT = 5432
# PG_DATABASE = "ben_db"
# PG_USER = "ben_user"
# PG_PASSWORD = "Olajumokepsgr@9@9"
# PG_SCHEMA = "ben"
#
# engine = create_engine(
#     f"postgresql+psycopg2://{PG_USER}:{PG_PASSWORD}@{PG_HOST}:{PG_PORT}/{PG_DATABASE}",
#     pool_pre_ping=True
# )
#
st.set_page_config(
    page_title="NCTO State Officer Portal",
    page_icon="🔐",
    layout="wide"
)

# PG_HOST = "102.164.37.69"
# PG_PORT = 5432
# PG_DATABASE = "your_database"
# PG_USER = "your_username"
# PG_PASSWORD = "9@9"   # example, keep your real password here

PG_HOST = "102.164.37.69"
PG_PORT = 5432
PG_DATABASE = "ben_db"
PG_USER = "ben_user"
PG_PASSWORD = "Olajumokepsgr#9#9"
PG_SCHEMA = "ben"

DATABASE_URL = URL.create(
    drivername="postgresql+psycopg2",
    username=PG_USER,
    password=PG_PASSWORD,
    host=PG_HOST,
    port=PG_PORT,
    database=PG_DATABASE,
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True
)

EXPORT_FOLDER = os.path.join(
    os.path.expanduser("~"),
    "Downloads",
    "NCTOExports"
)


# ============================================================
# AUTHENTICATION
# ============================================================

def verify_password(plain_password, password_hash):
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        password_hash.encode("utf-8")
    )


def authenticate_user(username, password):
    query = text("""
        SELECT username, password_hash, assigned_state
        FROM ben.state_officer_users
        WHERE lower(username) = lower(:username)
          AND is_active = true;
    """)

    with engine.connect() as conn:
        user = conn.execute(query, {"username": username}).mappings().first()

    if user and bcrypt.checkpw(
            password.encode("utf-8"),
            user["password_hash"].encode("utf-8")
    ):
        return {
            "username": user["username"],
            "state": user["assigned_state"]
        }

    return None


# ============================================================
# DATA QUERY
# ============================================================

@st.cache_data(ttl=600, show_spinner=False)
def load_state_summary(state_name):
    query = text("""
        WITH unified_payments AS
        (
            SELECT 
                'First Tranche' AS tranche,
                "State" AS state,
                "LGA" AS lga,
                "Ward" AS ward,
                "Community" AS community,
                nidhh,
                nid
            FROM ben."itblDistinctPaidBeneficiaries"

            UNION ALL

            SELECT 
                'Second Tranche' AS tranche,
                "State" AS state,
                "LGA" AS lga,
                "Ward" AS ward,
                "Community" AS community,
                nidhh,
                nid
            FROM ben."itblDistinctSecondTranche"

            UNION ALL

            SELECT 
                'Third Tranche' AS tranche,
                "State" AS state,
                "LGA" AS lga,
                "Ward" AS ward,
                "Community" AS community,
                nidhh,
                nid
            FROM ben."itblDistinctThirdTranche"
        )
        SELECT
            tranche,
            state,
            COUNT(DISTINCT nidhh) AS total_households,
            COUNT(DISTINCT nid) AS total_beneficiaries,
            COUNT(DISTINCT lga) AS total_lgas,
            COUNT(DISTINCT ward) AS total_wards,
            COUNT(DISTINCT community) AS total_communities
        FROM unified_payments
        WHERE lower(state) = lower(:state_name)
        GROUP BY tranche, state
        ORDER BY tranche;
    """)

    with engine.connect() as conn:
        return pd.read_sql(query, conn, params={"state_name": state_name})


@st.cache_data(ttl=600, show_spinner=False)
def load_state_data(state_name, tranche_filter, search_value, limit_rows):
    params = {
        "state_name": state_name,
        "limit_rows": limit_rows
    }

    tranche_condition = ""
    if tranche_filter != "All":
        tranche_condition = "AND tranche = :tranche_filter"
        params["tranche_filter"] = tranche_filter

    search_condition = ""
    if search_value:
        search_condition = """
            AND (
                nid ILIKE :search_value
                OR nidhh ILIKE :search_value
                OR "NIN" ILIKE :search_value
                OR "AccountNumber" ILIKE :search_value
                OR "Name" ILIKE :search_value
                OR "HouseholdID" ILIKE :search_value
            )
        """
        params["search_value"] = f"%{search_value}%"

    query = text(f"""
        WITH unified_payments AS
        (
            SELECT
                'First Tranche' AS tranche,
                CAST("State" AS TEXT) AS "State",
                CAST("LGA" AS TEXT) AS "LGA",
                CAST("Ward" AS TEXT) AS "Ward",
                CAST("Community" AS TEXT) AS "Community",
                CAST("HouseholdID" AS TEXT) AS "HouseholdID",
                CAST(nidhh AS TEXT) AS nidhh,
                CAST(nid AS TEXT) AS nid,
                CAST("Name" AS TEXT) AS "Name",
                CAST("TelephoneNo" AS TEXT) AS "TelephoneNo",
                CAST("Gender" AS TEXT) AS "Gender",
                CAST("Age" AS TEXT) AS "Age",
                CAST("AccountName" AS TEXT) AS "AccountName",
                CAST("AccountNumber" AS TEXT) AS "AccountNumber",
                CAST("BankName" AS TEXT) AS "BankName",
                CAST("AmountPaid" AS TEXT) AS "AmountPaid",
                CAST("PaymentStatus" AS TEXT) AS "PaymentStatus",
                CAST("PaymentDate" AS TEXT) AS "PaymentDate",
                CAST("NIN" AS TEXT) AS "NIN",
                CAST("NINBVN" AS TEXT) AS "NINBVN",
                CAST("IDType" AS TEXT) AS "IDType",
                CAST("TrancheStatus" AS TEXT) AS "TrancheStatus",
                CAST("TotalAmount" AS TEXT) AS "TotalAmount",
                CAST("AccountUsed" AS TEXT) AS "AccountUsed",
                CAST("Zone" AS TEXT) AS "Zone",
                CAST("ward_class" AS TEXT) AS "ward_class",
                CAST("EffectiveNIN" AS TEXT) AS "EffectiveNIN",
                CAST("NormalizedAccountNumber" AS TEXT) AS "NormalizedAccountNumber",
                CAST("NormalizedBankName" AS TEXT) AS "NormalizedBankName"
            FROM ben."itblDistinctPaidBeneficiaries"

            UNION ALL

            SELECT
                'Second Tranche' AS tranche,
                CAST("State" AS TEXT) AS "State",
                CAST("LGA" AS TEXT) AS "LGA",
                CAST("Ward" AS TEXT) AS "Ward",
                CAST("Community" AS TEXT) AS "Community",
                CAST("HouseholdID" AS TEXT) AS "HouseholdID",
                CAST(nidhh AS TEXT) AS nidhh,
                CAST(nid AS TEXT) AS nid,
                CAST("Name" AS TEXT) AS "Name",
                CAST("TelephoneNo" AS TEXT) AS "TelephoneNo",
                CAST("Gender" AS TEXT) AS "Gender",
                CAST("Age" AS TEXT) AS "Age",
                CAST("AccountName" AS TEXT) AS "AccountName",
                CAST("AccountNumber" AS TEXT) AS "AccountNumber",
                CAST("BankName" AS TEXT) AS "BankName",
                CAST("AmountPaid" AS TEXT) AS "AmountPaid",
                CAST("PaymentStatus" AS TEXT) AS "PaymentStatus",
                CAST("PaymentDate" AS TEXT) AS "PaymentDate",
                CAST("NIN" AS TEXT) AS "NIN",
                CAST("NINBVN" AS TEXT) AS "NINBVN",
                CAST("IDType" AS TEXT) AS "IDType",
                CAST("TrancheStatus" AS TEXT) AS "TrancheStatus",
                CAST("TotalAmount" AS TEXT) AS "TotalAmount",
                CAST("AccountUsed" AS TEXT) AS "AccountUsed",
                CAST("Zone" AS TEXT) AS "Zone",
                CAST("ward_class" AS TEXT) AS "ward_class",
                CAST("EffectiveNIN" AS TEXT) AS "EffectiveNIN",
                CAST("NormalizedAccountNumber" AS TEXT) AS "NormalizedAccountNumber",
                CAST("NormalizedBankName" AS TEXT) AS "NormalizedBankName"
            FROM ben."itblDistinctSecondTranche"

            UNION ALL

            SELECT
                'Third Tranche' AS tranche,
                CAST("State" AS TEXT) AS "State",
                CAST("LGA" AS TEXT) AS "LGA",
                CAST("Ward" AS TEXT) AS "Ward",
                CAST("Community" AS TEXT) AS "Community",
                CAST("HouseholdID" AS TEXT) AS "HouseholdID",
                CAST(nidhh AS TEXT) AS nidhh,
                CAST(nid AS TEXT) AS nid,
                CAST("Name" AS TEXT) AS "Name",
                CAST("TelephoneNo" AS TEXT) AS "TelephoneNo",
                CAST("Gender" AS TEXT) AS "Gender",
                CAST("Age" AS TEXT) AS "Age",
                CAST("AccountName" AS TEXT) AS "AccountName",
                CAST("AccountNumber" AS TEXT) AS "AccountNumber",
                CAST("BankName" AS TEXT) AS "BankName",
                CAST("AmountPaid" AS TEXT) AS "AmountPaid",
                CAST("PaymentStatus" AS TEXT) AS "PaymentStatus",
                CAST("PaymentDate" AS TEXT) AS "PaymentDate",
                CAST("NIN" AS TEXT) AS "NIN",
                CAST("NINBVN" AS TEXT) AS "NINBVN",
                CAST("IDType" AS TEXT) AS "IDType",
                CAST("TrancheStatus" AS TEXT) AS "TrancheStatus",
                CAST("TotalAmount" AS TEXT) AS "TotalAmount",
                CAST("AccountUsed" AS TEXT) AS "AccountUsed",
                CAST("Zone" AS TEXT) AS "Zone",
                CAST("ward_class" AS TEXT) AS "ward_class",
                CAST("EffectiveNIN" AS TEXT) AS "EffectiveNIN",
                CAST("NormalizedAccountNumber" AS TEXT) AS "NormalizedAccountNumber",
                CAST("NormalizedBankName" AS TEXT) AS "NormalizedBankName"
            FROM ben."itblDistinctThirdTranche"
        )
        SELECT *
        FROM unified_payments
        WHERE lower("State") = lower(:state_name)
        {tranche_condition}
        {search_condition}
        ORDER BY tranche, "LGA", "Ward", "Community"
        LIMIT :limit_rows;
    """)

    with engine.connect() as conn:
        return pd.read_sql(query, conn, params=params)


@st.cache_data(ttl=600, show_spinner=False)
def load_lga_summary(state_name):
    query = text("""
        WITH unified_payments AS
        (
            SELECT 'First Tranche' AS tranche, "State" AS state, "LGA" AS lga, nidhh, nid
            FROM ben."itblDistinctPaidBeneficiaries"

            UNION ALL

            SELECT 'Second Tranche' AS tranche, "State" AS state, "LGA" AS lga, nidhh, nid
            FROM ben."itblDistinctSecondTranche"

            UNION ALL

            SELECT 'Third Tranche' AS tranche, "State" AS state, "LGA" AS lga, nidhh, nid
            FROM ben."itblDistinctThirdTranche"
        )
        SELECT
            lga AS "LGA",
            COUNT(DISTINCT CASE WHEN tranche = 'First Tranche' THEN nidhh END) AS "First_Tranche_HHs",
            COUNT(DISTINCT CASE WHEN tranche = 'First Tranche' THEN nid END) AS "First_Tranche_Beneficiaries",

            COUNT(DISTINCT CASE WHEN tranche = 'Second Tranche' THEN nidhh END) AS "Second_Tranche_HHs",
            COUNT(DISTINCT CASE WHEN tranche = 'Second Tranche' THEN nid END) AS "Second_Tranche_Beneficiaries",

            COUNT(DISTINCT CASE WHEN tranche = 'Third Tranche' THEN nidhh END) AS "Third_Tranche_HHs",
            COUNT(DISTINCT CASE WHEN tranche = 'Third Tranche' THEN nid END) AS "Third_Tranche_Beneficiaries",

            COUNT(DISTINCT nidhh) AS "Total_Unique_HHs",
            COUNT(DISTINCT nid) AS "Total_Unique_Beneficiaries"
        FROM unified_payments
        WHERE lower(state) = lower(:state_name)
        GROUP BY lga
        ORDER BY lga;
    """)

    with engine.connect() as conn:
        return pd.read_sql(query, conn, params={"state_name": state_name})


# def export_state_beneficiaries_csv(state_name, output_folder="exports", chunk_size=100000):
#     os.makedirs(output_folder, exist_ok=True)
#
#     safe_state = str(state_name).replace("/", "_").replace("\\", "_").replace(" ", "_")
#     timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
#
#     output_file = os.path.join(
#         output_folder,
#         f"{safe_state}_Beneficiaries_Details_{timestamp}.csv"
#     )
#
#     query = text("""
#         WITH unified_payments AS
#         (
#             SELECT
#                 'First Tranche' AS tranche,
#                 CAST("State" AS TEXT) AS "State",
#                 CAST("LGA" AS TEXT) AS "LGA",
#                 CAST("Ward" AS TEXT) AS "Ward",
#                 CAST("Community" AS TEXT) AS "Community",
#                 CAST("HouseholdID" AS TEXT) AS "HouseholdID",
#                 CAST(nidhh AS TEXT) AS nidhh,
#                 CAST(nid AS TEXT) AS nid,
#                 CAST("Name" AS TEXT) AS "Name",
#                 CAST("TelephoneNo" AS TEXT) AS "TelephoneNo",
#                 CAST("Gender" AS TEXT) AS "Gender",
#                 CAST("Age" AS TEXT) AS "Age",
#                 CAST("AccountName" AS TEXT) AS "AccountName",
#                 CAST("AccountNumber" AS TEXT) AS "AccountNumber",
#                 CAST("BankName" AS TEXT) AS "BankName",
#                 CAST("AmountPaid" AS TEXT) AS "AmountPaid",
#                 CAST("PaymentStatus" AS TEXT) AS "PaymentStatus",
#                 CAST("PaymentDate" AS TEXT) AS "PaymentDate",
#                 CAST("NIN" AS TEXT) AS "NIN",
#                 CAST("NINBVN" AS TEXT) AS "NINBVN",
#                 CAST("IDType" AS TEXT) AS "IDType",
#                 CAST("TrancheStatus" AS TEXT) AS "TrancheStatus",
#                 CAST("TotalAmount" AS TEXT) AS "TotalAmount",
#                 CAST("AccountUsed" AS TEXT) AS "AccountUsed",
#                 CAST("Zone" AS TEXT) AS "Zone",
#                 CAST("ward_class" AS TEXT) AS "ward_class"
#             FROM ben."itblDistinctPaidBeneficiaries"
#
#             UNION ALL
#
#             SELECT
#                 'Second Tranche' AS tranche,
#                 CAST("State" AS TEXT),
#                 CAST("LGA" AS TEXT),
#                 CAST("Ward" AS TEXT),
#                 CAST("Community" AS TEXT),
#                 CAST("HouseholdID" AS TEXT),
#                 CAST(nidhh AS TEXT),
#                 CAST(nid AS TEXT),
#                 CAST("Name" AS TEXT),
#                 CAST("TelephoneNo" AS TEXT),
#                 CAST("Gender" AS TEXT),
#                 CAST("Age" AS TEXT),
#                 CAST("AccountName" AS TEXT),
#                 CAST("AccountNumber" AS TEXT),
#                 CAST("BankName" AS TEXT),
#                 CAST("AmountPaid" AS TEXT),
#                 CAST("PaymentStatus" AS TEXT),
#                 CAST("PaymentDate" AS TEXT),
#                 CAST("NIN" AS TEXT),
#                 CAST("NINBVN" AS TEXT),
#                 CAST("IDType" AS TEXT),
#                 CAST("TrancheStatus" AS TEXT),
#                 CAST("TotalAmount" AS TEXT),
#                 CAST("AccountUsed" AS TEXT),
#                 CAST("Zone" AS TEXT),
#                 CAST("ward_class" AS TEXT)
#             FROM ben."itblDistinctSecondTranche"
#
#             UNION ALL
#
#             SELECT
#                 'Third Tranche' AS tranche,
#                 CAST("State" AS TEXT),
#                 CAST("LGA" AS TEXT),
#                 CAST("Ward" AS TEXT),
#                 CAST("Community" AS TEXT),
#                 CAST("HouseholdID" AS TEXT),
#                 CAST(nidhh AS TEXT),
#                 CAST(nid AS TEXT),
#                 CAST("Name" AS TEXT),
#                 CAST("TelephoneNo" AS TEXT),
#                 CAST("Gender" AS TEXT),
#                 CAST("Age" AS TEXT),
#                 CAST("AccountName" AS TEXT),
#                 CAST("AccountNumber" AS TEXT),
#                 CAST("BankName" AS TEXT),
#                 CAST("AmountPaid" AS TEXT),
#                 CAST("PaymentStatus" AS TEXT),
#                 CAST("PaymentDate" AS TEXT),
#                 CAST("NIN" AS TEXT),
#                 CAST("NINBVN" AS TEXT),
#                 CAST("IDType" AS TEXT),
#                 CAST("TrancheStatus" AS TEXT),
#                 CAST("TotalAmount" AS TEXT),
#                 CAST("AccountUsed" AS TEXT),
#                 CAST("Zone" AS TEXT),
#                 CAST("ward_class" AS TEXT)
#             FROM ben."itblDistinctThirdTranche"
#         )
#         SELECT *
#         FROM unified_payments
#         WHERE lower("State") = lower(:state_name)
#         ORDER BY tranche, "LGA", "Ward", "Community";
#     """)
#
#     first_chunk = True
#     total_rows = 0
#
#     with engine.connect().execution_options(stream_results=True) as conn:
#         for chunk in pd.read_sql(
#                 query,
#                 conn,
#                 params={"state_name": state_name},
#                 chunksize=chunk_size
#         ):
#             chunk.to_csv(
#                 output_file,
#                 mode="w" if first_chunk else "a",
#                 index=False,
#                 header=first_chunk,
#                 encoding="utf-8-sig"
#             )
#
#             total_rows += len(chunk)
#             first_chunk = False
#
#     return output_file, total_rows



import io

def export_state_beneficiaries_csv(state_name, chunk_size=100000):
    query = text("""
        WITH unified_payments AS
        (
            SELECT
                'First Tranche' AS tranche,
                CAST("State" AS TEXT) AS "State",
                CAST("LGA" AS TEXT) AS "LGA",
                CAST("Ward" AS TEXT) AS "Ward",
                CAST("Community" AS TEXT) AS "Community",
                CAST("HouseholdID" AS TEXT) AS "HouseholdID",
                CAST(nidhh AS TEXT) AS nidhh,
                CAST(nid AS TEXT) AS nid,
                CAST("Name" AS TEXT) AS "Name",
                CAST("TelephoneNo" AS TEXT) AS "TelephoneNo",
                CAST("Gender" AS TEXT) AS "Gender",
                CAST("Age" AS TEXT) AS "Age",
                CAST("AccountName" AS TEXT) AS "AccountName",
                CAST("AccountNumber" AS TEXT) AS "AccountNumber",
                CAST("BankName" AS TEXT) AS "BankName",
                CAST("AmountPaid" AS TEXT) AS "AmountPaid",
                CAST("PaymentStatus" AS TEXT) AS "PaymentStatus",
                CAST("PaymentDate" AS TEXT) AS "PaymentDate",
                CAST("NIN" AS TEXT) AS "NIN",
                CAST("NINBVN" AS TEXT) AS "NINBVN",
                CAST("IDType" AS TEXT) AS "IDType",
                CAST("TrancheStatus" AS TEXT) AS "TrancheStatus",
                CAST("TotalAmount" AS TEXT) AS "TotalAmount",
                CAST("AccountUsed" AS TEXT) AS "AccountUsed",
                CAST("Zone" AS TEXT) AS "Zone",
                CAST("ward_class" AS TEXT) AS "ward_class"
            FROM ben."itblDistinctPaidBeneficiaries"

            UNION ALL

            SELECT
                'Second Tranche' AS tranche,
                CAST("State" AS TEXT), CAST("LGA" AS TEXT), CAST("Ward" AS TEXT),
                CAST("Community" AS TEXT), CAST("HouseholdID" AS TEXT),
                CAST(nidhh AS TEXT), CAST(nid AS TEXT), CAST("Name" AS TEXT),
                CAST("TelephoneNo" AS TEXT), CAST("Gender" AS TEXT),
                CAST("Age" AS TEXT), CAST("AccountName" AS TEXT),
                CAST("AccountNumber" AS TEXT), CAST("BankName" AS TEXT),
                CAST("AmountPaid" AS TEXT), CAST("PaymentStatus" AS TEXT),
                CAST("PaymentDate" AS TEXT), CAST("NIN" AS TEXT),
                CAST("NINBVN" AS TEXT), CAST("IDType" AS TEXT),
                CAST("TrancheStatus" AS TEXT), CAST("TotalAmount" AS TEXT),
                CAST("AccountUsed" AS TEXT), CAST("Zone" AS TEXT),
                CAST("ward_class" AS TEXT)
            FROM ben."itblDistinctSecondTranche"

            UNION ALL

            SELECT
                'Third Tranche' AS tranche,
                CAST("State" AS TEXT), CAST("LGA" AS TEXT), CAST("Ward" AS TEXT),
                CAST("Community" AS TEXT), CAST("HouseholdID" AS TEXT),
                CAST(nidhh AS TEXT), CAST(nid AS TEXT), CAST("Name" AS TEXT),
                CAST("TelephoneNo" AS TEXT), CAST("Gender" AS TEXT),
                CAST("Age" AS TEXT), CAST("AccountName" AS TEXT),
                CAST("AccountNumber" AS TEXT), CAST("BankName" AS TEXT),
                CAST("AmountPaid" AS TEXT), CAST("PaymentStatus" AS TEXT),
                CAST("PaymentDate" AS TEXT), CAST("NIN" AS TEXT),
                CAST("NINBVN" AS TEXT), CAST("IDType" AS TEXT),
                CAST("TrancheStatus" AS TEXT), CAST("TotalAmount" AS TEXT),
                CAST("AccountUsed" AS TEXT), CAST("Zone" AS TEXT),
                CAST("ward_class" AS TEXT)
            FROM ben."itblDistinctThirdTranche"
        )
        SELECT *
        FROM unified_payments
        WHERE lower("State") = lower(:state_name)
        ORDER BY tranche, "LGA", "Ward", "Community";
    """)

    csv_buffer = io.StringIO()
    first_chunk = True
    total_rows = 0

    with engine.connect().execution_options(stream_results=True) as conn:
        for chunk in pd.read_sql(
            query,
            conn,
            params={"state_name": state_name},
            chunksize=chunk_size
        ):
            chunk.to_csv(
                csv_buffer,
                index=False,
                header=first_chunk,
                encoding="utf-8-sig"
            )
            total_rows += len(chunk)
            first_chunk = False

    return csv_buffer.getvalue(), total_rows
# ============================================================
# LOGIN PAGE
# ============================================================

# if "logged_in" not in st.session_state:
#     st.session_state.logged_in = False
#
# if not st.session_state.logged_in:
#     st.title("🔐 NCTO State MIS Login")
#
#     username = st.text_input("Username")
#     password = st.text_input("Password", type="password")
#
#     if st.button("Login"):
#         user = authenticate_user(username.strip(), password)
#
#         if user:
#             st.session_state.logged_in = True
#             st.session_state.username = user["username"]
#             st.session_state.state = user["state"]
#             st.rerun()
#         else:
#             st.error("Invalid username or password.")
#
#     st.stop()

# 1. Initialize permanent profile keys at the top
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False
if "logged_in_user" not in st.session_state:
    st.session_state.logged_in_user = None
if "assigned_state" not in st.session_state:
    st.session_state.assigned_state = None

if not st.session_state.logged_in:
    st.title("🔐 NCTO State MIS Login")

    # 2. Use distinct names for your widget keys (e.g., adding '_input')
    username_input = st.text_input("Username", key="username_widget")
    password_input = st.text_input("Password", type="password", key="password_widget")

    if st.button("Login"):
        user = authenticate_user(username_input.strip(), password_input)

        if user:
            # 3. Store authentication data safely in the separate keys
            st.session_state.logged_in = True
            st.session_state.logged_in_user = user["username"]
            st.session_state.assigned_state = user["state"]
            st.rerun()
        else:
            st.error("Invalid username or password.")

    st.stop()

# ============================================================
# MAIN APP
# ============================================================

#st.title("NCTO State Officer Payment Data Portal")

# assigned_state = st.session_state.state
# Returns None instead of throwing a KeyError if the state isn't found yet
# assigned_state = st.session_state.get("state", "No State Assigned")
#
#
#
# st.sidebar.success(f"Logged in as: {st.session_state.username}")

# This line 613 will now work perfectly after rerun!
# 4. Use the safe, non-widget keys down here in your main app
st.sidebar.success(f"Logged in as: {st.session_state.logged_in_user}")
assigned_state = st.session_state.assigned_state

st.sidebar.info(f"Assigned State: {assigned_state}")

if st.sidebar.button("Logout"):
    st.session_state.clear()
    st.rerun()

st.subheader(f"Payment Data for {assigned_state}")

# ============================================================
# STATE SUMMARY
# ============================================================

summary_df = load_state_summary(assigned_state)

if not summary_df.empty:
    c1, c2, c3 = st.columns(3)

    c1.metric("Total Beneficiaries", f"{summary_df['total_beneficiaries'].sum():,}")
    c2.metric("Total Households", f"{summary_df['total_households'].sum():,}")
    c3.metric("Tranches Available", f"{summary_df['tranche'].nunique():,}")

st.markdown("### State Summary")

if summary_df.empty:
    st.warning("No state summary found.")
else:
    st.dataframe(summary_df, use_container_width=True)

    summary_excel_file = f"{assigned_state}_State_Summary.xlsx"
    summary_df.to_excel(summary_excel_file, index=False)

    with open(summary_excel_file, "rb") as f:
        st.download_button(
            label="Download State Summary",
            data=f,
            file_name=summary_excel_file,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_state_summary"
        )

# ============================================================
# LGA SUMMARY
# ============================================================

st.markdown("### LGA Summary")

lga_summary_df = load_lga_summary(assigned_state)

if lga_summary_df.empty:
    st.warning("No LGA summary found for this state.")
else:
    st.dataframe(lga_summary_df, use_container_width=True, height=500)

    lga_excel_file = f"{assigned_state}_LGA_Summary.xlsx"
    lga_summary_df.to_excel(lga_excel_file, index=False)

    with open(lga_excel_file, "rb") as f:
        st.download_button(
            label="Download LGA Summary",
            data=f,
            file_name=lga_excel_file,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key="download_lga_summary"
        )

# ============================================================
# LARGE CSV EXPORT
# ============================================================

st.markdown("### Export Full Beneficiaries Details")

if st.button(
    "Prepare Full Beneficiaries Export",
    key="prepare_csv_export"
):
    with st.spinner("Preparing export..."):

        csv_data, total_rows = export_state_beneficiaries_csv(
            assigned_state
        )

    st.success(
        f"{total_rows:,} beneficiary records prepared."
    )

    st.download_button(
        label="Download CSV",
        data=csv_data,
        file_name=f"{assigned_state}_Beneficiaries.csv",
        mime="text/csv",
        key="download_full_csv"
    )

# st.markdown("### Export Full Beneficiaries Details")
#
# st.info(
#     "Use this option for large state exports. It writes the CSV in chunks, "
#     "so it can handle large beneficiary records better than Excel."
# )
#
# if st.button("Generate Full State Beneficiaries CSV", key="generate_full_csv"):
#     with st.spinner("Generating CSV export..."):
#         csv_file, total_rows = export_state_beneficiaries_csv(
#             assigned_state,
#             output_folder="EXPORT_FOLDER",
#             chunk_size=100000
#         )
#
#     st.success(f"CSV generated successfully. Total rows exported: {total_rows:,}")
#
#     with open(csv_file, "rb") as f:
#         st.download_button(
#             label="Download Full Beneficiaries CSV",
#             data=f,
#             file_name=os.path.basename(csv_file),
#             mime="text/csv",
#             key="download_full_beneficiaries_csv"
#         )
# ============================================================
# DETAILED DATA FILTERS
# ============================================================

st.markdown("### Search and Filter Detailed Data")

col1, col2, col3 = st.columns(3)

with col1:
    tranche_filter = st.selectbox(
        "Tranche",
        ["All", "First Tranche", "Second Tranche", "Third Tranche"],
        key="tranche_filter"
    )

with col2:
    search_value = st.text_input(
        "Search by NID, NIDHH, NIN, Account Number, Name",
        key="search_value"
    )

with col3:
    limit_rows = st.number_input(
        "Maximum Rows to Load",
        min_value=1000,
        max_value=500000,
        value=50000,
        step=10000,
        key="limit_rows"
    )

if st.button("Load Data", key="load_detailed_data"):
    df = load_state_data(
        assigned_state,
        tranche_filter,
        search_value.strip(),
        limit_rows
    )

    if df.empty:
        st.warning("No records found.")
    else:
        st.success(f"{len(df):,} records loaded.")
        st.dataframe(df, use_container_width=True, height=600)

        output_file = f"{assigned_state}_payment_data.xlsx"
        df.to_excel(output_file, index=False)

        with open(output_file, "rb") as f:
            st.download_button(
                label="Download Detailed Data",
                data=f,
                file_name=output_file,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key="download_detailed_data"
            )
