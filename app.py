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
from fetch_ritm_details import ServiceNowClient, SERVICENOW_BASE_URL, SERVICENOW_USERNAME, SERVICENOW_PASSWORD, USER_REFERENCE_FIELDS, CATALOG_VARIABLES

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
        # New format: {name, question, value, display_value, order}
        dv = var_data.get('display_value', '')
        v = var_data.get('value', '')
        return dv if dv else v
    return str(var_data) if var_data else ''


def _resolve_ritm_field(ritm_data, field):
    """Extract a scalar string from a RITM field that may be a {value, display_value} dict."""
    obj = ritm_data.get(field, '')
    if isinstance(obj, dict):
        return obj.get('display_value') or obj.get('value') or ''
    return str(obj) if obj else ''

def _resolve_person(var_name, variables, client):
    """Resolve a user reference field to {name, email, display} dict."""
    v = variables.get(var_name, {})
    raw_val = v.get('value', '') if isinstance(v, dict) else str(v)
    display_val = v.get('display_value', '') if isinstance(v, dict) else ''
    result = {'name': display_val or raw_val, 'email': '', 'sys_id': raw_val}
    if raw_val and len(str(raw_val)) == 32:  # SNOW sys_id
        user = client.get_user_details(raw_val)
        if user:
            result['name'] = user.get('name', display_val)
            result['email'] = user.get('email', '')
    return result


def build_form_data_from_ritm(ritm_data, variables, client):
    """
    Map ServiceNow RITM fields → app onboarding form fields.
    Returns a rich dict ready to be passed to the template / JS.
    """
    def get_var(name):
        return _resolve_var(variables.get(name, {}))

    # ── Resolve people ──────────────────────────────────────────
    people = {}
    for field in ['requested_for', 'requested_by', 'business_owner', 'technical_owner']:
        people[field] = _resolve_person(field, variables, client)

    # Requested for from RITM header (fallback)
    if not people['requested_for']['name']:
        req_for_obj = ritm_data.get('requested_for', {})
        if isinstance(req_for_obj, dict):
            people['requested_for']['name'] = req_for_obj.get('display_value', 'N/A')
        else:
            people['requested_for']['name'] = str(req_for_obj) if req_for_obj else 'N/A'

    # ── App details ───────────────────────────────────────────
    app_name_raw = get_var('app_name')
    environment = get_var('environment')
    processed_app_name = f"{app_name_raw} - {environment}" if environment else app_name_raw

    auth_method = get_var('auth_method')
    app_type_raw = get_var('app_type')  # 'App Registration' / 'Enterprise Application'

    # Suggest SAML vs OIDC
    if 'saml' in auth_method.lower() or 'sso' in auth_method.lower() \
            or 'enterprise' in app_type_raw.lower():
        suggested_app_type = 'SAML'
    else:
        suggested_app_type = 'OIDC'

    tenant_scope = get_var('tenant_scope')
    suggested_tenant = 'Multi-Tenant' if 'multi' in tenant_scope.lower() else 'Single-Tenant'

    # Redirect URIs
    redirect_uris_raw = get_var('redirect_uris_list')
    redirect_urls = [u.strip() for u in redirect_uris_raw.replace(',', '\n').splitlines() if u.strip()]

    # Owners (deduped emails)
    owners = []
    for field in ['requested_for', 'business_owner', 'technical_owner']:
        email = people[field].get('email', '')
        if email and email not in owners:
            owners.append(email)

    # ── RITM meta ─────────────────────────────────────────────
    ritm_number = _resolve_ritm_field(ritm_data, 'number')
    ritm_sys_id_obj = ritm_data.get('sys_id', {})
    ritm_sys_id_val = ritm_sys_id_obj.get('value', '') if isinstance(ritm_sys_id_obj, dict) else str(ritm_sys_id_obj)
    ritm_url = f"{SERVICENOW_BASE_URL}/nav_to.do?uri=sc_req_item.do?sys_id={ritm_sys_id_val}" if ritm_sys_id_val else ''
    ritm_state = _resolve_ritm_field(ritm_data, 'state')
    ritm_opened = _resolve_ritm_field(ritm_data, 'opened_at')
    ritm_priority = _resolve_ritm_field(ritm_data, 'priority')

    # Catalog item name
    cat_item_obj = ritm_data.get('cat_item', {})
    if isinstance(cat_item_obj, dict):
        catalog_item = cat_item_obj.get('display_value') or cat_item_obj.get('value') or 'N/A'
    else:
        catalog_item = str(cat_item_obj) if cat_item_obj else 'N/A'

    # ── All SNOW variables flat snapshot ─────────────────────
    snow_vars_flat = {k: _resolve_var(v) for k, v in variables.items()}

    # ── Notes (validation) ────────────────────────────────────
    notes = []
    risk_assessment = get_var('risk_assessment')
    import re as _re
    if _re.match(r'^(?!12345$)(?!.*(\d)\1{4})\d{5}$', str(risk_assessment)):
        notes.append({'message': f'Risk Assessment # found: {risk_assessment}', 'type': 'success'})
    elif risk_assessment:
        notes.append({'message': f'Risk Assessment # value looks unusual: {risk_assessment} — verify before submitting', 'type': 'warning'})
    else:
        notes.append({'message': 'Risk Assessment # is missing — enter manually', 'type': 'error'})

    if suggested_app_type == 'SAML':
        notes.append({'message': f'Application Type: Enterprise Application (SAML)', 'type': 'info'})
        if get_var('saml_acs_url'):
            notes.append({'message': f'SAML ACS URL found in RITM', 'type': 'success'})
        else:
            notes.append({'message': 'SAML ACS URL not provided in RITM — enter manually', 'type': 'warning'})
        if get_var('saml_entity_id'):
            notes.append({'message': 'SAML Entity ID found in RITM', 'type': 'success'})
        else:
            notes.append({'message': 'SAML Entity ID not provided in RITM — enter manually', 'type': 'warning'})
    else:
        notes.append({'message': f'Application Type: App Registration (OIDC)', 'type': 'info'})
        if redirect_urls:
            notes.append({'message': f'{len(redirect_urls)} redirect URI(s) found in RITM', 'type': 'success'})
        else:
            if get_var('redirect_uris_required').lower() == 'yes':
                notes.append({'message': 'Redirect URIs required but not listed — enter manually', 'type': 'warning'})

    if get_var('admin_consent').lower() == 'yes':
        notes.append({'message': 'Admin Consent Required — note this for post-creation step', 'type': 'warning'})

    return {
        # ── Form fields (right panel) ───────────────────────
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
        # OIDC specific
        'api_permissions_required': get_var('api_permissions_required'),
        'api_permissions_details': get_var('api_permissions_details'),
        'expose_apis': get_var('expose_apis'),
        'expose_apis_details': get_var('expose_apis_details'),
        'redirect_uris_required': get_var('redirect_uris_required'),
        'tenant_scope': tenant_scope,
        'cert_secret_required': get_var('cert_secret_required'),
        # SAML specific
        'saml_entity_id': get_var('saml_entity_id'),
        'saml_acs_url': get_var('saml_acs_url'),
        'saml_attributes': get_var('saml_attributes'),
        'metadata_xml_available': get_var('metadata_xml_available'),
        'users_assigned': get_var('users_assigned'),
        'users_assigned_list': get_var('users_assigned_list'),
        # General
        'purpose': get_var('purpose'),
        'additional_info': get_var('additional_info'),
        'admin_consent': get_var('admin_consent'),
        'location': get_var('location'),
        'contact_mode': get_var('contact_mode'),
        # ── RITM metadata (left panel) ──────────────────────
        'ritm_number': ritm_number,
        'ritm_url': ritm_url,
        'ritm_state': ritm_state,
        'ritm_opened': ritm_opened,
        'ritm_priority': ritm_priority,
        'catalog_item': catalog_item,
        # ── People (resolved) ───────────────────────────────
        'people': people,
        'requested_for_name': people['requested_for']['name'],
        'requested_for_email': people['requested_for']['email'],
        'requested_by_name': people['requested_by']['name'],
        'requested_by_email': people['requested_by']['email'],
        'business_owner_name': people['business_owner']['name'],
        'business_owner_email': people['business_owner']['email'],
        'technical_owner_name': people['technical_owner']['name'],
        'technical_owner_email': people['technical_owner']['email'],
        # ── Validation notes ────────────────────────────────
        'notes': notes,
        # ── Raw SNOW vars snapshot ──────────────────────────
        'snow_variables': snow_vars_flat,
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
                users_list = [u.strip() for u in form_data.get('users_assignment', '').splitlines() if u.strip()]
                groups_list = [g.strip() for g in form_data.get('groups_assignment', '').splitlines() if g.strip()]
                result = entra_client.create_enterprise_application(
                    name=form_data['app_name'],
                    owner_emails=form_data['owners'],
                    gallery_mode=form_data.get('gallery_app', False),
                    gallery_name=form_data.get('gallery_name', '') if form_data.get('gallery_app', False) else None,
                    entity_id=form_data.get('saml_entity_id', ''),
                    acs_url=form_data.get('saml_acs_url', ''),
                    users=users_list,
                    groups=groups_list
                )
                # Enrich SAML result with full Entra enterprise app URL
                if result and 'service_principal' in result:
                    sp = result['service_principal']
                    sp_id = sp.get('id', '')
                    app_id_val = sp.get('app_id', '')
                    if sp_id and app_id_val:
                        result['sp_portal_url'] = (
                            f"https://entra.microsoft.com/#view/Microsoft_AAD_IAM/ManagedAppMenuBlade/~/Overview"
                            f"/objectId/{sp_id}/appId/{app_id_val}"
                            f"/preferredSingleSignOnMode/saml/servicePrincipalType/Application/fromNav/"
                        )
                    elif sp_id:
                        result['sp_portal_url'] = (
                            f"https://entra.microsoft.com/#view/Microsoft_AAD_IAM/ManagedAppMenuBlade/~/Overview/objectId/{sp_id}"
                        )

            elif form_data['app_type'] == 'OIDC':
                add_log(f"🔑 Creating OIDC App Registration: {form_data['app_name']}", "info")
                update_progress("Creating OIDC App Registration...", 60)
                tenant_param = "multiple" if "multi" in form_data.get('tenant', 'single').lower() else "single"
                redirect_input = form_data.get('redirect_urls', [])
                if isinstance(redirect_input, str):
                    redirect_input = [u.strip() for u in redirect_input.splitlines() if u.strip()]
                api_perms = form_data.get('api_permissions', [])
                users_list = [u.strip() for u in form_data.get('users_assignment', '').splitlines() if u.strip()]
                groups_list = [g.strip() for g in form_data.get('groups_assignment', '').splitlines() if g.strip()]
                result = entra_client.create_app_registration(
                    name=form_data['app_name'],
                    owner_emails=form_data['owners'],
                    tenant_type=tenant_param,
                    redirect_urls=redirect_input,
                    api_permissions=api_perms if api_perms else None,
                    users=users_list if users_list else None,
                    groups=groups_list if groups_list else None
                )
                # Enrich OIDC result with Entra portal URLs
                if result and 'app_registration' in result:
                    ar = result['app_registration']
                    client_id_val = ar.get('client_id', '')
                    if client_id_val:
                        result['app_reg_portal_url'] = (
                            f"https://entra.microsoft.com/#view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/Overview/appId/{client_id_val}"
                        )
                if result and 'service_principal' in result:
                    sp = result['service_principal']
                    sp_id = sp.get('id', '')
                    # appId is on the SP object returned from Graph API
                    sp_app_id = sp.get('appId', '') or result.get('app_registration', {}).get('client_id', '')
                    if sp_id and sp_app_id:
                        result['sp_portal_url'] = (
                            f"https://entra.microsoft.com/#view/Microsoft_AAD_IAM/ManagedAppMenuBlade/~/Overview"
                            f"/objectId/{sp_id}/appId/{sp_app_id}/servicePrincipalType/Application/fromNav/"
                        )
                    elif sp_id:
                        result['sp_portal_url'] = (
                            f"https://entra.microsoft.com/#view/Microsoft_AAD_IAM/ManagedAppMenuBlade/~/Overview/objectId/{sp_id}"
                        )

            add_log("🎉 Application creation completed successfully!", "success")
            update_progress("Finalizing...", 90)

            if result:
                if 'app_registration' in result:
                    ar = result['app_registration']
                    add_log(f"📋 OIDC App Registration Details:", "info")
                    add_log(f"   Client ID: {ar.get('client_id', 'N/A')}", "info")
                    add_log(f"   Object ID: {ar.get('id', 'N/A')}", "info")
                    if ar.get('owners_added'):
                        add_log(f"👥 Owners: {', '.join(ar['owners_added'])}", "success")
                    if result.get('assigned_users'):
                        add_log(f"👤 Assigned Users: {', '.join(result['assigned_users'])}", "success")
                    if result.get('assigned_groups'):
                        add_log(f"👥 Assigned Groups: {', '.join(result['assigned_groups'])}", "success")
                    if ar.get('api_permissions_added'):
                        add_log(f"🔑 API Permissions: {', '.join(ar['api_permissions_added'])}", "success")
                    if result.get('app_reg_portal_url'):
                        add_log(f"🔗 App Registration (Entra): {result['app_reg_portal_url']}", "info")
                    if result.get('sp_portal_url'):
                        add_log(f"🔗 Enterprise App (Entra): {result['sp_portal_url']}", "info")
                elif 'service_principal' in result:
                    sp = result['service_principal']
                    add_log(f"📋 SAML Enterprise Application Details:", "info")
                    add_log(f"   App ID: {sp.get('app_id', 'N/A')}", "info")
                    add_log(f"   SP Object ID: {sp.get('id', 'N/A')}", "info")
                    if sp.get('owners_added'):
                        add_log(f"👥 Owners: {', '.join(sp['owners_added'])}", "success")
                    if sp.get('assigned_users'):
                        add_log(f"👤 Assigned Users: {', '.join(sp['assigned_users'])}", "success")
                    if sp.get('assigned_groups'):
                        add_log(f"👥 Assigned Groups: {', '.join(sp['assigned_groups'])}", "success")
                    if sp.get('entity_id'):
                        add_log(f"⚙️  Entity ID: {sp['entity_id']}", "info")
                    if sp.get('acs_url'):
                        add_log(f"⚙️  ACS URL: {sp['acs_url']}", "info")
                    if result.get('federated_metadata_url'):
                        add_log(f"🔗 Federation Metadata XML: {result['federated_metadata_url']}", "info")
                    if result.get('sp_portal_url'):
                        add_log(f"🔗 Enterprise App (Entra): {result['sp_portal_url']}", "info")


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
            ritm_data['sys_id'].get('value', '')
            if isinstance(ritm_data.get('sys_id'), dict)
            else str(ritm_data.get('sys_id', ''))
        )

        # Single combined call: discover variables then fetch values
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


@app.route('/history')
def history():
    """History page — records stored in browser localStorage, served as SPA."""
    return render_template('history.html')


if __name__ == '__main__':
    print("=" * 60)
    print("Azure AD App Onboarding Tool  [+ ServiceNow RITM Integration]")
    print("=" * 60)
    print(f"Upload folder  : {app.config['UPLOAD_FOLDER']}")
    print(f"Results folder : {app.config['RESULTS_FOLDER']}")
    print(f"Storage        : JSON Files")
    print("=" * 60)
    app.run(host="0.0.0.0", port=5000, debug=True, threaded=True)