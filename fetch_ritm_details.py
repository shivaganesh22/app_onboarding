"""
ServiceNow RITM Details Fetcher for Azure Entra ID Application Request
This script fetches Requested Item (RITM) details including all form variables
with proper reference field resolution (names, emails)
"""

import requests
import json
from requests.auth import HTTPBasicAuth
from datetime import datetime
import sys

# ServiceNow Configuration
SERVICENOW_BASE_URL = "https://dev265148.service-now.com"
SERVICENOW_USERNAME = "1"
SERVICENOW_PASSWORD = "[cO=OVUsl]e}bC{nQM_.6SAWMG<py-n{!g%VA3p}k-<SA72C]@$cC$aHzAn)XZYpdgueaF!@J%c]6q&QF&pb0e5QV{5=Lu?wUa@C"

# Catalog Item Details (from exported form)
CATALOG_ITEM_NAME = "Azure Entra ID Application Request"
CATALOG_ITEM_SYS_ID = "af5112b483c0c310b5b7f496feaad35e"

# Variable Names extracted from the catalog form
CATALOG_VARIABLES = [
    'requested_for',
    'location',
    'requested_by',
    'contact_mode',
    'app_name',
    'app_type',
    'purpose',
    'risk_assessment',
    'business_owner',
    'technical_owner',
    'auth_method',
    'environment',
    'admin_consent',
    'additional_info',
    'api_permissions_required',
    'api_permissions_details',
    'expose_apis',
    'expose_apis_details',
    'redirect_uris_required',
    'redirect_uris_list',
    'tenant_scope',
    'cert_secret_required',
    'users_assigned',
    'users_assigned_list',
    'metadata_xml_available',
    'saml_entity_id',
    'saml_acs_url',
    'saml_attributes'
]

# Reference fields that need user details (name, email)
USER_REFERENCE_FIELDS = ['requested_for', 'requested_by', 'business_owner', 'technical_owner']


class ServiceNowClient:
    """Client for interacting with ServiceNow API"""
    
    def __init__(self, base_url, username, password):
        self.base_url = base_url.rstrip('/')
        self.auth = HTTPBasicAuth(username, password)
        self.headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        }
    
    def get_user_details(self, user_sys_id):
        """
        Fetch user details (name, email) by sys_id
        """
        if not user_sys_id or len(user_sys_id) != 32:  # ServiceNow sys_ids are 32 chars
            return None
            
        url = f"{self.base_url}/api/now/table/sys_user/{user_sys_id}"
        params = {
            'sysparm_fields': 'name,email,user_name,first_name,last_name,phone',
            'sysparm_display_value': 'false'
        }
        
        try:
            response = requests.get(url, auth=self.auth, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data.get('result'):
                user = data['result']
                return {
                    'name': user.get('name', 'N/A'),
                    'email': user.get('email', 'N/A'),
                    'user_name': user.get('user_name', 'N/A'),
                    'first_name': user.get('first_name', ''),
                    'last_name': user.get('last_name', ''),
                    'phone': user.get('phone', 'N/A')
                }
            return None
            
        except requests.exceptions.RequestException as e:
            print(f"Error fetching user details for {user_sys_id}: {e}")
            return None
    
    def get_ritm_by_number(self, ritm_number):
        """
        Fetch RITM details by RITM number (e.g., RITM0012345)
        """
        url = f"{self.base_url}/api/now/table/sc_req_item"
        params = {
            'sysparm_query': f'number={ritm_number}',
            'sysparm_display_value': 'true',
            'sysparm_fields': 'number,sys_id,short_description,description,state,stage,priority,'                             'opened_at,closed_at,request,request.number,request.sys_id,'                             'requested_for,requested_for.name,requested_for.email,'                             'cat_item,cat_item.name,assignment_group,assigned_to,'                             'work_notes,comments'
        }
        
        try:
            response = requests.get(url, auth=self.auth, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            if data.get('result'):
                return data['result'][0]
            else:
                print(f"No RITM found with number: {ritm_number}")
                return None
                
        except requests.exceptions.RequestException as e:
            print(f"Error fetching RITM: {e}")
            return None
    
    def get_ritm_variables(self, ritm_sys_id):
        """
        Fetch all variables for a specific RITM with proper reference field handling
        """
        url = f"{self.base_url}/api/now/table/sc_item_option_mtom"
        params = {
            'sysparm_query': f'request_item={ritm_sys_id}',
            'sysparm_display_value': 'all'  # Get both display and actual values
        }
        
        try:
            response = requests.get(url, auth=self.auth, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            variables = {}
            for record in data.get('result', []):
                # Extract variable name
                var_name = None
                if 'sc_item_option' in record:
                    if isinstance(record['sc_item_option'], dict):
                        var_name = record['sc_item_option'].get('item_option_new', {}).get('name', '')
                        if not var_name and 'display_value' in record['sc_item_option']:
                            var_name = record['sc_item_option']['display_value']
                
                # Try alternative structure
                if not var_name:
                    var_name = record.get('sc_item_option.item_option_new.name', '')
                
                # Extract value
                var_value = None
                var_display_value = None
                
                if 'sc_item_option' in record and isinstance(record['sc_item_option'], dict):
                    var_value = record['sc_item_option'].get('value', '')
                    var_display_value = record['sc_item_option'].get('display_value', '')
                else:
                    var_value = record.get('sc_item_option.value', '')
                    var_display_value = record.get('sc_item_option.display_value', '')
                
                if var_name:
                    variables[var_name] = {
                        'value': var_value,
                        'display_value': var_display_value
                    }
            
            return variables
            
        except requests.exceptions.RequestException as e:
            print(f"Error fetching RITM variables: {e}")
            return {}
    
    def get_ritm_variables_alternative(self, ritm_sys_id):
        """
        Alternative method to fetch variables using sc_item_option table
        Returns variables with both actual values and display values
        """
        url = f"{self.base_url}/api/now/table/sc_item_option"
        params = {
            'sysparm_query': f'request_item={ritm_sys_id}',
            'sysparm_display_value': 'all',
            'sysparm_fields': 'item_option_new.name,item_option_new.question_text,value'
        }
        
        try:
            response = requests.get(url, auth=self.auth, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            
            variables = {}
            for record in data.get('result', []):
                var_name_obj = record.get('item_option_new.name', {})
                var_question_obj = record.get('item_option_new.question_text', {})
                var_value_obj = record.get('value', {})
                
                # Extract actual name
                var_name = var_name_obj.get('value', '') if isinstance(var_name_obj, dict) else str(var_name_obj)
                
                # Extract question text
                var_question = var_question_obj.get('value', '') if isinstance(var_question_obj, dict) else str(var_question_obj)
                if not var_question:
                    var_question = var_question_obj.get('display_value', '') if isinstance(var_question_obj, dict) else str(var_question_obj)
                
                # Extract value and display value
                if isinstance(var_value_obj, dict):
                    var_value = var_value_obj.get('value', '')
                    var_display_value = var_value_obj.get('display_value', '')
                else:
                    var_value = str(var_value_obj)
                    var_display_value = str(var_value_obj)
                
                if var_name:
                    variables[var_name] = {
                        'question': var_question,
                        'value': var_value,
                        'display_value': var_display_value
                    }
            
            return variables
            
        except requests.exceptions.RequestException as e:
            print(f"Error fetching RITM variables (alternative method): {e}")
            return {}
    
    def get_ritms_by_catalog_item(self, catalog_item_sys_id, limit=10):
        """
        Fetch RITMs for a specific catalog item
        """
        url = f"{self.base_url}/api/now/table/sc_req_item"
        params = {
            'sysparm_query': f'cat_item={catalog_item_sys_id}^ORDERBYDESCopened_at',
            'sysparm_limit': limit,
            'sysparm_display_value': 'true',
            'sysparm_fields': 'number,sys_id,short_description,state,opened_at,requested_for.name'
        }
        
        try:
            response = requests.get(url, auth=self.auth, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            return data.get('result', [])
            
        except requests.exceptions.RequestException as e:
            print(f"Error fetching RITMs by catalog item: {e}")
            return []
    
    def get_request_details(self, request_sys_id):
        """
        Fetch parent request (REQ) details
        """
        url = f"{self.base_url}/api/now/table/sc_request/{request_sys_id}"
        params = {
            'sysparm_display_value': 'true'
        }
        
        try:
            response = requests.get(url, auth=self.auth, headers=self.headers, params=params)
            response.raise_for_status()
            data = response.json()
            return data.get('result', {})
            
        except requests.exceptions.RequestException as e:
            print(f"Error fetching request details: {e}")
            return {}


def display_ritm_details(ritm_data, variables, client):
    """
    Display RITM details in a formatted manner with resolved user references
    """
    print("\n" + "="*100)
    print("RITM DETAILS - Azure Entra ID Application Request")
    print("="*100)
    
    # Basic RITM Information
    print(f"\nRITM Number:        {ritm_data.get('number', 'N/A')}")
    print(f"State:               {ritm_data.get('state', 'N/A')}")
    print(f"Stage:               {ritm_data.get('stage', 'N/A')}")
    print(f"Priority:            {ritm_data.get('priority', 'N/A')}")
    print(f"Opened At:           {ritm_data.get('opened_at', 'N/A')}")
    print(f"Closed At:           {ritm_data.get('closed_at', 'N/A')}")
    
    # Request Information
    print(f"\nParent Request:      {ritm_data.get('request.number', 'N/A')}")
    print(f"Catalog Item:        {ritm_data.get('cat_item.name', 'N/A')}")
    
    # Requestor Information
    print(f"\nRequested For:       {ritm_data.get('requested_for.name', 'N/A')}")
    print(f"Email:               {ritm_data.get('requested_for.email', 'N/A')}")
    
    # Assignment
    print(f"\nAssignment Group:    {ritm_data.get('assignment_group', 'N/A')}")
    print(f"Assigned To:         {ritm_data.get('assigned_to', 'N/A')}")
    
    # Description
    print(f"\nShort Description:   {ritm_data.get('short_description', 'N/A')}")
    if ritm_data.get('description'):
        print(f"Description:         {ritm_data.get('description', 'N/A')}")
    
    # Variables
    print("\n" + "="*100)
    print("CATALOG FORM VARIABLES")
    print("="*100)
    
    if variables:
        for var_name in CATALOG_VARIABLES:
            if var_name in variables:
                var_data = variables[var_name]
                
                # Get question text
                if isinstance(var_data, dict):
                    question = var_data.get('question', var_name)
                    value = var_data.get('value', '')
                    display_value = var_data.get('display_value', '')
                else:
                    question = var_name
                    value = var_data
                    display_value = var_data
                
                # For user reference fields, fetch full details
                if var_name in USER_REFERENCE_FIELDS and value and len(str(value)) == 32:
                    print(f"\n{question}:")
                    user_details = client.get_user_details(value)
                    if user_details:
                        print(f"  Name:       {user_details['name']}")
                        print(f"  Email:      {user_details['email']}")
                        print(f"  User Name:  {user_details['user_name']}")
                        if user_details.get('phone') and user_details['phone'] != 'N/A':
                            print(f"  Phone:      {user_details['phone']}")
                        print(f"  [Sys ID: {value}]")
                    else:
                        print(f"  Value: {display_value if display_value else value}")
                        print(f"  [Sys ID: {value}]")
                else:
                    # Regular field display
                    display = display_value if display_value else value
                    print(f"\n{question}:")
                    print(f"  {display if display else 'N/A'}")
    else:
        print("\nNo variables found for this RITM")
    
    print("\n" + "="*100)


def export_to_json(ritm_data, variables, client, filename=None):
    """
    Export RITM details to JSON file with resolved user references
    """
    if not filename:
        ritm_number = ritm_data.get('number', 'RITM').replace('/', '_')
        filename = f"{ritm_number}_details_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    
    # Enhance variables with user details
    enhanced_variables = {}
    for var_name, var_data in variables.items():
        if isinstance(var_data, dict):
            value = var_data.get('value', '')
            enhanced_var = var_data.copy()
            
            # Fetch user details for reference fields
            if var_name in USER_REFERENCE_FIELDS and value and len(str(value)) == 32:
                user_details = client.get_user_details(value)
                if user_details:
                    enhanced_var['user_details'] = user_details
            
            enhanced_variables[var_name] = enhanced_var
        else:
            enhanced_variables[var_name] = var_data
    
    export_data = {
        'ritm_details': ritm_data,
        'variables': enhanced_variables,
        'exported_at': datetime.now().isoformat()
    }
    
    try:
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(export_data, f, indent=2, ensure_ascii=False)
        print(f"\nData exported to: {filename}")
        return filename
    except Exception as e:
        print(f"\nError exporting to JSON: {e}")
        return None


def main():
    """
    Main function
    """
    print("ServiceNow RITM Details Fetcher - Enhanced with User Details")
    print("="*100)
    
    # Initialize ServiceNow client
    client = ServiceNowClient(SERVICENOW_BASE_URL, SERVICENOW_USERNAME, SERVICENOW_PASSWORD)
    
    # Get RITM number from user
    if len(sys.argv) > 1:
        ritm_number = sys.argv[1]
    else:
        ritm_number = input("\nEnter RITM number (e.g., RITM0012345): ").strip()
    
    if not ritm_number:
        print("\nNo RITM number provided. Fetching recent RITMs for this catalog item...")
        ritms = client.get_ritms_by_catalog_item(CATALOG_ITEM_SYS_ID, limit=10)
        
        if ritms:
            print(f"\nFound {len(ritms)} recent RITMs:")
            print("-" * 100)
            for idx, ritm in enumerate(ritms, 1):
                print(f"{idx}. {ritm.get('number')} - {ritm.get('state')} - "
                      f"{ritm.get('requested_for.name')} - {ritm.get('opened_at')}")
            print("-" * 100)
            
            choice = input("\nEnter RITM number to view details (or press Enter to exit): ").strip()
            if choice:
                ritm_number = choice
            else:
                return
        else:
            print("No RITMs found for this catalog item.")
            return
    
    # Fetch RITM details
    print(f"\nFetching details for {ritm_number}...")
    ritm_data = client.get_ritm_by_number(ritm_number)
    
    if not ritm_data:
        return
    
    ritm_sys_id = ritm_data.get('sys_id')
    
    # Fetch variables
    print("Fetching catalog variables...")
    variables = client.get_ritm_variables_alternative(ritm_sys_id)
    
    # If alternative method returns empty, try the first method
    if not variables:
        print("Trying alternative method...")
        variables = client.get_ritm_variables(ritm_sys_id)
    
    # Display details
    display_ritm_details(ritm_data, variables, client)
    
    # Ask if user wants to export to JSON
    export_choice = input("\nExport to JSON? (y/n): ").strip().lower()
    if export_choice == 'y':
        export_to_json(ritm_data, variables, client)


if __name__ == "__main__":
    main()
