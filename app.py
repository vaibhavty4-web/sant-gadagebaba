import json
import os
import random
import re
import time
from datetime import datetime, timezone
from typing import Any

import gspread
import pandas as pd
import streamlit as st
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
import io

st.set_page_config(page_title="संत गाडगेबाबा ग्राम स्वच्छता अभियान", page_icon="🧹", layout="wide")

APP_TITLE = "संत गाडगेबाबा ग्राम स्वच्छता अभियान"
SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
RESPONSE_HEADERS = [
    "Timestamp", "GP Code", "Taluka", "GP Name", "Status", "Submitted By",
    "Mobile", "AnswersJson", "Score", "Drive Folder/File"
]
OTP_TTL_SECONDS = 300
MAX_OTP_ATTEMPTS = 5

def config_value(name: str, default=None):
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:
        pass
    return os.getenv(name, default)

def normalize_mobile(value: str) -> str:
    digits = re.sub(r"\D", "", value or "")
    if len(digits) == 10:
        return digits
    if len(digits) == 12 and digits.startswith("91"):
        return digits[-10:]
    return digits

def get_google_creds():
    raw = config_value("GOOGLE_SERVICE_ACCOUNT_JSON")
    if not raw:
        raise RuntimeError("GOOGLE_SERVICE_ACCOUNT_JSON सेट केलेले नाही. README मधील Google सेटअप पूर्ण करा.")
    if isinstance(raw, str):
        info = json.loads(raw)
    else:
        info = dict(raw)
    return Credentials.from_service_account_info(info, scopes=SHEETS_SCOPES)

@st.cache_resource
def get_clients():
    creds = get_google_creds()
    gc = gspread.authorize(creds)
    drive = build("drive", "v3", credentials=creds, cache_discovery=False)
    return gc, drive

def get_spreadsheet():
    gc, _ = get_clients()
    spreadsheet_id = config_value("SPREADSHEET_ID")
    if not spreadsheet_id:
        raise RuntimeError("SPREADSHEET_ID सेट केलेला नाही.")
    return gc.open_by_key(str(spreadsheet_id))

def ensure_worksheet(ss, title, headers):
    try:
        ws = ss.worksheet(title)
    except gspread.WorksheetNotFound:
        ws = ss.add_worksheet(title=title, rows=1000, cols=max(12, len(headers)))
        ws.append_row(headers)
        return ws
    if not ws.get_all_values():
        ws.append_row(headers)
    return ws

def initialize_sheets():
    ss = get_spreadsheet()
    ensure_worksheet(ss, "GP_Master", ["GP Code", "Taluka", "GP Name", "Mobile", "Active"])
    ensure_worksheet(ss, "Questions", ["Section", "Question", "Type", "Required", "Options", "Max Marks"])
    ensure_worksheet(ss, "Responses", RESPONSE_HEADERS)
    ensure_worksheet(ss, "OTP_Audit", ["Timestamp", "Mobile", "Event", "Details"])
    return ss

def read_records(ws):
    values = ws.get_all_records()
    return pd.DataFrame(values) if values else pd.DataFrame()

def audit_otp(ss, mobile, event, details=""):
    ws = ensure_worksheet(ss, "OTP_Audit", ["Timestamp", "Mobile", "Event", "Details"])
    ws.append_row([datetime.now().isoformat(timespec="seconds"), mobile, event, details], value_input_option="USER_ENTERED")

def send_sms_otp(mobile: str, otp: str):
    provider = str(config_value("SMS_PROVIDER", "msg91")).lower()
    if provider == "msg91":
        authkey = config_value("MSG91_AUTHKEY")
        template_id = config_value("MSG91_TEMPLATE_ID")
        sender_id = config_value("MSG91_SENDER_ID", "")
        if not authkey or not template_id:
            raise RuntimeError("MSG91_AUTHKEY आणि MSG91_TEMPLATE_ID आवश्यक आहेत. Render Environment मध्ये सेट करा.")
        import requests
        payload = {
            "template_id": str(template_id),
            "short_url": "0",
            "recipients": [{"mobiles": "91" + mobile, "var1": otp}]
        }
        headers = {"accept": "application/json", "authkey": str(authkey), "content-type": "application/json"}
        response = requests.post("https://control.msg91.com/api/v5/flow/", json=payload, headers=headers, timeout=20)
        if response.status_code >= 300:
            raise RuntimeError(f"SMS provider error ({response.status_code}): {response.text[:250]}")
        return
    if provider == "twilio":
        account_sid = config_value("TWILIO_ACCOUNT_SID")
        auth_token = config_value("TWILIO_AUTH_TOKEN")
        from_number = config_value("TWILIO_FROM_NUMBER")
        if not all([account_sid, auth_token, from_number]):
            raise RuntimeError("Twilio साठी TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER आवश्यक.")
        from twilio.rest import Client
        Client(str(account_sid), str(auth_token)).messages.create(
            body=f"संत गाडगेबाबा ग्राम स्वच्छता अभियान OTP: {otp}. हा OTP 5 मिनिटांसाठी वैध आहे.",
            from_=str(from_number), to="+91" + mobile
        )
        return
    raise RuntimeError("SMS_PROVIDER म्हणून msg91 किंवा twilio वापरा.")

def create_drive_file(drive, folder_id, filename, content, mime_type="application/json"):
    metadata = {"name": filename, "mimeType": mime_type}
    if folder_id:
        metadata["parents"] = [str(folder_id)]
    media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype=mime_type, resumable=False)
    return drive.files().create(body=metadata, media_body=media, fields="id,name,webViewLink").execute()

def upload_submission_json(gp_code, status, payload):
    folder_id = config_value("DRIVE_FOLDER_ID")
    if not folder_id:
        return ""
    _, drive = get_clients()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{gp_code}_{status}_{stamp}.json"
    result = create_drive_file(drive, folder_id, filename, json.dumps(payload, ensure_ascii=False, indent=2))
    return result.get("webViewLink") or result.get("id", "")

def get_active_gps():
    ss = get_spreadsheet()
    df = read_records(ensure_worksheet(ss, "GP_Master", ["GP Code", "Taluka", "GP Name", "Mobile", "Active"]))
    if df.empty:
        return df
    df.columns = [str(c).strip() for c in df.columns]
    if "Active" in df.columns:
        df = df[df["Active"].astype(str).str.strip().str.lower().isin(["yes", "true", "1", "active", "होय"])]
    return df

def get_questions():
    ss = get_spreadsheet()
    df = read_records(ensure_worksheet(ss, "Questions", ["Section", "Question", "Type", "Required", "Options", "Max Marks"]))
    if df.empty:
        return df
    df = df[df.get("Question", pd.Series(dtype=str)).astype(str).str.strip() != ""]
    return df

def get_responses():
    ss = get_spreadsheet()
    ws = ensure_worksheet(ss, "Responses", RESPONSE_HEADERS)
    df = read_records(ws)
    if not df.empty:
        for c in RESPONSE_HEADERS:
            if c not in df.columns:
                df[c] = ""
    return df

def submit_response(gp, status, submitted_by, mobile, answers, score):
    ss = get_spreadsheet()
    ws = ensure_worksheet(ss, "Responses", RESPONSE_HEADERS)
    payload = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "gp_code": str(gp.get("GP Code", "")),
        "taluka": str(gp.get("Taluka", "")),
        "gp_name": str(gp.get("GP Name", "")),
        "status": status,
        "submitted_by": submitted_by,
        "mobile": mobile,
        "answers": answers,
        "score": score,
    }
    drive_link = ""
    try:
        drive_link = upload_submission_json(payload["gp_code"], status, payload)
    except Exception as exc:
        st.warning(f"Drive फाइल साठवता आली नाही; Sheets मध्ये नोंद करण्याचा प्रयत्न सुरू आहे. तपशील: {exc}")
    ws.append_row([
        payload["timestamp"], payload["gp_code"], payload["taluka"], payload["gp_name"],
        status, submitted_by, mobile, json.dumps(answers, ensure_ascii=False), score, drive_link
    ], value_input_option="USER_ENTERED")
    return payload

def calculate_score(question_df, answers):
    score = 0.0
    for _, q in question_df.iterrows():
        question = str(q.get("Question", "")).strip()
        max_marks = q.get("Max Marks", 0)
        try:
            max_marks = float(max_marks or 0)
        except Exception:
            max_marks = 0
        answer = str(answers.get(question, "")).strip().lower()
        if answer in ("होय", "yes", "पूर्ण", "completed", "आहे", "true"):
            score += max_marks
    return score

def show_login(gps):
    st.subheader("ग्रामपंचायत OTP लॉगिन")
    st.caption("नोंदणीकृत ग्रामपंचायतीच्या अधिकृत मोबाईल क्रमांकावर OTP पाठवला जाईल.")
    mobile_input = st.text_input("नोंदणीकृत मोबाईल क्रमांक", max_chars=15, key="mobile_input")
    if st.button("OTP पाठवा", type="primary", use_container_width=True):
        mobile = normalize_mobile(mobile_input)
        if len(mobile) != 10:
            st.error("कृपया वैध 10 अंकी मोबाईल क्रमांक भरा.")
        else:
            matching = gps[gps["Mobile"].astype(str).apply(normalize_mobile) == mobile] if "Mobile" in gps.columns else pd.DataFrame()
            if matching.empty:
                st.error("हा मोबाईल क्रमांक GP_Master मध्ये नोंदणीकृत नाही. तालुका/जिल्हा प्रशासकांशी संपर्क करा.")
            else:
                otp = f"{random.SystemRandom().randint(0, 999999):06d}"
                try:
                    send_sms_otp(mobile, otp)
                    st.session_state.otp_hash = __import__("hashlib").sha256((otp + str(config_value("OTP_PEPPER", "change-this-secret"))).encode()).hexdigest()
                    st.session_state.otp_mobile = mobile
                    st.session_state.otp_expiry = time.time() + OTP_TTL_SECONDS
                    st.session_state.otp_attempts = 0
                    st.session_state.otp_gp_code = str(matching.iloc[0].get("GP Code", ""))
                    audit_otp(get_spreadsheet(), mobile, "OTP_SENT", "SMS provider accepted request")
                    st.success("OTP पाठवण्याची विनंती यशस्वी. SMS तपासा.")
                except Exception as exc:
                    st.error(f"OTP पाठवता आला नाही: {exc}")
    if st.session_state.get("otp_hash"):
        otp_entered = st.text_input("6 अंकी OTP", max_chars=6, key="otp_entered")
        if st.button("OTP पडताळा", use_container_width=True):
            st.session_state.otp_attempts = st.session_state.get("otp_attempts", 0) + 1
            if time.time() > st.session_state.get("otp_expiry", 0):
                st.error("OTP ची मुदत संपली आहे. पुन्हा OTP मागवा.")
            elif st.session_state.otp_attempts > MAX_OTP_ATTEMPTS:
                st.error("खूप चुकीचे प्रयत्न झाले. पुन्हा OTP मागवा.")
            else:
                import hashlib
                candidate = hashlib.sha256((otp_entered.strip() + str(config_value("OTP_PEPPER", "change-this-secret"))).encode()).hexdigest()
                if candidate == st.session_state.otp_hash:
                    row = gps[gps["GP Code"].astype(str) == st.session_state.otp_gp_code].iloc[0]
                    st.session_state.authenticated_gp = {
                        "GP Code": str(row.get("GP Code", "")),
                        "Taluka": str(row.get("Taluka", "")),
                        "GP Name": str(row.get("GP Name", "")),
                        "Mobile": st.session_state.otp_mobile,
                    }
                    st.session_state.otp_hash = None
                    try:
                        audit_otp(get_spreadsheet(), st.session_state.otp_mobile, "OTP_VERIFIED", "Login successful")
                    except Exception:
                        pass
                    st.rerun()
                else:
                    st.error("OTP चुकीचा आहे.")

def admin_login():
    st.subheader("प्रशासक लॉगिन")
    admin_password = config_value("ADMIN_PASSWORD")
    if not admin_password:
        st.warning("ADMIN_PASSWORD सेट केलेला नाही. Render Environment मध्ये सुरक्षित पासवर्ड सेट करा.")
        return False
    pwd = st.text_input("प्रशासक पासवर्ड", type="password", key="admin_password")
    if st.button("प्रशासक लॉगिन", type="primary"):
        if pwd and pwd == str(admin_password):
            st.session_state.admin_authenticated = True
            st.rerun()
        else:
            st.error("पासवर्ड चुकीचा आहे.")
    return bool(st.session_state.get("admin_authenticated"))

def render_dashboard():
    st.title("प्रशासकीय Dashboard")
    df = get_active_gps()
    responses = get_responses()
    if df.empty:
        st.warning("GP_Master मध्ये ग्रामपंचायतींची नोंद नाही. प्रथम GP_Master टॅब भरा.")
        return
    total = len(df)
    submitted_codes = set()
    draft_codes = set()
    if not responses.empty:
        submitted_codes = set(responses.loc[responses["Status"].astype(str).str.lower() == "submitted", "GP Code"].astype(str))
        draft_codes = set(responses.loc[responses["Status"].astype(str).str.lower() == "draft", "GP Code"].astype(str))
    submitted = len(submitted_codes)
    draft_pending = len(draft_codes - submitted_codes)
    pending = max(total - len(submitted_codes | draft_codes), 0)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("एकूण ग्रामपंचायती", total)
    c2.metric("अंतिम सादर", submitted)
    c3.metric("Draft Pending", draft_pending)
    c4.metric("अद्याप सुरू नाही", pending)
    st.divider()
    gp_df = df.copy()
    gp_df["GP Code"] = gp_df["GP Code"].astype(str)
    gp_df["Status"] = gp_df["GP Code"].apply(lambda x: "Submitted" if x in submitted_codes else ("Draft Pending" if x in draft_codes else "Pending"))
    taluka_summary = gp_df.groupby(["Taluka", "Status"], dropna=False).size().unstack(fill_value=0).reset_index()
    st.subheader("तालुकानिहाय स्थिती")
    st.dataframe(taluka_summary, use_container_width=True, hide_index=True)
    st.subheader("ग्रामपंचायतीनिहाय स्थिती")
    talukas = ["सर्व तालुके"] + sorted(gp_df["Taluka"].dropna().astype(str).unique().tolist())
    selected_taluka = st.selectbox("तालुका निवडा", talukas)
    status_filter = st.selectbox("स्थिती", ["सर्व", "Submitted", "Draft Pending", "Pending"])
    filtered = gp_df.copy()
    if selected_taluka != "सर्व तालुके":
        filtered = filtered[filtered["Taluka"].astype(str) == selected_taluka]
    if status_filter != "सर्व":
        filtered = filtered[filtered["Status"] == status_filter]
    st.dataframe(filtered[["GP Code", "Taluka", "GP Name", "Status"]], use_container_width=True, hide_index=True)
    csv = filtered.to_csv(index=False).encode("utf-8-sig")
    st.download_button("CSV डाउनलोड", csv, "gp_status.csv", "text/csv")
    if not responses.empty:
        st.subheader("अलीकडील प्रतिसाद")
        st.dataframe(responses.tail(100).iloc[::-1], use_container_width=True, hide_index=True)
        csv2 = responses.to_csv(index=False).encode("utf-8-sig")
        st.download_button("सर्व Responses CSV डाउनलोड", csv2, "responses.csv", "text/csv")
    st.caption("Dashboard मधील Submitted स्थिती ही त्या GP Code साठी किमान एक अंतिम नोंद असल्यावर दाखवली जाते.")

def render_gp_form(gp):
    st.title("ग्रामपंचायत स्वमूल्यांकन फॉर्म")
    st.info(f"तालुका: **{gp['Taluka']}**  |  ग्रामपंचायत: **{gp['GP Name']}**  |  GP Code: **{gp['GP Code']}**")
    questions = get_questions()
    if questions.empty:
        st.warning("Questions टॅबमध्ये प्रश्न नाहीत. प्रशासकाने प्रश्नांची नोंद करावी.")
        return
    answers = {}
    current_section = None
    for i, q in questions.iterrows():
        section = str(q.get("Section", "माहिती")).strip() or "माहिती"
        question = str(q.get("Question", "")).strip()
        if not question:
            continue
        if section != current_section:
            st.subheader(section)
            current_section = section
        qtype = str(q.get("Type", "text")).strip().lower()
        required = str(q.get("Required", "No")).strip().lower() in ("yes", "true", "1", "होय", "required")
        options_raw = str(q.get("Options", "")).strip()
        options = [x.strip() for x in options_raw.split("|") if x.strip()]
        key = f"q_{i}_{question}"
        label = question + (" *" if required else "")
        if qtype in ("yesno", "होय/नाही", "boolean"):
            value = st.selectbox(label, ["निवडा", "होय", "नाही"], key=key)
            answers[question] = "" if value == "निवडा" else value
        elif qtype in ("select", "dropdown"):
            opts = ["निवडा"] + options
            value = st.selectbox(label, opts, key=key)
            answers[question] = "" if value == "निवडा" else value
        elif qtype in ("number", "numeric"):
            value = st.number_input(label, min_value=0.0, step=1.0, key=key)
            answers[question] = value
        elif qtype in ("date",):
            value = st.date_input(label, key=key)
            answers[question] = value.isoformat()
        elif qtype in ("textarea", "longtext"):
            answers[question] = st.text_area(label, key=key)
        else:
            answers[question] = st.text_input(label, key=key)
        if required and str(answers[question]).strip() == "":
            st.caption("वरील प्रश्न आवश्यक आहे.")
    submitted_by = st.text_input("फॉर्म भरणाऱ्या अधिकाऱ्याचे नाव")
    confirm = st.checkbox("दिलेली माहिती माझ्या माहितीनुसार बरोबर आहे.")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Draft Save", use_container_width=True):
            try:
                payload = submit_response(gp, "Draft", submitted_by, gp["Mobile"], answers, calculate_score(questions, answers))
                st.success("Draft जतन झाला.")
            except Exception as exc:
                st.error(f"Draft जतन करताना त्रुटी: {exc}")
    with col2:
        if st.button("Final Submit", type="primary", use_container_width=True):
            missing = []
            for _, q in questions.iterrows():
                question = str(q.get("Question", "")).strip()
                required = str(q.get("Required", "No")).strip().lower() in ("yes", "true", "1", "होय", "required")
                if required and not str(answers.get(question, "")).strip():
                    missing.append(question)
            if missing:
                st.error("खालील आवश्यक प्रश्नांची उत्तरे भरा: " + ", ".join(missing[:10]))
            elif not submitted_by.strip():
                st.error("फॉर्म भरणाऱ्या अधिकाऱ्याचे नाव आवश्यक आहे.")
            elif not confirm:
                st.error("अंतिम सादरीकरणासाठी माहिती बरोबर असल्याची पुष्टी करा.")
            else:
                try:
                    submit_response(gp, "Submitted", submitted_by.strip(), gp["Mobile"], answers, calculate_score(questions, answers))
                    st.success("फॉर्म अंतिमरीत्या सादर झाला.")
                    st.balloons()
                except Exception as exc:
                    st.error(f"अंतिम सादरीकरण करताना त्रुटी: {exc}")

def main():
    st.title(APP_TITLE)
    st.caption("ग्रामपंचायत माहिती संकलन व प्रगती निरीक्षण प्रणाली")
    try:
        gps = get_active_gps()
    except Exception as exc:
        st.error(f"Google Sheets कनेक्शन त्रुटी: {exc}")
        st.markdown("**सेटअप तपासा:** `SPREADSHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_JSON`, Google Sheet शेअरिंग आणि Google Drive API.")
        st.stop()
    if gps.empty:
        st.warning("GP_Master टॅबमध्ये ग्रामपंचायत मास्टर डेटा भरा.")
        st.stop()
    page = st.sidebar.radio("मेनू", ["ग्रामपंचायत फॉर्म", "प्रशासकीय Dashboard"])
    if page == "प्रशासकीय Dashboard":
        if admin_login():
            render_dashboard()
        return
    gp = st.session_state.get("authenticated_gp")
    if gp:
        st.sidebar.success(f"{gp['GP Name']} ({gp['Taluka']})")
        if st.sidebar.button("Logout"):
            st.session_state.pop("authenticated_gp", None)
            st.rerun()
        render_gp_form(gp)
    else:
        show_login(gps)

if __name__ == "__main__":
    main()
