"""
ServiceNow RITM Details Fetcher - DIAGNOSTIC & FIXED VERSION
Automatically discovers catalog item and variable structure
"""

import requests
import json
from requests.auth import HTTPBasicAuth
from datetime import datetime

# ServiceNow Configuration
SERVICENOW_BASE_URL = "https://dev265148.service-now.com"
SERVICENOW_USERNAME = "1"
SERVICENOW_PASSWORD = "[cO=OVUsl]e}bC{nQM_.6SAWMG<py-n{!g%VA3p}k-<SA72C]@$cC$aHzAn)XZYpdgueaF!@J%c]6q&QF&pb0e5QV{5=Lu?wUa@C"

# Reference fields
USER_REFERENCE_FIELDS = ['requested_for', 'requested_by', 'business_owner', 'technical_owner']
LOCATION_FIELDS = ['location']


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
        self.variable_map = {}
    
    def get_user_details(self, user_sys_id):
        """Fetch user details"""
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
                    'phone': user.get('phone', 'N/A')
                }
        except:
            pass
        return None
    
    def get_location_details(self, location_sys_id):
        """Fetch location details"""
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
        except:
            pass
        return None
    
    def get_ritm_by_number(self, ritm_number):
        """Fetch RITM by number"""
        url = f"{self.base_url}/api/now/table/sc_req_item"
        params = {
            'sysparm_query': f'number={ritm_number}',
            'sysparm_display_value': 'all',
            'sysparm_exclude_reference_link': 'true'
        }
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            if data.get('result') and len(data['result']) > 0:
                return data['result'][0]
            else:
                print(f"\n❌ No RITM found: {ritm_number}")
                return None
                
        except Exception as e:
            print(f"\n❌ Error: {e}")
            return None
    
    def discover_catalog_item_variables(self, ritm_sys_id):
        """
        Discover what variables exist for this RITM by looking at actual data
        Then fetch variable definitions
        """
        print("\n🔍 Discovering variables from RITM data...")
        
        # First, get the catalog item sys_id from the RITM
        ritm_url = f"{self.base_url}/api/now/table/sc_req_item/{ritm_sys_id}"
        ritm_params = {'sysparm_fields': 'cat_item', 'sysparm_display_value': 'all'}
        
        try:
            response = self.session.get(ritm_url, params=ritm_params, timeout=30)
            response.raise_for_status()
            ritm_data = response.json().get('result', {})
            
            cat_item = ritm_data.get('cat_item', {})
            cat_item_sys_id = cat_item.get('value') if isinstance(cat_item, dict) else cat_item
            
            if cat_item_sys_id:
                print(f"✓ Catalog Item sys_id: {cat_item_sys_id}")
                
                # Get variable definitions for this catalog item
                var_url = f"{self.base_url}/api/now/table/item_option_new"
                var_params = {
                    'sysparm_query': f'cat_item={cat_item_sys_id}',
                    'sysparm_fields': 'sys_id,name,question_text,type,order',
                    'sysparm_limit': 1000
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
                            'order': int(var.get('order', 0))
                        }
                
                print(f"✓ Loaded {len(self.variable_map)} variable definitions")
                
        except Exception as e:
            print(f"⚠️  Could not discover variables: {e}")
    
    def get_ritm_variables_direct(self, ritm_sys_id):
        """
        Get variables directly using the variable table
        Uses BOTH sc_item_option_mtom and sc_item_option tables
        """
        variables = {}
        
        # Method 1: Try sc_item_option_mtom (many-to-many relationship table)
        url1 = f"{self.base_url}/api/now/table/sc_item_option_mtom"
        params1 = {
            'sysparm_query': f'request_item={ritm_sys_id}',
            'sysparm_display_value': 'all',
            'sysparm_limit': 1000
        }
        
        try:
            print("\n📦 Method 1: Checking sc_item_option_mtom table...")
            response = self.session.get(url1, params=params1, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            for record in data.get('result', []):
                # Get variable reference
                sc_item_option = record.get('sc_item_option', {})
                
                if isinstance(sc_item_option, dict):
                    var_sys_id = sc_item_option.get('value', '')
                    
                    # Now get the actual variable details
                    if var_sys_id and len(var_sys_id) == 32:
                        var_details = self.get_variable_details(var_sys_id)
                        if var_details:
                            variables[var_details['name']] = var_details
            
            print(f"  Found {len(variables)} variables via method 1")
            
        except Exception as e:
            print(f"  Method 1 failed: {e}")
        
        # Method 2: Try sc_item_option directly
        url2 = f"{self.base_url}/api/now/table/sc_item_option"
        params2 = {
            'sysparm_query': f'request_item={ritm_sys_id}',
            'sysparm_display_value': 'all',
            'sysparm_limit': 1000
        }
        
        try:
            print("\n📦 Method 2: Checking sc_item_option table...")
            response = self.session.get(url2, params=params2, timeout=30)
            response.raise_for_status()
            data = response.json()
            
            for record in data.get('result', []):
                # Get item_option_new reference
                item_option_new = record.get('item_option_new', {})
                var_sys_id = item_option_new.get('value') if isinstance(item_option_new, dict) else item_option_new
                
                # Get variable name
                var_name = item_option_new.get('display_value', '') if isinstance(item_option_new, dict) else ''
                
                # If we have the variable definition in our map, use it
                if var_sys_id in self.variable_map:
                    var_info = self.variable_map[var_sys_id]
                    var_name = var_info['name']
                    question = var_info['question']
                else:
                    question = var_name
                
                # Get value
                value_obj = record.get('value', {})
                if isinstance(value_obj, dict):
                    value = value_obj.get('value', '')
                    display_value = value_obj.get('display_value', '')
                else:
                    value = str(value_obj)
                    display_value = str(value_obj)
                
                if var_name and var_name not in variables:
                    variables[var_name] = {
                        'name': var_name,
                        'question': question,
                        'value': value,
                        'display_value': display_value,
                        'order': self.variable_map.get(var_sys_id, {}).get('order', 999)
                    }
            
            print(f"  Found {len(variables)} variables via method 2")
            
        except Exception as e:
            print(f"  Method 2 failed: {e}")
        
        return variables
    
    def get_variable_details(self, var_sys_id):
        """Get details of a specific variable including its value"""
        url = f"{self.base_url}/api/now/table/sc_item_option/{var_sys_id}"
        params = {'sysparm_display_value': 'all'}
        
        try:
            response = self.session.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json().get('result', {})
            
            # Get variable name
            item_option_new = data.get('item_option_new', {})
            if isinstance(item_option_new, dict):
                var_name = item_option_new.get('display_value', '')
                var_name_value = item_option_new.get('value', '')
                
                # Try to get the actual name from our map
                if var_name_value in self.variable_map:
                    var_name = self.variable_map[var_name_value]['name']
                    question = self.variable_map[var_name_value]['question']
                else:
                    question = var_name
            else:
                var_name = str(item_option_new)
                question = var_name
            
            # Get value
            value_obj = data.get('value', {})
            if isinstance(value_obj, dict):
                value = value_obj.get('value', '')
                display_value = value_obj.get('display_value', '')
            else:
                value = str(value_obj)
                display_value = str(value_obj)
            
            return {
                'name': var_name,
                'question': question,
                'value': value,
                'display_value': display_value,
                'order': 0
            }
            
        except Exception as e:
            return None


def display_ritm_details(ritm_data, variables, client):
    """Display RITM details"""
    print("\n" + "="*100)
    print("RITM DETAILS - Azure Entra ID Application Request")
    print("="*100)
    
    # Basic Information
    print(f"\n{'BASIC INFORMATION':-^100}")
    
    number_obj = ritm_data.get('number', {})
    number = number_obj.get('display_value', 'N/A') if isinstance(number_obj, dict) else number_obj
    print(f"RITM Number:         {number}")
    
    state_obj = ritm_data.get('state', {})
    state = state_obj.get('display_value', 'N/A') if isinstance(state_obj, dict) else state_obj
    print(f"State:               {state}")
    
    stage_obj = ritm_data.get('stage', {})
    stage = stage_obj.get('display_value', 'N/A') if isinstance(stage_obj, dict) else stage_obj
    print(f"Stage:               {stage}")
    
    priority_obj = ritm_data.get('priority', {})
    priority = priority_obj.get('display_value', 'N/A') if isinstance(priority_obj, dict) else priority_obj
    print(f"Priority:            {priority}")
    
    opened_obj = ritm_data.get('opened_at', {})
    opened = opened_obj.get('display_value', 'N/A') if isinstance(opened_obj, dict) else opened_obj
    print(f"Opened At:           {opened}")
    
    closed_obj = ritm_data.get('closed_at', {})
    closed = closed_obj.get('display_value', 'N/A') if isinstance(closed_obj, dict) else closed_obj
    print(f"Closed At:           {closed}")
    
    # Request Information
    print(f"\n{'REQUEST INFORMATION':-^100}")
    
    req_obj = ritm_data.get('request', {})
    req = req_obj.get('display_value', 'N/A') if isinstance(req_obj, dict) else req_obj
    print(f"Parent Request:      {req}")
    
    cat_obj = ritm_data.get('cat_item', {})
    cat = cat_obj.get('display_value', 'N/A') if isinstance(cat_obj, dict) else cat_obj
    print(f"Catalog Item:        {cat}")
    
    # Requestor
    print(f"\n{'REQUESTOR INFORMATION':-^100}")
    
    req_for_obj = ritm_data.get('requested_for', {})
    req_for = req_for_obj.get('display_value', 'N/A') if isinstance(req_for_obj, dict) else req_for_obj
    print(f"Requested For:       {req_for}")
    
    # Assignment
    print(f"\n{'ASSIGNMENT':-^100}")
    
    grp_obj = ritm_data.get('assignment_group', {})
    grp = grp_obj.get('display_value', 'N/A') if isinstance(grp_obj, dict) else grp_obj
    print(f"Assignment Group:    {grp}")
    
    asn_obj = ritm_data.get('assigned_to', {})
    asn = asn_obj.get('display_value', 'N/A') if isinstance(asn_obj, dict) else asn_obj
    print(f"Assigned To:         {asn}")
    
    # Description
    print(f"\n{'DESCRIPTION':-^100}")
    
    desc_obj = ritm_data.get('short_description', {})
    desc = desc_obj.get('display_value', 'N/A') if isinstance(desc_obj, dict) else desc_obj
    print(f"Short Description:   {desc}")
    
    # Variables
    print("\n" + "="*100)
    print(f"{'CATALOG FORM VARIABLES':-^100}")
    print("="*100)
    
    if variables:
        sorted_vars = sorted(variables.items(), key=lambda x: x[1].get('order', 999))
        print(f"\n✓ Found {len(variables)} variables\n")
        
        for idx, (var_name, var_data) in enumerate(sorted_vars, 1):
            question = var_data.get('question', var_name)
            value = var_data.get('value', '')
            display_value = var_data.get('display_value', '')
            
            print(f"{'─'*100}")
            print(f"[{idx}] {question}")
            print(f"    Variable: {var_name}")
            
            # User reference fields
            if var_name in USER_REFERENCE_FIELDS and value and len(str(value).strip()) == 32:
                user_details = client.get_user_details(value)
                if user_details:
                    print(f"    👤 Name:     {user_details['name']}")
                    print(f"    📧 Email:    {user_details['email']}")
                    print(f"    🔑 Username: {user_details['user_name']}")
                    if user_details.get('phone') and user_details['phone'] != 'N/A':
                        print(f"    📞 Phone:    {user_details['phone']}")
                else:
                    print(f"    Value:       {display_value if display_value else value}")
            
            # Location fields
            elif var_name in LOCATION_FIELDS and value and len(str(value).strip()) == 32:
                location_name = client.get_location_details(value)
                print(f"    📍 Location: {location_name if location_name else (display_value if display_value else value)}")
            
            # Boolean fields
            elif str(value).lower() in ['true', 'false', '1', '0']:
                bool_val = "✓ Yes" if str(value).lower() in ['true', '1'] else "✗ No"
                print(f"    Value:       {bool_val}")
            
            # Regular fields
            else:
                final_val = display_value if display_value else value
                if not final_val or final_val == '':
                    final_val = "(Empty)"
                
                if '\n' in str(final_val):
                    print(f"    Value:")
                    for line in str(final_val).split('\n')[:10]:
                        print(f"              {line[:90]}")
                elif len(str(final_val)) > 80:
                    print(f"    Value:       {str(final_val)[:80]}...")
                else:
                    print(f"    Value:       {final_val}")
    else:
        print("\n⚠️  No variables found")
    
    print("\n" + "="*100)


def main():
    """Main function"""
    print("\n" + "="*100)
    print(" "*30 + "ServiceNow RITM Details Fetcher")
    print("="*100)
    
    # Initialize client
    print("\n🔗 Connecting to ServiceNow...")
    client = ServiceNowClient(SERVICENOW_BASE_URL, SERVICENOW_USERNAME, SERVICENOW_PASSWORD)
    
    # Get RITM number
    ritm_number = input("\n📋 Enter RITM number (e.g., RITM0010002): ").strip().upper()
    
    if not ritm_number:
        print("❌ RITM number required")
        return
    
    # Fetch RITM
    print(f"\n🔍 Fetching {ritm_number}...")
    ritm_data = client.get_ritm_by_number(ritm_number)
    
    if not ritm_data:
        return
    
    # Get sys_id
    ritm_sys_id_obj = ritm_data.get('sys_id', {})
    ritm_sys_id = ritm_sys_id_obj.get('value') if isinstance(ritm_sys_id_obj, dict) else ritm_sys_id_obj
    
    print(f"✓ RITM sys_id: {ritm_sys_id}")
    
    # Discover variables
    client.discover_catalog_item_variables(ritm_sys_id)
    
    # Fetch variables
    print("\n📦 Fetching variables...")
    variables = client.get_ritm_variables_direct(ritm_sys_id)
    print(f"\n✓ Total variables retrieved: {len(variables)}")
    
    # Display
    display_ritm_details(ritm_data, variables, client)
    
    print("\n✅ Complete!\n")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n⚠️  Cancelled")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()