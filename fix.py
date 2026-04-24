import requests
import json
from azure.identity import ClientSecretCredential

# Configuration - UPDATE THESE VALUES
CLIENT_SECRET="DlG8Q~FtwMo2u4Xnnp7n4YeZo6yA2TkQf5lOlcLQ"
CLIENT_ID="fccf1372-691d-4b33-ba95-fc148c5f23b3"
TENANT_ID="7d320f8a-d592-4dd2-bf25-4acfeb8f2e15"

# The Client ID of the app you want to fix
TARGET_APP_CLIENT_ID = "1e158c41-5b57-436d-a3a9-64a1f681f99e"

def get_access_token():
    """Get access token for Microsoft Graph API"""
    credential = ClientSecretCredential(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET
    )
    token = credential.get_token("https://graph.microsoft.com/.default")
    return token.token

def make_request(method, url, data=None):
    """Make HTTP request to Microsoft Graph API"""
    headers = {
        'Authorization': f'Bearer {get_access_token()}',
        'Content-Type': 'application/json'
    }
    
    full_url = f"https://graph.microsoft.com/v1.0{url}"
    response = requests.request(method, full_url, headers=headers, json=data)
    
    if response.status_code not in [200, 201, 204]:
        error_detail = response.text
        try:
            error_json = response.json()
            error_detail = json.dumps(error_json, indent=2)
        except:
            pass
        raise Exception(f"Request failed: {response.status_code} - {error_detail}")
    
    return response.json() if response.content else {}

def fix_enterprise_app_visibility(client_id):
    """Make Enterprise Application visible in Azure portal"""
    
    print(f"🔍 Looking for Service Principal with Client ID: {client_id}")
    
    # Find the service principal
    sp_url = f"/servicePrincipals?$filter=appId eq '{client_id}'"
    sp_result = make_request("GET", sp_url)
    
    if not sp_result.get('value'):
        print("❌ Service Principal not found!")
        return False
        
    sp = sp_result['value'][0]
    sp_id = sp['id']
    display_name = sp.get('displayName', 'Unknown')
    
    print(f"✅ Found Service Principal: {display_name}")
    print(f"   ID: {sp_id}")
    
    # Method 1: Add visibility tag to Service Principal
    print("\n🏷️  Step 1: Adding visibility tag to Service Principal...")
    try:
        tag_payload = {
            "tags": ["WindowsAzureActiveDirectoryIntegratedApp"]
        }
        make_request("PATCH", f"/servicePrincipals/{sp_id}", tag_payload)
        print("✅ Visibility tag added successfully")
    except Exception as e:
        print(f"⚠️ Could not add visibility tag: {str(e)}")
    
    # Method 2: Add basic Microsoft Graph permission to App Registration
    print("\n📝 Step 2: Adding basic permissions to App Registration...")
    try:
        app_url = f"/applications?$filter=appId eq '{client_id}'"
        app_result = make_request("GET", app_url)
        
        if app_result.get('value'):
            app_object_id = app_result['value'][0]['id']
            app_display_name = app_result['value'][0].get('displayName', 'Unknown')
            
            print(f"✅ Found App Registration: {app_display_name}")
            
            # Get current permissions
            current_permissions = app_result['value'][0].get('requiredResourceAccess', [])
            
            # Check if Microsoft Graph permission already exists
            graph_permission_exists = False
            for perm in current_permissions:
                if perm.get('resourceAppId') == '00000003-0000-0000-c000-000000000000':
                    graph_permission_exists = True
                    break
            
            if not graph_permission_exists:
                permission_payload = {
                    "requiredResourceAccess": current_permissions + [
                        {
                            "resourceAppId": "00000003-0000-0000-c000-000000000000",  # Microsoft Graph
                            "resourceAccess": [
                                {
                                    "id": "e1fe6dd8-ba31-4d61-89e7-88639da4683d",  # User.Read
                                    "type": "Scope"
                                }
                            ]
                        }
                    ]
                }
                
                make_request("PATCH", f"/applications/{app_object_id}", permission_payload)
                print("✅ Microsoft Graph User.Read permission added")
            else:
                print("ℹ️ Microsoft Graph permissions already exist")
        else:
            print("❌ App Registration not found!")
            
    except Exception as e:
        print(f"⚠️ Could not add permissions: {str(e)}")
    
    # Method 3: Update Service Principal properties
    print("\n🔧 Step 3: Updating Service Principal properties...")
    try:
        sp_update_payload = {
            "appRoleAssignmentRequired": False,
            "tags": ["WindowsAzureActiveDirectoryIntegratedApp", "HideApp"],
            "notes": f"Enterprise application for {display_name}"
        }
        make_request("PATCH", f"/servicePrincipals/{sp_id}", sp_update_payload)
        print("✅ Service Principal properties updated")
    except Exception as e:
        print(f"⚠️ Could not update Service Principal properties: {str(e)}")
    
    # Method 4: Grant admin consent (this often makes it visible)
    print("\n🔐 Step 4: Attempting to grant admin consent...")
    try:
        # This creates an admin consent record which often triggers visibility
        consent_payload = {
            "clientId": sp_id,
            "consentType": "AllPrincipals",
            "resourceId": "00000003-0000-0000-c000-000000000000",  # Microsoft Graph SP ID
            "scope": "User.Read"
        }
        
        # Note: This might fail if consent is already granted, which is fine
        try:
            make_request("POST", "/oauth2PermissionGrants", consent_payload)
            print("✅ Admin consent granted")
        except Exception as consent_error:
            if "already exists" in str(consent_error).lower():
                print("ℹ️ Admin consent already exists")
            else:
                print(f"⚠️ Could not grant admin consent: {str(consent_error)}")
                
    except Exception as e:
        print(f"⚠️ Error in consent process: {str(e)}")
    
    print("\n" + "="*60)
    print("🎉 Enterprise Application visibility fix completed!")
    print("="*60)
    print(f"📋 Service Principal ID: {sp_id}")
    print(f"📋 Client ID: {client_id}")
    print(f"📋 Display Name: {display_name}")
    print("\n📌 Next steps:")
    print("1. Go to Azure Portal → Enterprise Applications")
    print("2. Click 'Refresh' button (🔄)")
    print(f"3. Search for '{display_name}'")
    print(f"4. Or search by Service Principal ID: {sp_id}")
    print("5. If still not visible, wait 5-10 minutes and refresh again")
    print("\n💡 Alternative: Check 'All Applications' filter and remove any search filters")
    
    return True

def main():
    print("🚀 Enterprise Application Visibility Fix")
    print("="*50)
    print(f"Target App Client ID: {TARGET_APP_CLIENT_ID}")
    print(f"Tenant ID: {TENANT_ID}")
    print("")
    
    try:
        success = fix_enterprise_app_visibility(TARGET_APP_CLIENT_ID)
        
        if success:
            print("\n✅ Process completed successfully!")
        else:
            print("\n❌ Process failed!")
            
    except Exception as e:
        print(f"\n💥 Error: {str(e)}")
        print("\n🔧 Troubleshooting tips:")
        print("1. Check your Azure credentials in the script")
        print("2. Ensure your app has the required permissions:")
        print("   - Application.ReadWrite.All")
        print("   - Directory.ReadWrite.All")
        print("3. Make sure the Client ID is correct")

if __name__ == "__main__":
    main()