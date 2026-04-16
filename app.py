from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import pandas as pd
import os
import re
from werkzeug.utils import secure_filename
from entra_client import EntraAppOnboarding
import json
from datetime import datetime
import time
import uuid
import threading
from pathlib import Path
import sys

# Add current dir to path so we can import fetch_ritm_details
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fetch_ritm_details import ServiceNowClient, SERVICENOW_BASE_URL, SERVICENOW_USERNAME, SERVICENOW_PASSWORD, USER_REFERENCE_FIELDS

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-change-in-production'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['RESULTS_FOLDER'] = 'static/results'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Ensure directories exist
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['RESULTS_FOLDER'], exist_ok=True)

# Configuration for Entra client
ENTRA_CONFIG = {
    'tenant_id': '7d320f8a-d592-4dd2-bf25-4acfeb8f2e15',
    'client_id': 'fccf1372-691d-4b33-ba95-fc148c5f23b3',
    'client_secret': 'DlG8Q~FtwMo2u4Xnnp7n4YeZo6yA2TkQf5lOlcLQ'  # ⚠️ CHANGE THIS!
}

# In-memory storage for execution status
execution_status = {}

# File lock for thread-safe JSON operations
json_lock = threading.Lock()

# ─────────────────────────────────────────────────────────────────────────────
# Helper utilities
# ─────────────────────────────────────────────────────────────────────────────

def get_results_file_path(session_id: str) -> str:
    return os.path.join(app.config['RESULTS_FOLDER'], f"{session_id}.json")

def get_session_results(session_id: str) -> list:
    if not session_id:
        return []
    file_path = get_results_file_path(session_id)
    try:
        if os.path.exists(file_path):
            with json_lock:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        return []
    except Exception as e:
        print(f"Error reading session results: {e}")
        return []

def set_session_results(session_id: str, results: list):
    if not session_id:
        return
    file_path = get_results_file_path(session_id)
    try:
        with json_lock:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(results, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"Error writing session results: {e}")

def delete_session_results(session_id: str):
    if not session_id:
        return
    file_path = get_results_file_path(session_id)
    try:
        if os.path.exists(file_path):
            with json_lock:
                os.remove(file_path)
            print(f"Deleted session results file: {file_path}")
    except Exception as e:
        print(f"Error deleting session results: {e}")

def cleanup_old_results(days=7):
    try:
        results_folder = Path(app.config['RESULTS_FOLDER'])
        cutoff_time = time.time() - (days * 86400)
        for file_path in results_folder.glob('*.json'):
            if file_path.stat().st_mtime < cutoff_time:
                file_path.unlink()
                print(f"Cleaned up old file: {file_path}")
    except Exception as e:
        print(f"Error cleaning up old results: {e}")

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ['xlsx', 'xls']

def validate_risk_assessment(risk_assessment):
    return bool(re.search(r'^(?!12345$)(?!.*(\d)\1{4})\d{5}$', str(risk_assessment)))

def process_excel_data(file_path):
    df = pd.read_excel(file_path)
    processed_data = []
    for index, row in df.iterrows():
        ticket_number = str(row.iloc[0]) if pd.notna(row.iloc[0]) else ""
        requested_for = str(row.iloc[1]) if pd.notna(row.iloc[1]) else ""
        requested_by = str(row.iloc[2]) if pd.notna(row.iloc[2]) else ""
        application_name = str(row.iloc[3]) if pd.notna(row.iloc[3]) else ""
        application_type = str(row.iloc[4]) if pd.notna(row.iloc[4]) else ""
        risk_assessment = str(row.iloc[5]) if pd.notna(row.iloc[5]) else ""
        business_owner = str(row.iloc[6]) if pd.notna(row.iloc[6]) else ""
        technical_owner = str(row.iloc[7]) if pd.notna(row.iloc[7]) else ""
        authentication_method = str(row.iloc[8]) if pd.notna(row.iloc[8]) else ""
        environment = str(row.iloc[9]) if pd.notna(row.iloc[9]) else ""
        redirect_url = str(row.iloc[10]) if pd.notna(row.iloc[10]) else ""
        tenant = str(row.iloc[11]) if pd.notna(row.iloc[11]) else ""

        notes = []
        application_name_processed = f"{application_name} - {environment}"
        owners = [requested_for, business_owner, technical_owner]
        owners = [owner for owner in owners if owner and owner != 'nan']

        if not validate_risk_assessment(risk_assessment):
            notes.append({"message": f"Invalid risk assessment number: {risk_assessment}", "type": "error"})
            skip = False
        else:
            notes.append({"message": f"Valid risk assessment: {risk_assessment}", "type": "success"})
            skip = False

        suggested_auth_type = "OIDC"
        suggested_app_type = "OIDC"
        suggested_tenant = "Single-Tenant"
        suggested_gallery = False

        if redirect_url and redirect_url != 'nan' and redirect_url.startswith("http"):
            suggested_tenant = tenant if tenant and tenant != 'nan' else "Single-Tenant"
            suggested_auth_type = "OIDC"
            if authentication_method == "SAML/SSO":
                notes.append({"message": "Redirect URL provided but authentication is SAML - please confirm SAML/OIDC", "type": "warning"})
            elif authentication_method == "OATH":
                notes.append({"message": "Everything looks good for OIDC", "type": "success"})
            else:
                notes.append({"message": "No authentication method but redirect URL exists - please confirm SAML/OIDC", "type": "warning"})
        else:
            if authentication_method == "OATH":
                suggested_auth_type = "OIDC"
                if application_type == "App Registration":
                    notes.append({"message": "Everything looks good for OIDC", "type": "success"})
                elif application_type == "Enterprise App":
                    notes.append({"message": "Selected Enterprise App but authentication method is OATH - check OIDC/SAML", "type": "warning"})
                else:
                    notes.append({"message": "No application type but authentication method is OATH - check OIDC/SAML", "type": "warning"})
            elif authentication_method == "SAML/SSO":
                suggested_auth_type = "SAML"
                suggested_app_type = "SAML"
                if application_type == "Enterprise App":
                    notes.append({"message": "Everything looks good for SAML", "type": "success"})
                elif application_type == "App Registration":
                    suggested_tenant = tenant if tenant and tenant != 'nan' else "Single-Tenant"
                    if tenant == "Multi-Tenant":
                        notes.append({"message": "Authentication method is SAML but chosen multi-tenant - please confirm SAML", "type": "warning"})
                    else:
                        notes.append({"message": "App type is App Registration but authentication is SAML - please confirm SAML", "type": "warning"})
                else:
                    notes.append({"message": "No application type but authentication method is SAML - please confirm SAML", "type": "warning"})
            else:
                notes.append({"message": "No authentication method - please choose OIDC/SAML", "type": "error"})

        row_data = {
            'index': index,
            'ticket_number': ticket_number,
            'requested_for': requested_for,
            'requested_by': requested_by,
            'application_name': application_name,
            'application_type': application_type,
            'risk_assessment': risk_assessment,
            'business_owner': business_owner,
            'technical_owner': technical_owner,
            'authentication_method': authentication_method,
            'environment': environment,
            'redirect_url': redirect_url,
            'tenant': tenant,
            'notes': notes,
            'skip': skip,
            'processed_app_name': application_name_processed,
            'owners': owners,
            'suggested_auth_type': suggested_auth_type,
            'suggested_app_type': suggested_app_type,
            'suggested_tenant': suggested_tenant,
            'suggested_gallery': suggested_gallery
        }
        processed_data.append(row_data)
    return processed_data


# ─────────────────────────────────────────────────────────────────────────────
# ServiceNow helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_snow_client():
    return ServiceNowClient(SERVICENOW_BASE_URL, SERVICENOW_USERNAME, SERVICENOW_PASSWORD)


def _resolve_var(var_data):
    """Return the best display string from a variable dict."""
    if isinstance(var_data, dict):
        return var_data.get('display_value') or var_data.get('value') or ''
    return str(var_data) if var_data else ''


def build_form_data_from_ritm(ritm_data, variables, client):
    """
    Map ServiceNow RITM fields → app onboarding form fields.
    Returns a dict ready to be passed to the template / JS.
    """
    def get_var(name):
        return _resolve_var(variables.get(name, {}))

    def get_var_raw(name):
        v = variables.get(name, {})
        if isinstance(v, dict):
            return v.get('value') or v.get('display_value') or ''
        return str(v) if v else ''

    # Resolve user emails for owner fields
    def resolve_email(var_name):
        v = variables.get(var_name, {})
        raw_val = v.get('value', '') if isinstance(v, dict) else str(v)
        email = ''
        if raw_val and len(str(raw_val)) == 32:
            user_details = client.get_user_details(raw_val)
            if user_details:
                email = user_details.get('email', '')
                if not email:
                    name = user_details.get('name', '')
                    return name  # fall back to name
                return email
        # Try display_value directly — might already be an email
        dv = v.get('display_value', '') if isinstance(v, dict) else ''
        return dv or raw_val

    # App name + environment
    app_name_raw = get_var('app_name')
    environment = get_var('environment')
    processed_app_name = f"{app_name_raw} - {environment}" if environment else app_name_raw

    # Auth method → suggest SAML or OIDC
    auth_method = get_var('auth_method')
    app_type_raw = get_var('app_type')
    if 'saml' in auth_method.lower() or 'sso' in auth_method.lower():
        suggested_app_type = 'SAML'
    else:
        suggested_app_type = 'OIDC'

    # Tenant scope
    tenant_scope = get_var('tenant_scope')
    if 'multi' in tenant_scope.lower():
        suggested_tenant = 'Multi-Tenant'
    else:
        suggested_tenant = 'Single-Tenant'

    # Redirect URIs
    redirect_uris_raw = get_var('redirect_uris_list')
    redirect_urls = [u.strip() for u in redirect_uris_raw.replace(',', '\n').splitlines() if u.strip()]

    # Owners
    owners = []
    for field in ['requested_for', 'business_owner', 'technical_owner']:
        email = resolve_email(field)
        if email and email not in owners:
            owners.append(email)

    # Risk assessment
    risk_assessment = get_var('risk_assessment')

    # RITM meta
    ritm_number = ritm_data.get('number', '')
    ritm_state = ritm_data.get('state', 'N/A')
    ritm_opened = ritm_data.get('opened_at', 'N/A')
    ritm_priority = ritm_data.get('priority', 'N/A')
    ritm_short_desc = ritm_data.get('short_description', '')
    requested_for_name = ritm_data.get('requested_for.name', ritm_data.get('requested_for', 'N/A'))
    catalog_item = ritm_data.get('cat_item.name', ritm_data.get('cat_item', 'N/A'))

    return {
        # Form fields
        'app_name': app_name_raw,
        'processed_app_name': processed_app_name,
        'environment': environment,
        'suggested_app_type': suggested_app_type,
        'suggested_tenant': suggested_tenant,
        'redirect_urls': '\n'.join(redirect_urls),
        'owners': '\n'.join(owners),
        'risk_assessment': risk_assessment,
        'auth_method': auth_method,
        'app_type_raw': app_type_raw,
        'purpose': get_var('purpose'),
        'saml_entity_id': get_var('saml_entity_id'),
        'saml_acs_url': get_var('saml_acs_url'),
        'saml_attributes': get_var('saml_attributes'),
        'api_permissions': get_var('api_permissions_details'),
        'expose_apis': get_var('expose_apis_details'),
        'additional_info': get_var('additional_info'),
        'admin_consent': get_var('admin_consent'),
        # RITM metadata for display
        'ritm_number': ritm_number,
        'ritm_state': ritm_state,
        'ritm_opened': ritm_opened,
        'ritm_priority': ritm_priority,
        'ritm_short_desc': ritm_short_desc,
        'requested_for_name': requested_for_name,
        'catalog_item': catalog_item,
        # Raw variable snapshot for the verification panel
        'snow_variables': {k: _resolve_var(v) for k, v in variables.items()},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Shared execution engine (used by both Excel and new onboard routes)
# ─────────────────────────────────────────────────────────────────────────────

def start_background_execution(form_data: dict) -> str:
    """
    Kick off background Entra app creation.
    Returns execution_id to poll against.
    """
    execution_id = str(uuid.uuid4())
    execution_status[execution_id] = {
        'status': 'running',
        'logs': [],
        'current_step': 'Initializing...',
        'progress': 0,
        'result': None,
        'error': None,
        'form_data': form_data
    }

    def background_execution():
        try:
            def add_log(message, log_type='info'):
                if execution_id in execution_status:
                    logs = execution_status[execution_id]['logs']
                    logs.append({
                        'timestamp': datetime.now().strftime('%H:%M:%S'),
                        'message': message,
                        'type': log_type
                    })
                    if len(logs) > 100:
                        execution_status[execution_id]['logs'] = logs[-100:]

            def update_progress(step, progress):
                if execution_id in execution_status:
                    execution_status[execution_id]['current_step'] = step
                    execution_status[execution_id]['progress'] = progress

            add_log("🚀 Execution started", "info")
            update_progress("Initializing Azure AD connection...", 10)
            time.sleep(1)

            try:
                entra_client = EntraAppOnboarding(
                    ENTRA_CONFIG['tenant_id'],
                    ENTRA_CONFIG['client_id'],
                    ENTRA_CONFIG['client_secret'],
                    log_callback=add_log
                )
                add_log("✅ Azure AD connection established", "success")
                update_progress("Authenticating with Microsoft Graph API...", 20)
                time.sleep(1)
            except Exception as e:
                add_log(f"❌ Failed to initialize Azure AD client: {str(e)}", "error")
                execution_status[execution_id]['status'] = 'error'
                execution_status[execution_id]['error'] = str(e)
                return

            try:
                token = entra_client._get_access_token()
                add_log("✅ Authentication successful", "success")
                update_progress("Validating configuration...", 30)
                time.sleep(1)
            except Exception as e:
                add_log(f"❌ Authentication failed: {str(e)}", "error")
                execution_status[execution_id]['status'] = 'error'
                execution_status[execution_id]['error'] = str(e)
                return

            add_log("✅ Configuration validated", "success")
            update_progress("Creating application...", 50)
            time.sleep(1)

            result = None
            if form_data['app_type'] == 'SAML':
                add_log(f"🛡️  Creating SAML Enterprise Application: {form_data['app_name']}", "info")
                update_progress("Creating SAML Enterprise Application...", 60)
                result = entra_client.create_enterprise_application(
                    name=form_data['app_name'],
                    owner_emails=form_data['owners'],
                    gallery_mode=form_data.get('gallery_app', False),
                    gallery_name=form_data.get('gallery_name', '') if form_data.get('gallery_app', False) else None
                )
            elif form_data['app_type'] == 'OIDC':
                add_log(f"🔑 Creating OIDC App Registration: {form_data['app_name']}", "info")
                update_progress("Creating OIDC App Registration...", 60)
                tenant_param = "multiple" if "multi" in form_data.get('tenant', 'single').lower() else "single"
                result = entra_client.create_app_registration(
                    name=form_data['app_name'],
                    owner_emails=form_data['owners'],
                    tenant_type=tenant_param,
                    redirect_urls=form_data.get('redirect_urls', [])
                )

            add_log("🎉 Application creation completed successfully!", "success")
            update_progress("Finalizing...", 90)

            if result:
                if 'app_registration' in result:
                    app_reg = result['app_registration']
                    add_log(f"📋 App Registration Details:", "info")
                    add_log(f"   Client ID: {app_reg.get('client_id')}", "info")
                    app_owners = app_reg.get('owners_added', [])
                    if app_owners:
                        add_log(f"👥 App Registration Owners: {', '.join(app_owners)}", "success")
                    if 'service_principal' in result:
                        sp = result['service_principal']
                        sp_owners = sp.get('owners_added', [])
                        if sp_owners:
                            add_log(f"👥 Enterprise App Owners: {', '.join(sp_owners)}", "success")
                elif 'service_principal' in result:
                    sp = result['service_principal']
                    add_log(f"📋 SAML Enterprise Application:", "info")
                    add_log(f"   Application ID: {sp.get('app_id', 'N/A')}", "info")
                    sp_owners = sp.get('owners_added', [])
                    if sp_owners:
                        add_log(f"👥 Owners: {', '.join(sp_owners)}", "success")

            update_progress("Execution completed successfully!", 100)
            execution_status[execution_id]['status'] = 'completed'
            execution_status[execution_id]['result'] = result
            add_log("✅ Execution completed successfully!", "success")

        except Exception as e:
            import traceback
            error_details = traceback.format_exc()
            add_log(f"❌ Error during execution: {str(e)}", "error")
            print(f"Error details: {error_details}")
            execution_status[execution_id]['status'] = 'error'
            execution_status[execution_id]['error'] = str(e)
            update_progress("Failed", 100)

    thread = threading.Thread(target=background_execution)
    thread.daemon = True
    thread.start()
    return execution_id


# ─────────────────────────────────────────────────────────────────────────────
# Routes — Landing / Hub
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    cleanup_old_results(days=7)
    return render_template('onboard.html')


# Keep old Excel upload page accessible
@app.route('/excel')
def excel_home():
    return render_template('index.html')


# ─────────────────────────────────────────────────────────────────────────────
# Routes — Excel Upload (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('excel_home'))

    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('excel_home'))

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{timestamp}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)

        try:
            processed_data = process_excel_data(file_path)
            session_id = str(uuid.uuid4())
            session['session_id'] = session_id
            session['processed_data'] = processed_data
            session['current_index'] = 0
            session['summary'] = {'total': len(processed_data), 'processed': 0, 'skipped': 0}
            session['upload_timestamp'] = timestamp
            session['upload_filename'] = file.filename
            set_session_results(session_id, [])
            print(f"✅ Created new session: {session_id}")
            return redirect(url_for('review'))
        except Exception as e:
            flash(f'Error processing file: {str(e)}', 'error')
            return redirect(url_for('excel_home'))
    else:
        flash('Invalid file type. Please upload an Excel file (.xlsx or .xls)', 'error')
        return redirect(url_for('excel_home'))


@app.route('/review')
def review():
    if 'processed_data' not in session:
        flash('No data to review. Please upload a file first.', 'error')
        return redirect(url_for('excel_home'))

    current_index = session.get('current_index', 0)
    processed_data = session['processed_data']

    if current_index >= len(processed_data):
        return redirect(url_for('summary'))

    current_row = processed_data[current_index]
    total_rows = len(processed_data)

    return render_template('review.html',
                           row=current_row,
                           current_index=current_index + 1,
                           total_rows=total_rows)


@app.route('/skip')
def skip():
    if 'processed_data' not in session:
        return redirect(url_for('excel_home'))

    current_index = session.get('current_index', 0)
    processed_data = session['processed_data']
    session_id = session.get('session_id')

    if current_index < len(processed_data):
        summary = session['summary']
        summary['skipped'] += 1
        session['summary'] = summary

        if session_id:
            results = get_session_results(session_id)
            results.append({
                'index': current_index + 1,
                'app_name': processed_data[current_index]['processed_app_name'],
                'status': 'skipped',
                'message': 'Skipped by user',
                'timestamp': datetime.now().isoformat()
            })
            set_session_results(session_id, results)

        session['current_index'] = current_index + 1
        session.modified = True

    return redirect(url_for('review'))


# ─────────────────────────────────────────────────────────────────────────────
# Routes — ServiceNow RITM Onboarding
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/onboard/ritm')
def ritm_onboard():
    """RITM-based onboarding page."""
    form_data = session.pop('ritm_form_data', None)
    return render_template('ritm_onboard.html', form_data=form_data)


@app.route('/fetch_ritm', methods=['POST'])
def fetch_ritm():
    """
    AJAX endpoint: fetch RITM from ServiceNow and return form-ready JSON.
    Body: { "ritm_number": "RITM0012345" }
    """
    try:
        body = request.get_json()
        if not body or not body.get('ritm_number'):
            return jsonify({'success': False, 'error': 'RITM number is required'}), 400

        ritm_number = body['ritm_number'].strip().upper()

        client = _get_snow_client()

        # Fetch RITM record
        ritm_data = client.get_ritm_by_number(ritm_number)
        if not ritm_data:
            return jsonify({'success': False, 'error': f'No RITM found with number: {ritm_number}'}), 404

        ritm_sys_id = (
            ritm_data.get('sys_id') if isinstance(ritm_data.get('sys_id'), str)
            else ritm_data.get('sys_id', {}).get('value', '')
        )

        # Fetch variables (try alternative method first)
        variables = client.get_ritm_variables_alternative(ritm_sys_id)
        if not variables:
            variables = client.get_ritm_variables(ritm_sys_id)

        # Build form data
        form_data = build_form_data_from_ritm(ritm_data, variables, client)

        return jsonify({'success': True, 'form_data': form_data})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Routes — Manual Onboarding
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/onboard/manual')
def manual_onboard():
    """Manual app onboarding form."""
    return render_template('manual_onboard.html')


# ─────────────────────────────────────────────────────────────────────────────
# Routes — Unified Submit (RITM + Manual)
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/onboard/submit', methods=['POST'])
def onboard_submit():
    """
    Start execution for RITM or manual onboarding.
    Body: same shape as /execute_direct
    """
    try:
        form_data = request.get_json()

        if not form_data:
            return jsonify({'success': False, 'error': 'No form data received'}), 400

        required_fields = ['app_type', 'app_name', 'owners']
        for field in required_fields:
            if field not in form_data or not form_data[field]:
                return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400

        execution_id = start_background_execution(form_data)
        return jsonify({'success': True, 'execution_id': execution_id})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Routes — Execution (shared by Excel + Onboard flows)
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/execute_direct', methods=['POST'])
def execute_direct():
    """Start execution directly (Excel flow) and return execution ID."""
    try:
        form_data = request.get_json()

        if not form_data:
            return jsonify({'success': False, 'error': 'No form data received'}), 400

        required_fields = ['app_type', 'app_name', 'owners']
        for field in required_fields:
            if field not in form_data or not form_data[field]:
                return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400

        execution_id = start_background_execution(form_data)
        return jsonify({'success': True, 'execution_id': execution_id})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'Server error: {str(e)}'}), 500


@app.route('/complete_execution/<execution_id>', methods=['POST'])
def complete_execution(execution_id):
    """Complete execution and update session (Excel flow)."""
    try:
        if execution_id not in execution_status:
            return jsonify({'success': False, 'error': 'Execution not found'}), 404

        exec_data = execution_status[execution_id]
        form_data = exec_data.get('form_data', {})

        current_index = session.get('current_index', 0)
        summary = session.get('summary', {'total': 0, 'processed': 0, 'skipped': 0})
        session_id = session.get('session_id')

        summary['processed'] += 1

        if session_id:
            results = get_session_results(session_id)
            logs = exec_data.get('logs', [])
            limited_logs = logs[-50:] if len(logs) > 50 else logs
            result_entry = {
                'index': current_index + 1,
                'app_name': form_data.get('app_name', 'Unknown'),
                'status': 'success' if exec_data['status'] == 'completed' else 'error',
                'message': f"Successfully created {form_data.get('app_type', 'Unknown')} application"
                           if exec_data['status'] == 'completed'
                           else f"Error: {exec_data.get('error', 'Unknown error')}",
                'details': exec_data.get('result'),
                'execution_logs': limited_logs,
                'timestamp': datetime.now().isoformat()
            }
            results.append(result_entry)
            set_session_results(session_id, results)

        new_index = current_index + 1
        session['current_index'] = new_index
        session['summary'] = summary
        session.modified = True

        del execution_status[execution_id]

        has_more_rows = new_index < len(session.get('processed_data', []))

        return jsonify({
            'success': True,
            'next_index': new_index,
            'has_more_rows': has_more_rows,
            'total_rows': len(session.get('processed_data', [])),
            'summary': summary
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/execution_status/<execution_id>')
def get_execution_status(execution_id):
    try:
        if execution_id in execution_status:
            return jsonify(execution_status[execution_id])
        else:
            return jsonify({'error': 'Execution not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# Routes — Summary / Download / Reset / Debug
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/summary')
def summary():
    if 'summary' not in session:
        flash('No summary available. Please upload and process a file first.', 'error')
        return redirect(url_for('index'))

    session_id = session.get('session_id')
    detailed_results = get_session_results(session_id)
    summary_data = session['summary']
    summary_data['results'] = detailed_results
    summary_data['session_id'] = session_id
    summary_data['upload_filename'] = session.get('upload_filename', 'Unknown')
    summary_data['upload_timestamp'] = session.get('upload_timestamp', 'Unknown')

    return render_template('summary.html', summary=summary_data)


@app.route('/download_results/<session_id>')
def download_results(session_id):
    try:
        results = get_session_results(session_id)
        if not results:
            flash('No results found for this session', 'error')
            return redirect(url_for('summary'))

        from flask import Response
        export_data = {
            'session_id': session_id,
            'generated_at': datetime.now().isoformat(),
            'upload_filename': session.get('upload_filename', 'Unknown'),
            'summary': session.get('summary', {}),
            'results': results
        }
        response = Response(
            json.dumps(export_data, indent=2, ensure_ascii=False),
            mimetype='application/json',
            headers={
                'Content-Disposition': f'attachment; filename=results_{session_id}_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json'
            }
        )
        return response
    except Exception as e:
        flash(f'Error downloading results: {str(e)}', 'error')
        return redirect(url_for('summary'))


@app.route('/reset')
def reset():
    session_id = session.get('session_id')
    if session_id:
        delete_session_results(session_id)
        print(f"🗑️  Cleaned up session: {session_id}")
    session.clear()
    global execution_status
    execution_status = {}
    return redirect(url_for('index'))


@app.route('/debug_session')
def debug_session():
    session_id = session.get('session_id')
    results_file_exists = False
    results_file_size = 0
    results_count = 0

    if session_id:
        file_path = get_results_file_path(session_id)
        results_file_exists = os.path.exists(file_path)
        if results_file_exists:
            results_file_size = os.path.getsize(file_path)
            results_count = len(get_session_results(session_id))

    return jsonify({
        'session_id': session_id,
        'current_index': session.get('current_index'),
        'summary': session.get('summary'),
        'processed_data_length': len(session.get('processed_data', [])),
        'results_file_exists': results_file_exists,
        'results_file_size_kb': round(results_file_size / 1024, 2) if results_file_exists else 0,
        'results_count': results_count,
        'execution_status_keys': list(execution_status.keys()),
        'storage_type': 'JSON Files'
    })


@app.route('/cleanup_old_files')
def cleanup_files():
    try:
        cleanup_old_results(days=7)
        return jsonify({'success': True, 'message': 'Cleanup completed'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    print("=" * 60)
    print("Azure AD App Onboarding Tool  [+ ServiceNow RITM Integration]")
    print("=" * 60)
    print(f"📁 Upload folder: {app.config['UPLOAD_FOLDER']}")
    print(f"📄 Results folder: {app.config['RESULTS_FOLDER']}")
    print(f"💾 Storage: JSON Files")
    print("=" * 60)
    app.run(debug=True, threaded=True)