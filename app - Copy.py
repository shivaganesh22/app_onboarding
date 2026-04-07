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

app = Flask(__name__)
app.config['SECRET_KEY'] = 'your-secret-key-here'
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# Ensure upload directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Configuration for Entra client
ENTRA_CONFIG = {
    'tenant_id': '7d320f8a-d592-4dd2-bf25-4acfeb8f2e15',
    'client_id': 'fccf1372-691d-4b33-ba95-fc148c5f23b3',
    'client_secret': 'DlG8Q~FtwMo2u4Xnnp7n4YeZo6yA2TkQf5lOlcLQ'
}

# In-memory storage for execution status (use Redis in production)
execution_status = {}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ['xlsx', 'xls']

def validate_risk_assessment(risk_assessment):
    """Validate risk assessment number"""
    return bool(re.search(r'^(?!12345$)(?!.*(\d)\1{4})\d{5}$', str(risk_assessment)))

def process_excel_data(file_path):
    """Process Excel file and return processed data"""
    df = pd.read_excel(file_path)
    processed_data = []
    
    for index, row in df.iterrows():
        # Extract values 
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
        
        # Process each row (same logic as before)
        notes = []
        application_name_processed = f"{application_name} - {environment}"
        owners = [requested_for, business_owner, technical_owner]
        owners = [owner for owner in owners if owner and owner != 'nan']
        
        # Validate risk assessment
        if not validate_risk_assessment(risk_assessment):
            notes.append({"message": "Invalid risk assessment number", "type": "error"})
            skip = True
        else:
            notes.append({"message": f"Valid risk assessment: {risk_assessment}", "type": "success"})
            skip = False
        
        # Determine application configuration (same logic as before)
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

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        flash('No file selected', 'error')
        return redirect(url_for('index'))
    
    file = request.files['file']
    if file.filename == '':
        flash('No file selected', 'error')
        return redirect(url_for('index'))
    
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f"{timestamp}_{filename}"
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        file.save(file_path)
        
        try:
            processed_data = process_excel_data(file_path)
            session['processed_data'] = processed_data
            session['current_index'] = 0
            session['summary'] = {'total': len(processed_data), 'processed': 0, 'skipped': 0, 'results': []}
            
            return redirect(url_for('review'))
        except Exception as e:
            flash(f'Error processing file: {str(e)}', 'error')
            return redirect(url_for('index'))
    else:
        flash('Invalid file type. Please upload an Excel file (.xlsx or .xls)', 'error')
        return redirect(url_for('index'))

@app.route('/review')
def review():
    if 'processed_data' not in session:
        flash('No data to review. Please upload a file first.', 'error')
        return redirect(url_for('index'))
    
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
        return redirect(url_for('index'))
    
    current_index = session.get('current_index', 0)
    processed_data = session['processed_data']
    
    if current_index < len(processed_data):
        summary = session['summary']
        summary['skipped'] += 1
        summary['results'].append({
            'index': current_index + 1,
            'app_name': processed_data[current_index]['processed_app_name'],
            'status': 'skipped',
            'message': 'Skipped by user'
        })
        session['summary'] = summary
        
        session['current_index'] = current_index + 1
    
    return redirect(url_for('review'))

@app.route('/confirm', methods=['POST'])
def confirm():
    if 'processed_data' not in session:
        return redirect(url_for('index'))
    
    current_index = session.get('current_index', 0)
    processed_data = session['processed_data']
    
    if current_index >= len(processed_data):
        return redirect(url_for('summary'))
    
    # Get form data
    form_data = {
        'app_type': request.form.get('app_type'),
        'app_name': request.form.get('app_name'),
        'gallery_app': request.form.get('gallery_app') == 'yes',
        'gallery_name': request.form.get('gallery_name', ''),
        'tenant': request.form.get('tenant'),
        'redirect_urls': [url.strip() for url in request.form.get('redirect_urls', '').split('\n') if url.strip()],
        'owners': [email.strip() for email in request.form.get('owners', '').split('\n') if email.strip()]
    }
    
    current_row = processed_data[current_index]
    
    return render_template('confirm.html', 
                         row=current_row, 
                         form_data=form_data,
                         current_index=current_index + 1,
                         total_rows=len(processed_data))

@app.route('/execute_direct', methods=['POST'])
def execute_direct():
    """Start execution directly and return execution ID"""
    try:
        # Get JSON data from request
        form_data = request.get_json()
        
        if not form_data:
            return jsonify({'success': False, 'error': 'No form data received'}), 400
        
        # Validate required fields
        required_fields = ['app_type', 'app_name', 'owners']
        for field in required_fields:
            if field not in form_data or not form_data[field]:
                return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400
        
        # Generate unique execution ID
        execution_id = str(uuid.uuid4())
        
        # Store form data for this execution
        execution_status[execution_id] = {
            'status': 'running',
            'logs': [],
            'current_step': 'Initializing...',
            'progress': 0,
            'result': None,
            'error': None,
            'form_data': form_data  # Store form data here
        }
        
        # Start execution in background thread
        def background_execution():
            try:
                # Helper functions
                def add_log(message, log_type='info'):
                    if execution_id in execution_status:
                        execution_status[execution_id]['logs'].append({
                            'timestamp': datetime.now().strftime('%H:%M:%S'),
                            'message': message,
                            'type': log_type
                        })
                
                def update_progress(step, progress):
                    if execution_id in execution_status:
                        execution_status[execution_id]['current_step'] = step
                        execution_status[execution_id]['progress'] = progress
                
                add_log("🚀 Execution started", "info")
                update_progress("Initializing Azure AD connection...", 10)
                time.sleep(1)
                
                # Initialize Entra client WITH logging callback
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
                
                # Test authentication
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
                    
                    if form_data.get('gallery_app', False):
                        gallery_name = form_data.get('gallery_name', form_data['app_name'])
                        add_log(f"🔍 Will search Azure AD Gallery for: {gallery_name}", "info")
                        update_progress("Will search Azure AD Gallery...", 70)
                    else:
                        add_log("🔧 Will create custom SAML enterprise application", "info")
                        update_progress("Will create custom SAML enterprise application...", 70)
                    
                    add_log("📝 Processing owners...", "info")
                    update_progress("Processing owners...", 80)
                    
                    result = entra_client.create_enterprise_application(
                        name=form_data['app_name'],
                        owner_emails=form_data['owners'],
                        gallery_mode=form_data.get('gallery_app', False),
                        gallery_name=form_data.get('gallery_name', '') if form_data.get('gallery_app', False) else None
                    )
                elif form_data['app_type'] == 'OIDC':
                    add_log(f"🔑 Creating OIDC App Registration: {form_data['app_name']}", "info")
                    update_progress("Creating OIDC App Registration...", 60)
                    
                    tenant_type = form_data.get('tenant', 'Single-Tenant')
                    add_log(f"🏢 Tenant Type: {tenant_type}", "info")
                    
                    redirect_urls = form_data.get('redirect_urls', [])
                    if redirect_urls:
                        add_log(f"🔗 Will configure {len(redirect_urls)} redirect URL(s)", "info")
                        update_progress("Will configure redirect URLs...", 70)
                    
                    add_log("📝 Processing owners...", "info")
                    update_progress("Processing owners...", 80)
                    
                    # Fix the tenant type parameter
                    tenant_param = "multiple" if "multi" in tenant_type.lower() else "single"
                    add_log(f"🔧 Using tenant parameter: {tenant_param}", "info")
                    
                    result = entra_client.create_app_registration(
                        name=form_data['app_name'],
                        owner_emails=form_data['owners'],
                        tenant_type=tenant_param,
                        redirect_urls=redirect_urls
                    )
                
                # Final success messages
                add_log("🎉 Application creation completed successfully!", "success")
                
                # Show summary of what was created
                if result:
                    if 'app_registration' in result:
                        add_log(f"📋 App Registration ID: {result['app_registration']['id']}", "info")
                        if result['app_registration'].get('client_id'):
                            add_log(f"🆔 Client ID: {result['app_registration']['client_id']}", "info")
                        
                        owners_added = result['app_registration'].get('owners_added', [])
                        if owners_added:
                            add_log(f"👥 App Registration Owners Added: {', '.join(owners_added)}", "success")
                        else:
                            add_log(f"⚠️ No owners were added to App Registration (users may not exist)", "error")
                    
                    if 'service_principal' in result:
                        add_log(f"📋 SAML Enterprise Application ID: {result['service_principal']['id']}", "info")
                        add_log(f"🆔 Application ID: {result['service_principal']['app_id']}", "info")
                        add_log(f"🔒 SSO Mode: SAML", "info")
                        
                        sp_owners_added = result['service_principal'].get('owners_added', [])
                        if sp_owners_added:
                            add_log(f"👥 SAML Application Owners Added: {', '.join(sp_owners_added)}", "success")
                        else:
                            add_log(f"⚠️ No owners were added to SAML Application (users may not exist)", "error")
                
                update_progress("Execution completed successfully!", 100)
                
                # Mark as completed
                execution_status[execution_id]['status'] = 'completed'
                execution_status[execution_id]['result'] = result
                
                add_log("✅ Execution completed successfully!", "success")
                
            except Exception as e:
                # Handle errors
                add_log(f"❌ Error during execution: {str(e)}", "error")
                execution_status[execution_id]['status'] = 'error'
                execution_status[execution_id]['error'] = str(e)
                update_progress("Failed", 100)
        
        # Start background execution
        thread = threading.Thread(target=background_execution)
        thread.daemon = True
        thread.start()
        
        return jsonify({'success': True, 'execution_id': execution_id})
        
    except Exception as e:
        print(f"Error in execute_direct: {str(e)}")
        return jsonify({'success': False, 'error': f"Server error: {str(e)}"}), 500

# NEW ROUTE: Update session and move to next row
def save_session():
    session.modified = True

# Update the complete_execution route:
@app.route('/complete_execution/<execution_id>', methods=['POST'])
def complete_execution(execution_id):
    """Complete execution and update session"""
    try:
        if execution_id not in execution_status:
            return jsonify({'error': 'Execution not found'}), 404
        
        exec_data = execution_status[execution_id]
        form_data = exec_data.get('form_data', {})
        
        # Get current session data
        current_index = session.get('current_index', 0)
        summary = session.get('summary', {'total': 0, 'processed': 0, 'skipped': 0, 'results': []})
        
        print(f"DEBUG: Before update - Current Index: {current_index}, Summary: {summary}")
        
        # Update summary based on execution result
        if exec_data['status'] == 'completed':
            summary['processed'] += 1
            summary['results'].append({
                'index': current_index + 1,
                'app_name': form_data.get('app_name', 'Unknown'),
                'status': 'success',
                'message': f"Successfully created {form_data.get('app_type', 'Unknown')} application",
                'details': exec_data.get('result'),
                'execution_logs': exec_data.get('logs', [])
            })
        else:
            summary['results'].append({
                'index': current_index + 1,
                'app_name': form_data.get('app_name', 'Unknown'),
                'status': 'error',
                'message': f"Error: {exec_data.get('error', 'Unknown error')}",
                'execution_logs': exec_data.get('logs', [])
            })
        
        # Move to next row
        new_index = current_index + 1
        session['current_index'] = new_index
        session['summary'] = summary
        session.modified = True  # Force session save
        
        print(f"DEBUG: After update - New Index: {new_index}, Summary: {summary}")
        
        # Clean up execution status
        del execution_status[execution_id]
        
        return jsonify({
            'success': True,
            'next_index': new_index,
            'summary': summary,
            'debug': {
                'old_index': current_index,
                'new_index': new_index,
                'total_processed': summary['processed']
            }
        })
        
    except Exception as e:
        print(f"ERROR in complete_execution: {str(e)}")
        return jsonify({'error': str(e)}), 500



@app.route('/execution_status/<execution_id>')
def get_execution_status(execution_id):
    """Get current execution status"""
    try:
        if execution_id in execution_status:
            return jsonify(execution_status[execution_id])
        else:
            return jsonify({'error': 'Execution not found'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/test_connection')
def test_connection():
    """Test Azure AD connection"""
    try:
        # Initialize Entra client
        entra_client = EntraAppOnboarding(
            ENTRA_CONFIG['tenant_id'],
            ENTRA_CONFIG['client_id'],
            ENTRA_CONFIG['client_secret']
        )
        
        # Test authentication
        token = entra_client._get_access_token()
        
        return jsonify({
            'success': True,
            'message': 'Connection successful',
            'token_length': len(token)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/summary')
def summary():
    if 'summary' not in session:
        flash('No summary available. Please upload and process a file first.', 'error')
        return redirect(url_for('index'))
    
    summary_data = session['summary']
    return render_template('summary.html', summary=summary_data)

@app.route('/reset')
def reset():
    session.clear()
    # Clear execution status
    global execution_status
    execution_status = {}
    return redirect(url_for('index'))
@app.route('/debug_session')
def debug_session():
    """Debug session contents"""
    return jsonify({
        'session_contents': dict(session),
        'current_index': session.get('current_index'),
        'summary': session.get('summary'),
        'processed_data_length': len(session.get('processed_data', []))
    })
if __name__ == '__main__':
    app.run(debug=True, threaded=True)