"""
ServiceNow RITM Details Fetcher - FIXED VERSION
Based on the working diagnostic version (test.py).
"""

import requests
import json
from requests.auth import HTTPBasicAuth
from datetime import datetime

# ServiceNow Configuration
SERVICENOW_BASE_URL = "https://dev265148.service-now.com"
SERVICENOW_USERNAME = "1"
SERVICENOW_PASSWORD = "[cO=OVUsl]e}bC{nQM_.6SAWMG<py-n{!g%VA3p}k-<SA72C]@$cC$aHzAn)XZYpdgueaF!@J%c]6q&QF&pb0e5QV{5=Lu?wUa@C"

# Reference fields that need user resolution
USER_REFERENCE_FIELDS = ['requested_for', 'requested_by', 'business_owner', 'technical_owner']
LOCATION_FIELDS = ['location']

# All expected catalog variable names (for ordering / display)
CATALOG_VARIABLES = [
    'requested_for', 'location', 'requested_by', 'contact_mode',
    'app_name', 'app_type', 'purpose', 'risk_assessment',
    'business_owner', 'technical_owner', 'auth_method', 'environment',
    'admin_consent', 'additional_info',
    'api_permissions_required', 'api_permissions_details',
    'expose_apis', 'expose_apis_details',
    'redirect_uris_required', 'redirect_uris_list',
    'tenant_scope', 'cert_secret_required',
    'users_assigned', 'users_assigned_list',
    'metadata_xml_available', 'saml_entity_id', 'saml_acs_url', 'saml_attributes',
]


class ServiceNowClient:
    """Client for interacting with ServiceNow API"""

    def __init__(self, base_url, username, password):
        self.base_url = base_url.rstrip('/')
        self.auth = HTTPBasicAuth(username, password)
        self.headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
        self.session = requests.Session()
        self.session.auth = self.auth
        self.session.headers.update(self.headers)
        self.variable_map = {}  # sys_id -> {name, question, type, order}

    # ------------------------------------------------------------------
    # User / location resolution
    # ------------------------------------------------------------------

    def get_user_details(self, user_sys_id):
        """Fetch user details by sys_id (32-char SNOW id)."""
        if not user_sys_id or len(str(user_sys_id).strip()) != 32:
            return None
        url = f"{self.base_url}/api/now/table/sys_user/{user_sys_id}"
        params = {'sysparm_fields': 'name,email,user_name,phone'}
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            if data.get('result'):
                user = data['result']
                return {
                    'name': user.get('name', 'N/A'),
                    'email': user.get('email', 'N/A'),
                    'user_name': user.get('user_name', 'N/A'),
                    'phone': user.get('phone', 'N/A'),
                }
        except Exception:
            pass
        return None

    def get_location_details(self, location_sys_id):
        """Fetch location name by sys_id."""
        if not location_sys_id or len(str(location_sys_id).strip()) != 32:
            return None
        url = f"{self.base_url}/api/now/table/cmn_location/{location_sys_id}"
        params = {'sysparm_fields': 'name,full_name'}
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            if data.get('result'):
                loc = data['result']
                return loc.get('full_name') or loc.get('name', 'N/A')
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------
    # RITM fetch  (exact match — same as test.py)
    # ------------------------------------------------------------------

    def get_ritm_by_number(self, ritm_number):
        """Fetch RITM by number. Returns raw result dict (sysparm_display_value=all)."""
        url = f"{self.base_url}/api/now/table/sc_req_item"
        params = {
            'sysparm_query': f'number={ritm_number}',
            'sysparm_display_value': 'all',
            'sysparm_exclude_reference_link': 'true',
        }
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            if data.get('result') and len(data['result']) > 0:
                return data['result'][0]
            else:
                print(f"No RITM found: {ritm_number}")
                return None
        except Exception as e:
            print(f"Error fetching RITM: {e}")
            return None

    # ------------------------------------------------------------------
    # Variable discovery  (same as test.py)
    # ------------------------------------------------------------------

    def discover_catalog_item_variables(self, ritm_sys_id):
        """
        Step 1: Load variable definitions from item_option_new for the
        catalog item linked to this RITM.
        Populates self.variable_map: {option_sys_id -> {name, question, type, order}}
        """
        self.variable_map = {}
        try:
            # Get cat_item sys_id from the RITM record
            ritm_url = f"{self.base_url}/api/now/table/sc_req_item/{ritm_sys_id}"
            ritm_params = {'sysparm_fields': 'cat_item', 'sysparm_display_value': 'all'}
            response = self.session.get(ritm_url, params=ritm_params, timeout=30)
            response.raise_for_status()
            ritm_data = response.json().get('result', {})

            cat_item = ritm_data.get('cat_item', {})
            cat_item_sys_id = cat_item.get('value') if isinstance(cat_item, dict) else cat_item

            if not cat_item_sys_id:
                return

            # Fetch variable definitions for this catalog item
            var_url = f"{self.base_url}/api/now/table/item_option_new"
            var_params = {
                'sysparm_query': f'cat_item={cat_item_sys_id}',
                'sysparm_fields': 'sys_id,name,question_text,type,order',
                'sysparm_limit': 1000,
            }
            var_response = self.session.get(var_url, params=var_params, timeout=30)
            var_response.raise_for_status()
            var_data = var_response.json()

            for var in var_data.get('result', []):
                sys_id = var.get('sys_id')
                name = var.get('name')
                question = var.get('question_text', name)
                if sys_id and name:
                    self.variable_map[sys_id] = {
                        'name': name,
                        'question': question,
                        'type': var.get('type'),
                        'order': int(var.get('order') or 0),
                    }
        except Exception as e:
            print(f"Warning: Could not discover catalog variables: {e}")

    # ------------------------------------------------------------------
    # Variable value fetching  (same logic as test.py — mtom first, then sc_item_option)
    # ------------------------------------------------------------------

    def get_ritm_variables_direct(self, ritm_sys_id):
        """
        Step 2: Fetch actual variable values for this RITM.
        Mirrors test.py exactly:
          Method 1 – sc_item_option_mtom  (look up each sc_item_option record)
          Method 2 – sc_item_option directly  (supplements/fills gaps)
        Returns dict: {var_name: {name, question, value, display_value, order}}
        """
        variables = {}

        # ── Method 1: sc_item_option_mtom ────────────────────────────
        url1 = f"{self.base_url}/api/now/table/sc_item_option_mtom"
        params1 = {
            'sysparm_query': f'request_item={ritm_sys_id}',
            'sysparm_display_value': 'all',
            'sysparm_limit': 1000,
        }
        try:
            response = self.session.get(url1, params=params1, timeout=30)
            response.raise_for_status()
            data = response.json()

            for record in data.get('result', []):
                sc_item_option = record.get('sc_item_option', {})
                if isinstance(sc_item_option, dict):
                    var_sys_id = sc_item_option.get('value', '')
                    if var_sys_id and len(var_sys_id) == 32:
                        var_details = self.get_variable_details(var_sys_id)
                        if var_details and var_details.get('name'):
                            variables[var_details['name']] = var_details
        except Exception as e:
            print(f"Method 1 (mtom) failed: {e}")

        # ── Method 2: sc_item_option directly (supplements method 1) ─
        url2 = f"{self.base_url}/api/now/table/sc_item_option"
        params2 = {
            'sysparm_query': f'request_item={ritm_sys_id}',
            'sysparm_display_value': 'all',
            'sysparm_limit': 1000,
        }
        try:
            response = self.session.get(url2, params=params2, timeout=30)
            response.raise_for_status()
            data = response.json()

            for record in data.get('result', []):
                # item_option_new points to the option definition
                item_option_new = record.get('item_option_new', {})
                var_sys_id = item_option_new.get('value') if isinstance(item_option_new, dict) else item_option_new

                # Resolve name from variable_map; fall back to display_value of the reference
                if var_sys_id and var_sys_id in self.variable_map:
                    var_info = self.variable_map[var_sys_id]
                    var_name = var_info['name']
                    question = var_info['question']
                    order = var_info['order']
                else:
                    var_name = (item_option_new.get('display_value', '')
                                if isinstance(item_option_new, dict) else '') or ''
                    question = var_name
                    order = 999

                # Resolve value
                value_obj = record.get('value', {})
                if isinstance(value_obj, dict):
                    value = value_obj.get('value', '')
                    display_value = value_obj.get('display_value', '')
                else:
                    value = str(value_obj) if value_obj else ''
                    display_value = value

                # Only add if not already captured by method 1
                if var_name and var_name not in variables:
                    variables[var_name] = {
                        'name': var_name,
                        'question': question,
                        'value': value,
                        'display_value': display_value,
                        'order': self.variable_map.get(var_sys_id, {}).get('order', order),
                    }
        except Exception as e:
            print(f"Method 2 (sc_item_option) failed: {e}")

        return variables

    def get_variable_details(self, var_sys_id):
        """
        Fetch a single sc_item_option record and return a variable dict.
        Used by Method 1 (mtom) — same as test.py's get_variable_details.
        """
        url = f"{self.base_url}/api/now/table/sc_item_option/{var_sys_id}"
        params = {'sysparm_display_value': 'all'}
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json().get('result', {})

            # Resolve variable name from the item_option_new reference
            item_option_new = data.get('item_option_new', {})
            if isinstance(item_option_new, dict):
                var_name_ref = item_option_new.get('value', '')
                if var_name_ref and var_name_ref in self.variable_map:
                    var_name = self.variable_map[var_name_ref]['name']
                    question = self.variable_map[var_name_ref]['question']
                    order = self.variable_map[var_name_ref]['order']
                else:
                    var_name = item_option_new.get('display_value', '')
                    question = var_name
                    order = 0
            else:
                var_name = str(item_option_new)
                question = var_name
                order = 0

            # Resolve value
            value_obj = data.get('value', {})
            if isinstance(value_obj, dict):
                value = value_obj.get('value', '')
                display_value = value_obj.get('display_value', '')
            else:
                value = str(value_obj) if value_obj else ''
                display_value = value

            return {
                'name': var_name,
                'question': question,
                'value': value,
                'display_value': display_value,
                'order': order,
            }
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Combined fetch (called by app.py)
    # ------------------------------------------------------------------

    def get_ritm_variables(self, ritm_sys_id):
        """
        Full fetch: discover variable definitions, then fetch values.
        This is the single entry point called by app.py.
        """
        self.discover_catalog_item_variables(ritm_sys_id)
        return self.get_ritm_variables_direct(ritm_sys_id)

    # Backwards-compatible alias
    def get_ritm_variables_alternative(self, ritm_sys_id):
        return self.get_ritm_variables(ritm_sys_id)
