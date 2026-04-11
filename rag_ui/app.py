import streamlit as st
import requests
import json
import base64
import os
import logging
from datetime import datetime, timedelta
import pandas as pd
import plotly.graph_objects as go
from dotenv import load_dotenv
import re

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Load environment variables from .env file if it exists
load_dotenv()

# Configuration from environment variables or defaults
DEFAULT_API_BASE = os.getenv("API_ENDPOINT")
API_ENDPOINTS = {
    "base_url": DEFAULT_API_BASE,
    "upload": os.getenv("UPLOAD_ENDPOINT", "/upload"),
    "query": os.getenv("QUERY_ENDPOINT", "/query"),
    "auth": os.getenv("AUTH_ENDPOINT", "/auth")
}
DEFAULT_USER_ID = os.getenv("DEFAULT_USER_ID", "test-user")
COGNITO_CLIENT_ID = os.getenv("COGNITO_CLIENT_ID", "")
ENABLE_EVALUATION = os.getenv("ENABLE_EVALUATION", "false").lower() == "true"

# Set page config
st.set_page_config(
    page_title="RAG Application",
    page_icon="📄",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Session state initialization
if 'user_id' not in st.session_state:
    st.session_state.user_id = DEFAULT_USER_ID

if 'uploaded_docs' not in st.session_state:
    st.session_state.uploaded_docs = []

# Authentication state
if 'authenticated' not in st.session_state:
    st.session_state.authenticated = False

if 'access_token' not in st.session_state:
    st.session_state.access_token = None

if 'id_token' not in st.session_state:
    st.session_state.id_token = None

if 'refresh_token' not in st.session_state:
    st.session_state.refresh_token = None

if 'token_expiry' not in st.session_state:
    st.session_state.token_expiry = None

if 'user_email' not in st.session_state:
    st.session_state.user_email = None

# MCP Web Search Configuration
if 'mcp_web_search_enabled' not in st.session_state:
    st.session_state.mcp_web_search_enabled = False

if 'mcp_server_url' not in st.session_state:
    st.session_state.mcp_server_url = ""


# Function to get headers with correct authentication token format
def get_headers():
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Access-Control-Allow-Origin": "*"
    }

    if st.session_state.id_token:
        headers["Authorization"] = f"Bearer {st.session_state.id_token}"
    elif st.session_state.access_token:
        headers["Authorization"] = f"Bearer {st.session_state.access_token}"

    logger.info(f"Request headers (partial): {dict((k, v[:20] + '...' if k == 'Authorization' and v else v) for k, v in headers.items())}")
    return headers


# Function to validate email format
def is_valid_email(email):
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$"
    return re.match(pattern, email) is not None


# Function to validate password strength
def is_strong_password(password):
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not any(c.isupper() for c in password):
        return False, "Password must contain at least one uppercase letter."
    if not any(c.islower() for c in password):
        return False, "Password must contain at least one lowercase letter."
    if not any(c.isdigit() for c in password):
        return False, "Password must contain at least one number."
    if not any(c in "!@#$%^&*()_-+=<>?/|" for c in password):
        return False, "Password must contain at least one special character."
    return True, "Password is strong."


# Function to register a new user
def register_user(email, password, name=""):
    payload = {"operation": "register", "email": email, "password": password, "name": name}
    auth_url = f"{API_ENDPOINTS['base_url']}{API_ENDPOINTS['auth']}"
    try:
        response = requests.post(auth_url, json=payload, headers={"Content-Type": "application/json"})
        logger.info(f"Register response status: {response.status_code}")
        try:
            logger.info(f"Register response body: {response.json()}")
        except:
            logger.info(f"Register response text: {response.text}")
        if response.status_code == 200:
            result = response.json()
            return True, result.get("message", "Registration successful.")
        else:
            return False, response.json().get("message", f"Error: {response.status_code}")
    except Exception as e:
        logger.error(f"Register error: {str(e)}")
        return False, f"Error: {str(e)}"


# Function to verify a user's email
def verify_user(email, confirmation_code):
    payload = {"operation": "verify", "email": email, "confirmation_code": confirmation_code}
    auth_url = f"{API_ENDPOINTS['base_url']}{API_ENDPOINTS['auth']}"
    try:
        response = requests.post(auth_url, json=payload, headers={"Content-Type": "application/json"})
        logger.info(f"Verify response status: {response.status_code}")
        if response.status_code == 200:
            return True, response.json().get("message", "Verification successful.")
        else:
            return False, response.json().get("message", f"Error: {response.status_code}")
    except Exception as e:
        logger.error(f"Verify error: {str(e)}")
        return False, f"Error: {str(e)}"


# Function to login a user
def login_user(email, password):
    payload = {"operation": "login", "email": email, "password": password}
    auth_url = f"{API_ENDPOINTS['base_url']}{API_ENDPOINTS['auth']}"
    try:
        response = requests.post(auth_url, json=payload, headers={"Content-Type": "application/json"})
        if response.status_code == 200:
            result = response.json()
            access_token  = result.get("access_token")
            id_token      = result.get("id_token")
            refresh_token = result.get("refresh_token")
            expires_in    = result.get("expires_in", 3600)
            expiry_time   = datetime.now() + timedelta(seconds=expires_in)

            try:
                token_parts = id_token.split('.')
                if len(token_parts) == 3:
                    payload_data = json.loads(base64.b64decode(token_parts[1] + '==').decode('utf-8'))
                    user_id    = payload_data.get('sub')
                    user_email = payload_data.get('email')
                else:
                    user_id = "unknown"
                    user_email = email
            except:
                user_id = "unknown"
                user_email = email

            return True, {
                "message":      result.get("message", "Login successful."),
                "access_token": access_token,
                "id_token":     id_token,
                "refresh_token": refresh_token,
                "token_expiry": expiry_time,
                "user_id":      user_id,
                "user_email":   user_email
            }
        else:
            return False, response.json().get("message", f"Error: {response.status_code}")
    except Exception as e:
        logger.error(f"Login error: {str(e)}")
        return False, f"Error: {str(e)}"


# Function to refresh tokens
def refresh_token_func(refresh_token_value):
    payload  = {"operation": "refresh_token", "refresh_token": refresh_token_value}
    auth_url = f"{API_ENDPOINTS['base_url']}{API_ENDPOINTS['auth']}"
    try:
        response = requests.post(auth_url, json=payload, headers={"Content-Type": "application/json"})
        logger.info(f"Refresh token response status: {response.status_code}")
        if response.status_code == 200:
            result      = response.json()
            expiry_time = datetime.now() + timedelta(seconds=result.get("expires_in", 3600))
            return True, {
                "message":      result.get("message", "Tokens refreshed successfully."),
                "access_token": result.get("access_token"),
                "id_token":     result.get("id_token"),
                "token_expiry": expiry_time
            }
        else:
            return False, response.json().get("message", f"Error: {response.status_code}")
    except Exception as e:
        logger.error(f"Refresh token error: {str(e)}")
        return False, f"Error: {str(e)}"


# Function to initiate forgot password
def forgot_password(email):
    payload  = {"operation": "forgot_password", "email": email}
    auth_url = f"{API_ENDPOINTS['base_url']}{API_ENDPOINTS['auth']}"
    try:
        response = requests.post(auth_url, json=payload, headers={"Content-Type": "application/json"})
        logger.info(f"Forgot password response status: {response.status_code}")
        if response.status_code == 200:
            return True, response.json().get("message", "Password reset initiated.")
        else:
            return False, response.json().get("message", f"Error: {response.status_code}")
    except Exception as e:
        logger.error(f"Forgot password error: {str(e)}")
        return False, f"Error: {str(e)}"


# Function to confirm forgot password
def confirm_forgot_password(email, confirmation_code, new_password):
    payload = {
        "operation": "confirm_forgot_password",
        "email": email,
        "confirmation_code": confirmation_code,
        "new_password": new_password
    }
    auth_url = f"{API_ENDPOINTS['base_url']}{API_ENDPOINTS['auth']}"
    try:
        response = requests.post(auth_url, json=payload, headers={"Content-Type": "application/json"})
        logger.info(f"Confirm forgot password response status: {response.status_code}")
        if response.status_code == 200:
            return True, response.json().get("message", "Password reset confirmed.")
        else:
            return False, response.json().get("message", f"Error: {response.status_code}")
    except Exception as e:
        logger.error(f"Confirm forgot password error: {str(e)}")
        return False, f"Error: {str(e)}"


# Check if token needs to be refreshed
def check_token_refresh():
    if st.session_state.authenticated and st.session_state.token_expiry:
        if st.session_state.token_expiry < datetime.now() + timedelta(minutes=5):
            if st.session_state.refresh_token:
                success, result = refresh_token_func(st.session_state.refresh_token)
                if success:
                    st.session_state.access_token = result["access_token"]
                    st.session_state.id_token     = result["id_token"]
                    st.session_state.token_expiry = result["token_expiry"]
                    logger.info("Token refreshed successfully")
                    return True
                else:
                    logger.warning("Token refresh failed, logging out user")
                    logout_user()
                    return False
            else:
                logger.warning("No refresh token available, logging out user")
                logout_user()
                return False
    return True


# Function to log out the user
def logout_user():
    st.session_state.authenticated = False
    st.session_state.access_token  = None
    st.session_state.id_token      = None
    st.session_state.refresh_token = None
    st.session_state.token_expiry  = None
    st.session_state.user_email    = None
    st.session_state.user_id       = DEFAULT_USER_ID


# Function to test authentication token
def test_auth_token():
    if not st.session_state.authenticated:
        return False
    payload  = {"action": "healthcheck"}
    auth_url = f"{API_ENDPOINTS['base_url']}{API_ENDPOINTS['auth']}"
    try:
        response = requests.post(auth_url, json=payload, headers=get_headers())
        if response.status_code == 200:
            logger.info("Auth token test successful")
            return True
        else:
            logger.warning("Auth token test failed, token is invalid")
            return False
    except Exception as e:
        logger.error(f"Auth token test error: {str(e)}")
        return False


# Application title
st.title("RAG App on AWS")


# Function to render the login page
def render_login_page():
    tab1, tab2, tab3 = st.tabs(["Login", "Register", "Forgot Password"])

    with tab1:
        email    = st.text_input("Email:", key="login_email")
        password = st.text_input("Password:", type="password", key="login_password")
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("Login", use_container_width=True):
                if not email or not password:
                    st.error("Please enter both email and password.")
                else:
                    with st.spinner("Logging in..."):
                        success, result = login_user(email, password)
                        if success:
                            st.success(result.get("message", "Login successful!"))
                            st.session_state.authenticated  = True
                            st.session_state.access_token  = result["access_token"]
                            st.session_state.id_token      = result["id_token"]
                            st.session_state.refresh_token = result["refresh_token"]
                            st.session_state.token_expiry  = result["token_expiry"]
                            st.session_state.user_id       = result["user_id"]
                            st.session_state.user_email    = result["user_email"]
                            st.rerun()
                        else:
                            error_msg = result
                            if "UserNotConfirmed" in error_msg:
                                st.error("Email not verified. Please check your email for verification code.")
                                st.session_state.verify_email  = email
                                st.session_state.current_tab   = "Verify"
                            else:
                                st.error(error_msg)

    with tab2:
        reg_email            = st.text_input("Email:", key="reg_email")
        reg_name             = st.text_input("Name (optional):", key="reg_name")
        reg_password         = st.text_input("Password:", type="password", key="reg_password")
        reg_confirm_password = st.text_input("Confirm Password:", type="password", key="reg_confirm_password")
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("Register", use_container_width=True):
                if not reg_email:
                    st.error("Email is required.")
                elif not is_valid_email(reg_email):
                    st.error("Please enter a valid email address.")
                elif not reg_password:
                    st.error("Password is required.")
                elif reg_password != reg_confirm_password:
                    st.error("Passwords do not match.")
                else:
                    is_valid, message = is_strong_password(reg_password)
                    if not is_valid:
                        st.error(message)
                    else:
                        with st.spinner("Registering..."):
                            success, result = register_user(reg_email, reg_password, reg_name)
                            if success:
                                st.success(result)
                                st.session_state.verify_email = reg_email
                                st.session_state.current_tab  = "Verify"
                            else:
                                st.error(result)

    if st.session_state.get("current_tab") == "Verify" or "verify_email" in st.session_state:
        with st.expander("Verify Your Email", expanded=True):
            st.write(f"Please enter the verification code sent to {st.session_state.get('verify_email', '')}")
            verification_code = st.text_input("Verification Code:", key="verification_code")
            col1, col2 = st.columns([1, 1])
            with col1:
                if st.button("Verify Email", use_container_width=True):
                    if not verification_code:
                        st.error("Please enter the verification code.")
                    else:
                        with st.spinner("Verifying..."):
                            success, result = verify_user(st.session_state.get("verify_email", ""), verification_code)
                            if success:
                                st.success(result)
                                if "verify_email" in st.session_state:
                                    del st.session_state.verify_email
                                if "current_tab" in st.session_state:
                                    del st.session_state.current_tab
                            else:
                                st.error(result)

    with tab3:
        forgot_email = st.text_input("Email:", key="forgot_email")
        col1, col2 = st.columns([1, 1])
        with col1:
            if st.button("Reset Password", use_container_width=True):
                if not forgot_email:
                    st.error("Please enter your email.")
                elif not is_valid_email(forgot_email):
                    st.error("Please enter a valid email address.")
                else:
                    with st.spinner("Processing request..."):
                        success, result = forgot_password(forgot_email)
                        if success:
                            st.success(result)
                            st.session_state.reset_email       = forgot_email
                            st.session_state.show_reset_confirm = True
                        else:
                            st.error(result)

    if st.session_state.get("show_reset_confirm", False):
        with st.expander("Confirm Password Reset", expanded=True):
            st.write(f"Please enter the confirmation code sent to {st.session_state.get('reset_email', '')}")
            reset_code             = st.text_input("Confirmation Code:", key="reset_code")
            reset_password         = st.text_input("New Password:", type="password", key="reset_password")
            reset_confirm_password = st.text_input("Confirm New Password:", type="password", key="reset_confirm_password")
            col1, col2 = st.columns([1, 1])
            with col1:
                if st.button("Confirm Reset", use_container_width=True):
                    if not reset_code:
                        st.error("Please enter the confirmation code.")
                    elif not reset_password:
                        st.error("Please enter a new password.")
                    elif reset_password != reset_confirm_password:
                        st.error("Passwords do not match.")
                    else:
                        is_valid, message = is_strong_password(reset_password)
                        if not is_valid:
                            st.error(message)
                        else:
                            with st.spinner("Resetting password..."):
                                success, result = confirm_forgot_password(
                                    st.session_state.get("reset_email", ""),
                                    reset_code,
                                    reset_password
                                )
                                if success:
                                    st.success(result)
                                    if "reset_email" in st.session_state:
                                        del st.session_state.reset_email
                                    if "show_reset_confirm" in st.session_state:
                                        del st.session_state.show_reset_confirm
                                else:
                                    st.error(result)


# Add a user profile in the sidebar
def render_user_sidebar():
    if st.session_state.authenticated:
        st.sidebar.markdown("---")
        st.sidebar.subheader("User Profile")
        st.sidebar.write(f"Email: {st.session_state.user_email}")

        if st.session_state.token_expiry:
            now    = datetime.now()
            expiry = st.session_state.token_expiry

            if expiry > now:
                minutes_left = int((expiry - now).total_seconds() / 60)
                if minutes_left > 60:
                    hours = minutes_left // 60
                    mins  = minutes_left % 60
                    expiry_text = f"Token expires in {hours}h {mins}m"
                else:
                    expiry_text = f"Token expires in {minutes_left}m"

                if minutes_left < 5:
                    st.sidebar.warning(expiry_text)
                else:
                    st.sidebar.info(expiry_text)
            else:
                st.sidebar.error("Token expired. Please log in again.")

            check_token_refresh()

        if st.sidebar.button("Logout", use_container_width=True):
            logout_user()
            st.rerun()


# ─────────────────────────────────────────────────────────────────────────────
# Upload document using presigned S3 URL pattern
# ─────────────────────────────────────────────────────────────────────────────
# Flow:
#   1. POST /upload {operation: get_upload_url}  → Lambda returns presigned PUT URL
#   2. PUT <presigned_url>                        → Browser uploads directly to S3
#                                                   (no API Gateway, no Lambda timeout)
#   3. POST /upload {operation: confirm_upload}   → Lambda writes metadata to DB
#
# This eliminates the 504 "Endpoint request timed out" errors that occurred
# because the old approach sent the entire base64-encoded file through API
# Gateway (29 s hard timeout, 10 MB payload limit).
# ─────────────────────────────────────────────────────────────────────────────
def upload_document(file, user_id):
    if not check_token_refresh():
        st.error("Session expired. Please log in again.")
        logout_user()
        st.rerun()
        return False, "Authentication failed."

    upload_url = f"{API_ENDPOINTS['base_url']}{API_ENDPOINTS['upload']}"
    headers    = get_headers()
    mime_type  = file.type or "application/octet-stream"

    # ── Step 1: Get a presigned S3 PUT URL from the Lambda ───────────────────
    try:
        url_response = requests.post(
            upload_url,
            headers=headers,
            json={
                "operation": "get_upload_url",
                "file_name": file.name,
                "mime_type": mime_type,
                "user_id":   user_id,
            }
        )
        logger.info(f"get_upload_url response: {url_response.status_code}")

        if url_response.status_code != 200:
            return show_error(f"Failed to get upload URL (Error {url_response.status_code})", url_response)

        url_data    = url_response.json()
        presigned   = url_data["upload_url"]
        document_id = url_data["document_id"]
        s3_key      = url_data["s3_key"]

    except Exception as e:
        logger.error(f"Exception getting upload URL: {e}")
        return show_error("Exception getting upload URL", str(e))

    # ── Step 2: Upload file directly to S3 (bypasses API Gateway entirely) ───
    try:
        s3_response = requests.put(
            presigned,
            data=file.getvalue(),
            headers={"Content-Type": mime_type},
        )
        logger.info(f"Direct S3 upload response: {s3_response.status_code}")

        if s3_response.status_code not in (200, 204):
            return show_error(
                f"S3 upload failed (Error {s3_response.status_code})",
                s3_response.text
            )

    except Exception as e:
        logger.error(f"Exception uploading to S3: {e}")
        return show_error("Exception uploading to S3", str(e))

    # ── Step 3: Confirm upload — Lambda writes metadata to PostgreSQL/DynamoDB
    try:
        confirm_response = requests.post(
            upload_url,
            headers=headers,
            json={
                "operation":   "confirm_upload",
                "document_id": document_id,
                "file_name":   file.name,
                "mime_type":   mime_type,
                "user_id":     user_id,
                "s3_key":      s3_key,
            }
        )
        logger.info(f"confirm_upload response: {confirm_response.status_code}")

        if confirm_response.status_code != 200:
            return show_error(
                f"Upload confirmation failed (Error {confirm_response.status_code})",
                confirm_response
            )

        return handle_response(confirm_response, file.name, user_id)

    except Exception as e:
        logger.error(f"Exception confirming upload: {e}")
        return show_error("Exception confirming upload", str(e))


# Function to handle Upload API response
def handle_response(response, file_name, user_id):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if response.status_code == 200:
        result      = response.json()
        document_id = result.get("document_id")
        st.session_state.uploaded_docs.append({
            "document_id": document_id,
            "file_name":   file_name,
            "upload_time": timestamp,
            "status":      "Uploaded",
            "user_id":     user_id
        })
        show_success_tabs(file_name, document_id, timestamp, user_id, result)
        return True, result

    elif response.status_code == 401:
        st.error("Authentication failed. Please log in again.")
        logout_user()
        st.rerun()
        return False, "Authentication failed."

    else:
        return show_error(f"Upload failed (Error {response.status_code})", response)


# Function to show success tabs after upload
def show_success_tabs(file_name, document_id, timestamp, user_id, result):
    tab1, tab2, tab3 = st.tabs(["Upload Summary", "API Response", "Recent Uploads"])
    with tab1:
        st.write(f"**File Name:** {file_name}")
        st.write(f"**Document ID:** {document_id}")
        st.write(f"**Upload Time:** {timestamp}")
        st.write(f"**User ID:** {user_id}")
        st.write("**Status:** Success")
    with tab2:
        st.json(result)
    with tab3:
        show_upload_history()


# Function to show error message in tabs
def show_error(title, details):
    tab1, tab2 = st.tabs(["Error Details", "Recent Uploads"])
    with tab1:
        st.subheader("Error Information")
        if isinstance(details, str):
            st.write(details)
        else:
            try:
                st.json(details.json())
            except:
                st.code(details.text)
    with tab2:
        show_upload_history()
    st.error(title)
    return False, title


# Function to show recent upload history
def show_upload_history():
    if not st.session_state.get("uploaded_docs"):
        st.info("No upload history available.")
        return
    recent = sorted(st.session_state.uploaded_docs, key=lambda x: x.get('upload_time', ''), reverse=True)[:5]
    df = pd.DataFrame(recent)
    st.dataframe(df, use_container_width=True)
    if st.button("Clear Upload History", key="clear_history_upload_func"):
        st.session_state.uploaded_docs = []
        st.rerun()


# Function to query documents
def query_documents(selected_model, query_text, user_id, ground_truth=None, enable_evaluation=ENABLE_EVALUATION, web_search_with_mcp=False, mcp_server_url=None):
    if not check_token_refresh():
        st.error("Your session has expired. Please log in again.")
        logout_user()
        st.rerun()
        return False, "Authentication failed. Please log in again."

    payload = {
        "query":              query_text,
        "user_id":            user_id,
        "enable_evaluation":  enable_evaluation,
        "model_name":         selected_model,
        "web_search_with_mcp": web_search_with_mcp,
        "mcp_server_url":     mcp_server_url
    }

    if ground_truth:
        payload["ground_truth"] = ground_truth

    try:
        query_url = f"{API_ENDPOINTS['base_url']}{API_ENDPOINTS['query']}"
        logger.info(f"Sending query request to: {query_url}")
        logger.info(f"Query payload: {payload}")

        response = requests.post(query_url, json=payload, headers=get_headers())

        logger.info(f"Query response status: {response.status_code}")
        logger.info(f"Query response headers: {dict(response.headers)}")
        try:
            logger.info(f"Query response body: {response.json()}")
        except:
            logger.info(f"Query response text: {response.text}")

        if response.status_code == 200:
            return True, response.json()

        elif response.status_code == 401:
            error_message = "Authentication token expired or invalid."
            try:
                error_data = response.json()
                if "message" in error_data:
                    error_message = error_data["message"]
            except:
                pass
            st.warning(f"{error_message} Please log in again.")
            logout_user()
            st.rerun()
            return False, "Authentication failed. Please log in again."

        else:
            error_message = f"Error: {response.status_code}"
            try:
                error_data = response.json()
                if "message" in error_data:
                    error_message += f" - {error_data['message']}"
            except:
                error_message += f" - {response.text}"
            return False, error_message

    except Exception as e:
        logger.error(f"Query error: {str(e)}")
        return False, f"Error: {str(e)}"


# Function to create evaluation chart
def create_evaluation_chart(eval_results):
    metric_names = {
        "answer_relevancy": "Answer Relevancy",
        "faithfulness":     "Faithfulness",
        "context_precision": "Context Precision"
    }
    colors = {
        "answer_relevancy": "#2563eb",
        "faithfulness":     "#16a34a",
        "context_precision": "#d97706"
    }
    x_values   = [metric_names.get(k, k) for k in eval_results.keys()]
    y_values   = list(eval_results.values())
    bar_colors = [colors.get(k, "#6b7280") for k in eval_results.keys()]

    fig = go.Figure(data=[go.Bar(
        x=x_values, y=y_values,
        text=[f"{v:.2f}" for v in y_values],
        textposition="auto",
        marker_color=bar_colors
    )])
    fig.update_layout(
        title="RAG Evaluation Metrics",
        xaxis_title="Metrics",
        yaxis_title="Score (0-1)",
        yaxis_range=[0, 1],
        template="plotly_white"
    )
    return fig


# Function to render the sidebar with navigation and settings
def render_sidebar():
    st.sidebar.title("📚 App Navigation")
    selected_model = ""

    if st.session_state.get("authenticated", False):
        selected_model = st.selectbox(
            "Select Model",
            options=["gemini-2.0-flash", "gemini-2.0-flash-lite", "gemini-1.5-pro", "gemini-2.5-flash-preview-04-17"],
            index=0,
            help="Select the model to use"
        )
        st.sidebar.markdown("---")
        page = st.sidebar.radio("Select an action:", ["Upload Documents", "Query Documents"])
        render_user_sidebar()
    else:
        page = "Login"

    st.sidebar.markdown("---")
    st.sidebar.caption("🔖 Version: RAG App on AWS v0.1")

    return page, selected_model


# Main function to run the Streamlit app
def main():
    page, selected_model = render_sidebar()

    if not st.session_state.authenticated and page != "Login":
        render_login_page()
        return
    elif page == "Login":
        render_login_page()
        return

    # ── Upload Documents Page ─────────────────────────────────────────────────
    if page == "Upload Documents":
        st.header("Upload Documents")

        uploaded_file = st.file_uploader(
            "Choose a file to upload",
            type=["pdf", "txt", "docx", "doc", "csv", "xlsx", "json", "md"],
            help="Select a document to upload. Supported formats include PDF, text, Word documents, spreadsheets, and more."
        )

        col1, col2 = st.columns(2)
        with col1:
            upload_user_id = st.text_input(
                "User ID for this upload:",
                value=st.session_state.user_id,
                help="Documents will be associated with this user ID"
            )
        with col2:
            st.write("Upload Status:")
            upload_status = st.empty()
            upload_status.info("No file selected yet")

        if uploaded_file is not None:
            upload_status.info("File selected, ready to upload")
            st.write("File Details:")
            st.json({
                "Filename":  uploaded_file.name,
                "File size": f"{uploaded_file.size / 1024:.2f} KB",
                "MIME type": uploaded_file.type or "application/octet-stream"
            })

            st.write("Ready to upload?")
            if st.button("Upload Document"):
                upload_status.warning("Uploading in progress...")
                with st.spinner("Uploading document..."):
                    success, result = upload_document(uploaded_file, upload_user_id)
                    if success:
                        upload_status.success("Upload complete!")
                        st.success(f"Document uploaded successfully! Document ID: {result.get('document_id')}")
                    else:
                        upload_status.error("Upload failed!")
                        st.error(result)

    # ── Query Documents Page ──────────────────────────────────────────────────
    elif page == "Query Documents":
        st.header("Query Documents")

        eval_expander = st.expander("RAG Evaluation Settings", expanded=False)
        with eval_expander:
            enable_evaluation = st.toggle(
                "Enable RAG Evaluation",
                value=ENABLE_EVALUATION,
                help="Evaluate the quality of RAG responses using metrics like faithfulness and relevancy. "
                     "Enabling this adds extra Gemini API calls and may slow responses."
            )
            use_ground_truth = st.checkbox(
                "Provide Ground Truth", value=False,
                help="Add a ground truth answer to compare with the generated response"
            )
            st.info("RAG evaluation uses Gemini to assess the quality of responses based on retrieved context.")

        agentic_expander = st.expander("MCP Web Search Setting", expanded=False)
        with agentic_expander:
            web_search_with_mcp = st.checkbox(
                "Enable MCP Web Search",
                value=st.session_state.mcp_web_search_enabled,
                help="Use MCP server for web search when traditional RAG quality is insufficient",
                key="mcp_checkbox"
            )
            if web_search_with_mcp != st.session_state.mcp_web_search_enabled:
                st.session_state.mcp_web_search_enabled = web_search_with_mcp

            if web_search_with_mcp:
                mcp_server_url = st.text_input(
                    "MCP Server URL",
                    value=st.session_state.mcp_server_url,
                    placeholder="https://your-mcp-server.com/mcp/",
                    help="URL of the MCP server for web search functionality",
                    key="mcp_url_input"
                )
                if mcp_server_url != st.session_state.mcp_server_url:
                    st.session_state.mcp_server_url = mcp_server_url
            else:
                mcp_server_url = None

        col1, col2 = st.columns([3, 1])
        with col1:
            query = st.text_area(
                "Enter your question:",
                placeholder="e.g., What are the key points in the latest financial report?",
                height=100
            )
            ground_truth = None
            if use_ground_truth:
                ground_truth = st.text_area(
                    "Ground Truth Answer (optional):",
                    placeholder="Enter the correct answer for evaluation purposes.",
                    height=100
                )
        with col2:
            query_user_id = st.text_input(
                "User ID for query:",
                value=st.session_state.user_id,
                help="Retrieves documents associated with this user ID"
            )
            submit_button = st.button("Submit Query", use_container_width=True)

        if submit_button:
            if not query:
                st.warning("Please enter a question.")
            else:
                if 'last_query_result' in st.session_state:
                    del st.session_state.last_query_result

                with st.spinner("Processing query..."):
                    success, result = query_documents(
                        selected_model,
                        query,
                        query_user_id,
                        ground_truth=ground_truth,
                        enable_evaluation=enable_evaluation,
                        web_search_with_mcp=web_search_with_mcp,
                        mcp_server_url=mcp_server_url if web_search_with_mcp else None
                    )

                    if success:
                        st.session_state.last_query_result = result
                        tabs = ["AI Response", "Document Details"]
                        if "evaluation" in result and result["evaluation"]:
                            tabs.append("Evaluation")

                        tab_objects = st.tabs(tabs)

                        # AI Response Tab
                        with tab_objects[0]:
                            st.markdown("### Generated Response")
                            if "response" in result:
                                st.markdown(result["response"])
                                metadata = result.get("metadata", {})
                                if metadata:
                                    with st.expander("Response Metadata"):
                                        col1, col2 = st.columns(2)
                                        with col1:
                                            st.write(f"**Force Web Search:** {metadata.get('force_web_search', False)}")
                                            st.write(f"**MCP Client Type:** {metadata.get('mcp_client_type', 'N/A')}")
                                        with col2:
                                            if metadata.get('mcp_server_url'):
                                                st.write(f"**MCP Server:** {metadata['mcp_server_url']}")
                            else:
                                st.info("No AI-generated response available.")

                        # Document Details Tab
                        with tab_objects[1]:
                            st.markdown("### All Document Sources")

                            mcp_web_search      = result.get("mcp_web_search", {})
                            traditional_results = result.get("traditional_rag", {}).get("results", [])

                            col1, col2, col3 = st.columns(3)
                            with col1:
                                st.metric("Traditional RAG Documents", len(traditional_results))
                            with col2:
                                web_search_used = mcp_web_search.get("used", False)
                                st.metric("Web Search Used", "Yes" if web_search_used else "No")
                            with col3:
                                total_sources = len(traditional_results) + (1 if web_search_used else 0)
                                st.metric("Total Sources", total_sources)

                            if traditional_results or web_search_used:
                                if traditional_results:
                                    st.markdown("#### 📁 Traditional RAG Documents")
                                    for i, doc in enumerate(traditional_results):
                                        score         = doc.get('similarity_score', 0)
                                        score_display = f"{score:.4f}" if isinstance(score, (int, float)) else "N/A"
                                        doc_name      = doc.get('file_name', doc.get('document_id', f'Document {i+1}'))
                                        with st.expander(f"📄 {doc_name} - Relevance: {score_display}"):
                                            col1, col2 = st.columns([1, 1])
                                            with col1:
                                                st.markdown("**Document Metadata**")
                                                metadata = {k: v for k, v in doc.items()
                                                            if k not in ['embedding_vector'] and not isinstance(v, list) or len(v) < 100}
                                                st.json(metadata)
                                            with col2:
                                                st.markdown("**Document Content**")
                                                if "content" in doc:
                                                    st.write(doc["content"])
                                                else:
                                                    st.info("No content available")

                                if web_search_used:
                                    st.markdown("#### 🌐 Web Search Results")
                                    search_data = mcp_web_search.get("data")
                                    if search_data:
                                        with st.expander("🔍 Web Search Content"):
                                            if isinstance(search_data, str):
                                                st.text_area("Search Results", search_data, height=200)
                                            elif isinstance(search_data, dict):
                                                st.json(search_data)
                                            else:
                                                st.write(search_data)
                                    else:
                                        st.info("Web search was used but no data is available.")
                            else:
                                st.info("No document sources found.")

                        # Evaluation Tab
                        if "evaluation" in result and result["evaluation"]:
                            eval_tab_index = len(tab_objects) - 1
                            with tab_objects[eval_tab_index]:
                                st.markdown("### RAG Response Evaluation")
                                eval_results  = result["evaluation"]
                                metrics_cols  = st.columns(len(eval_results))
                                for i, (metric, value) in enumerate(eval_results.items()):
                                    with metrics_cols[i]:
                                        display_name = " ".join(word.capitalize() for word in metric.split("_"))
                                        st.metric(display_name, f"{value:.2f}")

                                chart = create_evaluation_chart(eval_results)
                                st.plotly_chart(chart, use_container_width=True)

                                with st.expander("Understanding Evaluation Metrics"):
                                    st.markdown("""
                                    ### RAG Evaluation Metrics Explained

                                    - **Answer Relevancy (0-1)**: Measures how directly the answer addresses the question.

                                    - **Faithfulness (0-1)**: Measures how factually accurate the answer is based only on the provided context.

                                    - **Context Precision (0-1)**: When ground truth is provided, measures how well the answer aligns with the known correct answer.

                                    A higher score indicates better performance. Scores above 0.7 are generally considered good.
                                    """)
                    else:
                        st.error(f"Query failed: {result}")


# Run the app
if __name__ == "__main__":
    main()
