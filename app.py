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

def get_results_file_path(session_id: str) -> str:
    """Get the file path for session results"""
    return os.path.join(app.config['RESULTS_FOLDER'], f"{session_id}.json")

def get_session_results(session_id: str) -> list:
    """Get session results from JSON file"""
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
    """Store session results in JSON file"""
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
    """Delete session results JSON file"""
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
    """Clean up result files older than specified days"""
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
        
        # Process each row
        notes = []
        application_name_processed = f"{application_name} - {environment}"
        owners = [requested_for, business_owner, technical_owner]
        owners = [owner for owner in owners if owner and owner != 'nan']
        
        # Validate risk assessment
        if not validate_risk_assessment(risk_assessment):
            notes.append({"message": f"Invalid risk assessment number: {risk_assessment}", "type": "error"})
            skip = False
        else:
            notes.append({"message": f"Valid risk assessment: {risk_assessment}", "type": "success"})
            skip = False
        
        # Determine application configuration
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
    # Clean up old result files on app start
    cleanup_old_results(days=7)
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
            
            # Generate session ID
            session_id = str(uuid.uuid4())
            
            # Store in session (minimal data)
            session['session_id'] = session_id
            session['processed_data'] = processed_data
            session['current_index'] = 0
            session['summary'] = {
                'total': len(processed_data), 
                'processed': 0, 
                'skipped': 0
            }
            session['upload_timestamp'] = timestamp
            session['upload_filename'] = file.filename
            
            # Initialize JSON file for results
            set_session_results(session_id, [])
            
            print(f"✅ Created new session: {session_id}")
            print(f"   Results file: {get_results_file_path(session_id)}")
            
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
    session_id = session.get('session_id')
    
    if current_index < len(processed_data):
        summary = session['summary']
        summary['skipped'] += 1
        session['summary'] = summary
        
        # Store minimal result info in JSON file
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
            print(f"📝 Saved skip result for row {current_index + 1}")
        
        session['current_index'] = current_index + 1
        session.modified = True
    
    return redirect(url_for('review'))

@app.route('/execute_direct', methods=['POST'])
def execute_direct():
    """Start execution directly and return execution ID"""
    try:
        form_data = request.get_json()
        
        if not form_data:
            return jsonify({'success': False, 'error': 'No form data received'}), 400
        
        required_fields = ['app_type', 'app_name', 'owners']
        for field in required_fields:
            if field not in form_data or not form_data[field]:
                return jsonify({'success': False, 'error': f'Missing required field: {field}'}), 400
        
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
                        # Keep only last 100 logs to prevent memory issues
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
                
                # Show summary
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
        
        return jsonify({'success': True, 'execution_id': execution_id})
        
    except Exception as e:
        print(f"Error in execute_direct: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f"Server error: {str(e)}"}), 500

@app.route('/complete_execution/<execution_id>', methods=['POST'])
def complete_execution(execution_id):
    """Complete execution and update session"""
    try:
        if execution_id not in execution_status:
            return jsonify({'success': False, 'error': 'Execution not found'}), 404
        
        exec_data = execution_status[execution_id]
        form_data = exec_data.get('form_data', {})
        
        current_index = session.get('current_index', 0)
        summary = session.get('summary', {'total': 0, 'processed': 0, 'skipped': 0})
        session_id = session.get('session_id')
        
        print(f"DEBUG: Completing execution - Index: {current_index}, Session ID: {session_id}")
        
        # Update minimal summary in session
        summary['processed'] += 1
        
        # Store detailed result in JSON file
        if session_id:
            results = get_session_results(session_id)
            
            # Create result entry with limited log size
            logs = exec_data.get('logs', [])
            # Keep only last 50 logs for summary
            limited_logs = logs[-50:] if len(logs) > 50 else logs
            
            result_entry = {
                'index': current_index + 1,
                'app_name': form_data.get('app_name', 'Unknown'),
                'status': 'success' if exec_data['status'] == 'completed' else 'error',
                'message': f"Successfully created {form_data.get('app_type', 'Unknown')} application" if exec_data['status'] == 'completed' else f"Error: {exec_data.get('error', 'Unknown error')}",
                'details': exec_data.get('result'),
                'execution_logs': limited_logs,
                'timestamp': datetime.now().isoformat()
            }
            results.append(result_entry)
            set_session_results(session_id, results)
            print(f"📝 Saved execution result to JSON, total results: {len(results)}")
        
        # Move to next row
        new_index = current_index + 1
        session['current_index'] = new_index
        session['summary'] = summary
        session.modified = True
        
        # Clean up execution status
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
        print(f"ERROR in complete_execution: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

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

@app.route('/summary')
def summary():
    if 'summary' not in session:
        flash('No summary available. Please upload and process a file first.', 'error')
        return redirect(url_for('index'))
    
    session_id = session.get('session_id')
    
    # Get detailed results from JSON file
    detailed_results = get_session_results(session_id)
    
    summary_data = session['summary']
    summary_data['results'] = detailed_results
    summary_data['session_id'] = session_id
    summary_data['upload_filename'] = session.get('upload_filename', 'Unknown')
    summary_data['upload_timestamp'] = session.get('upload_timestamp', 'Unknown')
    
    print(f"DEBUG: Summary page - Session ID: {session_id}, Results: {len(detailed_results)}")
    
    return render_template('summary.html', summary=summary_data)

@app.route('/download_results/<session_id>')
def download_results(session_id):
    """Download results as JSON file"""
    try:
        results = get_session_results(session_id)
        
        if not results:
            flash('No results found for this session', 'error')
            return redirect(url_for('summary'))
        
        # Create a comprehensive export
        export_data = {
            'session_id': session_id,
            'generated_at': datetime.now().isoformat(),
            'upload_filename': session.get('upload_filename', 'Unknown'),
            'summary': session.get('summary', {}),
            'results': results
        }
        
        # Return as downloadable JSON
        from flask import Response
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
    
    # Clean up JSON file
    if session_id:
        delete_session_results(session_id)
        print(f"🗑️  Cleaned up session: {session_id}")
    
    session.clear()
    
    # Clear execution status
    global execution_status
    execution_status = {}
    
    return redirect(url_for('index'))

@app.route('/debug_session')
def debug_session():
    """Debug session contents"""
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
    """Manual cleanup endpoint"""
    try:
        cleanup_old_results(days=7)
        return jsonify({'success': True, 'message': 'Cleanup completed'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    print("="*60)
    print("Azure AD App Onboarding Tool")
    print("="*60)
    print(f"📁 Upload folder: {app.config['UPLOAD_FOLDER']}")
    print(f"📄 Results folder: {app.config['RESULTS_FOLDER']}")
    print(f"💾 Storage: JSON Files")
    print("="*60)
    app.run(debug=True, threaded=True)