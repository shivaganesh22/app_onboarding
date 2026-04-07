import requests
from azure.identity import ClientSecretCredential
import json

# Configuration
CLIENT_SECRET="DlG8Q~FtwMo2u4Xnnp7n4YeZo6yA2TkQf5lOlcLQ"
CLIENT_ID="fccf1372-691d-4b33-ba95-fc148c5f23b3"
TENANT_ID="7d320f8a-d592-4dd2-bf25-4acfeb8f2e15"

def get_access_token():
    credential = ClientSecretCredential(
        tenant_id=TENANT_ID,
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET
    )
    token = credential.get_token("https://graph.microsoft.com/.default")
    return token.token

def create_multitenant_test_app():
    headers = {
        'Authorization': f'Bearer {get_access_token()}',
        'Content-Type': 'application/json'
    }
    
    # Test multi-tenant app creation
    app_payload = {
        "displayName": "Multi-Tenant Test App",
        "signInAudience": "AzureADMultipleOrgs",  # This is the key for multi-tenant
        "web": {
            "redirectUris": ["https://test.com/callback"],
            "implicitGrantSettings": {
                "enableIdTokenIssuance": True,
                "enableAccessTokenIssuance": True
            }
        }
    }
    
    print("Creating multi-tenant test app...")
    print(f"Payload: {json.dumps(app_payload, indent=2)}")
    
    response = requests.post(
        "https://graph.microsoft.com/v1.0/applications",
        headers=headers,
        json=app_payload
    )
    
    if response.status_code in [200, 201]:
        result = response.json()
        print(f"✅ Success!")
        print(f"App ID: {result['id']}")
        print(f"Client ID: {result['appId']}")
        print(f"SignIn Audience: {result['signInAudience']}")
        print(f"Display Name: {result['displayName']}")
        
        # Verify it's actually multi-tenant
        if result['signInAudience'] == 'AzureADMultipleOrgs':
            print("✅ Confirmed: Multi-tenant app created successfully!")
        else:
            print(f"❌ Error: Expected 'AzureADMultipleOrgs' but got '{result['signInAudience']}'")
            
        return result
    else:
        print(f"❌ Error: {response.status_code}")
        print(response.text)
        return None

if __name__ == "__main__":
    create_multitenant_test_app()