# संत गाडगेबाबा ग्राम स्वच्छता अभियान — Streamlit Starter

Python Streamlit application for GP OTP login, GP form, Draft/Final Submit, Google Sheets response storage, Google Drive JSON snapshots, and administrative dashboard. Deployment target: Render.

## Important before production

- This is a deployment-ready **starter project**, not a certified government production system.
- SMS OTP requires an approved SMS provider account and a DLT-compliant template in India. MSG91 is configured by default; Twilio is also supported.
- Only GP records and mobile numbers in `GP_Master` can log in.
- Keep the Google service-account JSON, SMS credentials, OTP pepper and admin password in Render Environment settings / secrets. Never commit real credentials.
- Before official use, conduct security testing, backups, access-control review, and departmental approval. Add rate limiting at the provider/proxy level if required.
- `Responses` is append-only in this starter. It does not edit an existing draft; multiple draft rows may exist for one GP. Dashboard uses the latest status set by any submitted row, so check the data workflow before official rollout.

## 1. Create Google Sheet

Create a Google Spreadsheet, copy its ID from the URL, and create these tabs with these exact headers in row 1:

### GP_Master
`GP Code | Taluka | GP Name | Mobile | Active`

Add one row per GP. Mobile must be the registered 10-digit mobile number. Set Active to `Yes`.

### Questions
`Section | Question | Type | Required | Options | Max Marks`

Example rows:
- `मूलभूत माहिती | गावात ओला-सुका कचरा वर्गीकरण होते का? | yesno | Yes | | 5`
- `घनकचरा व्यवस्थापन | कचरा संकलनाची पद्धत | select | Yes | घंटागाडी|घरपोच संकलन|इतर | 5`
- `नोंदी | निरीक्षण / अडचणी | textarea | No | | 0`

Supported types: `text`, `textarea`, `number`, `date`, `yesno`, `select`. For select choices, separate options with `|`.

### Responses
`Timestamp | GP Code | Taluka | GP Name | Status | Submitted By | Mobile | AnswersJson | Score | Drive Folder/File`

### OTP_Audit
`Timestamp | Mobile | Event | Details`

The app can create missing tabs, but it does not create the GP master list or official question bank.

## 2. Configure Google access

1. In Google Cloud Console, create/select a project.
2. Enable **Google Sheets API** and **Google Drive API**.
3. Create a service account and download its JSON key.
4. Share the Google Spreadsheet with the service account's `client_email` as **Editor**.
5. Create a Drive folder for submission snapshots and share that folder with the same service-account email as **Editor**.
6. Copy the spreadsheet ID and folder ID.
7. Store the JSON contents in Render as `GOOGLE_SERVICE_ACCOUNT_JSON` secret/environment variable. Paste valid JSON, not a file path.

If you do not want to store JSON as one environment variable, adapt `get_google_creds()` to load a mounted file; the starter uses JSON text.

## 3. Configure SMS OTP (MSG91)

1. Open MSG91 and complete account / sender / DLT setup as applicable in India.
2. Create an approved OTP SMS template that includes a variable such as `##var1##`.
3. Copy Auth Key and Flow Template ID.
4. Set Render environment variables:
   - `SMS_PROVIDER=msg91`
   - `MSG91_AUTHKEY`
   - `MSG91_TEMPLATE_ID`
   - `MSG91_SENDER_ID` (optional in this API payload)
5. Confirm your MSG91 flow expects the variable name `var1` and recipient format `91XXXXXXXXXX`. Provider payload requirements can change; test with a designated number first.

Twilio option: set `SMS_PROVIDER=twilio`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`. This may require an eligible sender / destination setup.

## 4. Deploy to Render

1. Upload this project to a private GitHub repository.
2. In Render, choose **New + → Blueprint** and connect the repository. Render reads `render.yaml`.
3. Enter all environment values marked `sync: false`.
4. For `GOOGLE_SERVICE_ACCOUNT_JSON`, paste the full service-account JSON text as one value.
5. Set a strong `ADMIN_PASSWORD`; do not use the example.
6. Deploy. Open the service URL and test:
   - GP login OTP to a registered test mobile
   - Draft Save and Final Submit
   - Google Sheets `Responses` tab
   - Google Drive snapshot file
   - Admin dashboard and CSV exports

Website: https://render.com/

## 5. Run locally

```bash
python -m venv .venv
# Windows:
.venv\\Scripts\\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

For local secrets, create `.streamlit/secrets.toml` from the example and fill in credentials. Do not commit it.

## 6. Data and access notes

- Dashboard password is a single shared admin password. For production, prefer named admin accounts and role-based access.
- OTP has a five-minute expiry and maximum five verification attempts per browser session, but this starter does not implement robust per-IP/per-mobile throttling or a distributed OTP store. Add those before broad public rollout.
- A logged-in session is held in Streamlit session state. Logout or close the browser when using a shared computer.
- Drive snapshot creation is optional: if `DRIVE_FOLDER_ID` is empty, responses still save to Sheets.
- Dashboard status is based on response rows; this starter can show Draft Pending if a draft exists and no submitted response exists.
- For production, validate and review the campaign's final official question bank, scoring rules, data-retention policy, and consent notice before collection.
