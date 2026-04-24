import requests
import json
from typing import List, Optional, Dict, Any
from azure.identity import ClientSecretCredential
import time

class EntraAppOnboarding:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str, log_callback=None):
        """
        Initialize the Entra App Onboarding client
        """
        self.tenant_id = tenant_id
        self.credential = ClientSecretCredential(
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret
        )
        self.graph_endpoint = "https://graph.microsoft.com/v1.0"
        self.beta_endpoint = "https://graph.microsoft.com/beta"
        self.log_callback = log_callback
        
    def _log(self, message, log_type="info"):
        """Internal logging method"""
        if self.log_callback:
            self.log_callback(message, log_type)
        print(message)  # Also print to console for debugging
    
    def _get_access_token(self) -> str:
        """Get access token for Microsoft Graph API"""
        token = self.credential.get_token("https://graph.microsoft.com/.default")
        return token.token
    
    def _make_request(self, method: str, url: str, data: Optional[Dict] = None, use_beta: bool = False) -> Dict:
        """Make HTTP request to Microsoft Graph API"""
        headers = {
            'Authorization': f'Bearer {self._get_access_token()}',
            'Content-Type': 'application/json'
        }
        
        endpoint = self.beta_endpoint if use_beta else self.graph_endpoint
        full_url = f"{endpoint}{url}"
        
        response = requests.request(method, full_url, headers=headers, json=data)
        
        if response.status_code not in [200, 201, 204]:
            error_detail = ""
            try:
                error_json = response.json()
                error_detail = json.dumps(error_json, indent=2)
            except:
                error_detail = response.text
                
            raise Exception(f"Request failed: {response.status_code} - {error_detail}")
        
        return response.json() if response.content else {}
    
    def get_user_id_by_email(self, email: str) -> Optional[str]:
        """Get user ID by email address"""
        try:
            # Try different user principal name formats
            user_formats = [
                email,  # Direct email
                email.replace('@', '_') + f"#EXT#@{self.tenant_id}.onmicrosoft.com"  # External user format
            ]
            
            for user_format in user_formats:
                try:
                    url = f"/users/{user_format}"
                    user = self._make_request("GET", url)
                    self._log(f"✅ Found user: {email}", "success")
                    return user.get('id')
                except:
                    continue
                    
            # If direct lookup fails, try search
            url = f"/users?$filter=mail eq '{email}' or userPrincipalName eq '{email}'"
            result = self._make_request("GET", url)
            if result.get('value') and len(result['value']) > 0:
                self._log(f"✅ Found user via search: {email}", "success")
                return result['value'][0]['id']
                
            self._log(f"❌ User not found: {email}", "error")
            return None
            
        except Exception as e:
            self._log(f"❌ Error finding user {email}: {str(e)}", "error")
            return None

    def create_saml_enterprise_application(self, name: str, owner_emails: List[str]) -> Dict[str, Any]:
        """
        Create a simple non-gallery SAML Enterprise Application
        Just like clicking "Create your own application" in the portal
        """
        try:
            self._log(f"🛡️ Creating non-gallery SAML Enterprise Application: {name}")
            
            # Simple Service Principal creation - exactly like portal does
            sp_payload = {
                "displayName": name,
                "tags": [
                    "WindowsAzureActiveDirectoryIntegratedApp",
                    "WindowsAzureActiveDirectoryCustomSingleSignOnApplication"
                ]
            }
            
            self._log("🔧 Creating SAML Enterprise Application...")
            service_principal = self._make_request("POST", "/servicePrincipals", sp_payload)
            sp_id = service_principal['id']
            app_id = service_principal.get('appId')
            
            self._log(f"✅ SAML Enterprise Application created successfully!", "success")
            self._log(f"🆔 Service Principal ID: {sp_id}", "info")
            self._log(f"🆔 Application ID: {app_id}", "info")
            
            # Add owners to service principal
            successful_owners = self._add_owners_to_service_principal(sp_id, owner_emails)
            
            return {
                "service_principal": {
                    "id": sp_id,
                    "app_id": app_id,
                    "display_name": name,
                    "owners_added": successful_owners,
                    "type": "Non-Gallery SAML Enterprise Application"
                },
                "created_from_gallery": False,
                "display_name": name
            }
            
        except Exception as e:
            self._log(f"❌ Error creating SAML Enterprise Application: {str(e)}", "error")
            raise
   
    def _add_owners_to_service_principal(self, sp_id: str, owner_emails: List[str]) -> List[str]:
        """Add owners to service principal and return list of successfully added owners"""
        successful_owners = []
        
        for email in owner_emails:
            if not email or email.strip() == "":
                continue
                
            self._log(f"👤 Adding owner to SAML application: {email}")
            user_id = self.get_user_id_by_email(email)
            
            if user_id:
                try:
                    # Check if user is already an owner
                    existing_owners_url = f"/servicePrincipals/{sp_id}/owners"
                    existing_owners = self._make_request("GET", existing_owners_url)
                    existing_owner_ids = [owner['id'] for owner in existing_owners.get('value', [])]
                    
                    if user_id not in existing_owner_ids:
                        owner_payload = {
                            "@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user_id}"
                        }
                        self._make_request("POST", f"/servicePrincipals/{sp_id}/owners/$ref", owner_payload)
                        self._log(f"✅ Added owner {email} to SAML application", "success")
                        successful_owners.append(email)
                    else:
                        self._log(f"ℹ️ User {email} is already an owner of the SAML application", "info")
                        successful_owners.append(email)
                        
                except Exception as e:
                    self._log(f"❌ Could not add owner {email} to SAML application: {str(e)}", "error")
            else:
                self._log(f"⚠️ Skipping owner {email} - user not found in tenant", "error")
        
        return successful_owners

    # Keep existing OIDC methods unchanged...
    def create_app_registration(self, 
                              name: str, 
                              owner_emails: List[str], 
                              tenant_type: str = "single", 
                              redirect_urls: List[str] = None) -> Dict[str, Any]:
        """
        Create OIDC app registration with specified parameters
        """
        
        # Fix the tenant type mapping
        if tenant_type.lower() in ["multi-tenant", "multiple", "multitenant"]:
            supported_account_types = "AzureADMultipleOrgs"
            self._log(f"🌐 Setting up Multi-Tenant application (Any organizational directory)", "info")
        else:
            supported_account_types = "AzureADMyOrg"
            self._log(f"🏢 Setting up Single-Tenant application (This directory only)", "info")
        
        # Log the actual value being used
        self._log(f"📝 signInAudience will be set to: {supported_account_types}", "info")
        
        # Prepare redirect URIs
        web_redirect_uris = redirect_urls or []
        
        # App registration payload
        app_payload = {
            "displayName": name,
            "signInAudience": supported_account_types,
            "web": {
                "redirectUris": web_redirect_uris,
                "implicitGrantSettings": {
                    "enableIdTokenIssuance": True,
                    "enableAccessTokenIssuance": True
                }
            },
            "requiredResourceAccess": []
        }
        
        # Log the payload for debugging
        self._log(f"🔧 Creating OIDC app with signInAudience: {supported_account_types}")
        
        try:
            # Create app registration
            self._log(f"🔧 Creating OIDC app registration: {name}")
            app_registration = self._make_request("POST", "/applications", app_payload)
            app_id = app_registration['id']
            client_id = app_registration['appId']
            actual_signin_audience = app_registration.get('signInAudience', 'Unknown')
            
            self._log(f"✅ OIDC app registration created with ID: {app_id}, Client ID: {client_id}", "success")
            self._log(f"📋 Actual signInAudience: {actual_signin_audience}", "info")
            
            # Verify the tenant type was set correctly
            if tenant_type.lower() in ["multi-tenant", "multiple", "multitenant"] and actual_signin_audience != "AzureADMultipleOrgs":
                self._log(f"⚠️ Warning: Expected Multi-Tenant but got {actual_signin_audience}", "error")
            
            # Wait for app registration to propagate
            self._wait_for_app_propagation(client_id)
            
            # Add owners to app registration
            successful_owners = self._add_owners_to_app_registration(app_id, owner_emails)
            
            # Create service principal (enterprise application) with retry logic
            service_principal = self._create_service_principal_with_retry(client_id, owner_emails)
            
            return {
                "app_registration": {
                    "id": app_id,
                    "client_id": client_id,
                    "name": name,
                    "tenant_type": tenant_type,
                    "signin_audience": actual_signin_audience,
                    "redirect_urls": web_redirect_uris,
                    "owners_added": successful_owners,
                    "type": "OIDC App Registration"
                },
                "service_principal": service_principal
            }
            
        except Exception as e:
            self._log(f"❌ Error creating OIDC app registration: {str(e)}", "error")
            raise

    def _wait_for_app_propagation(self, client_id: str, max_retries: int = 10, delay: int = 2) -> bool:
        """Wait for app registration to propagate before creating service principal"""
        self._log(f"⏳ Waiting for app registration {client_id} to propagate...")
        
        for attempt in range(max_retries):
            try:
                # Try to fetch the application by appId to verify it exists
                url = f"/applications?$filter=appId eq '{client_id}'"
                result = self._make_request("GET", url)
                
                if result.get('value') and len(result['value']) > 0:
                    self._log(f"✅ App registration propagated successfully after {attempt + 1} attempts", "success")
                    return True
                    
                self._log(f"⏳ Attempt {attempt + 1}/{max_retries}: App not yet propagated, waiting {delay} seconds...")
                time.sleep(delay)
                
            except Exception as e:
                self._log(f"⚠️ Attempt {attempt + 1}/{max_retries}: Error checking app propagation: {str(e)}", "error")
                time.sleep(delay)
        
        self._log(f"⚠️ Warning: App registration may not be fully propagated after {max_retries} attempts", "error")
        return False 
    def _add_owners_to_app_registration(self, app_id: str, owner_emails: List[str]) -> List[str]:
        """Add owners to app registration and return list of successfully added owners"""
        successful_owners = []
        
        for email in owner_emails:
            if not email or email.strip() == "":
                continue
                
            self._log(f"👤 Processing owner for OIDC app: {email}")
            user_id = self.get_user_id_by_email(email)
            
            if user_id:
                try:
                    # Check if user is already an owner
                    existing_owners_url = f"/applications/{app_id}/owners"
                    existing_owners = self._make_request("GET", existing_owners_url)
                    existing_owner_ids = [owner['id'] for owner in existing_owners.get('value', [])]
                    
                    if user_id not in existing_owner_ids:
                        owner_payload = {
                            "@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user_id}"
                        }
                        self._make_request("POST", f"/applications/{app_id}/owners/$ref", owner_payload)
                        self._log(f"✅ Added owner {email} to OIDC app registration", "success")
                        successful_owners.append(email)
                    else:
                        self._log(f"ℹ️ User {email} is already an owner of the OIDC app registration", "info")
                        successful_owners.append(email)
                        
                except Exception as e:
                    self._log(f"❌ Could not add owner {email} to OIDC app registration: {str(e)}", "error")
            else:
                self._log(f"⚠️ Skipping owner {email} - user not found in tenant", "error")
        
        return successful_owners

    def _create_service_principal_with_retry(self, client_id: str, owner_emails: List[str], max_retries: int = 5) -> Dict[str, Any]:
        """Create service principal with retry logic for propagation issues"""
        
        for attempt in range(max_retries):
            try:
                return self._create_service_principal(client_id, owner_emails)
            except Exception as e:
                if "NoBackingApplicationObject" in str(e) and attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 3  # Exponential backoff
                    self._log(f"⚠️ Service principal creation failed (attempt {attempt + 1}/{max_retries}). "
                          f"Waiting {wait_time} seconds for app propagation...")
                    time.sleep(wait_time)
                    continue
                else:
                    raise e
        
        raise Exception(f"Failed to create service principal after {max_retries} attempts")

    def _create_service_principal(self, client_id: str, owner_emails: List[str]) -> Dict[str, Any]:
        """Create service principal for the OIDC app registration"""
        
        # First check if service principal already exists
        try:
            existing_sp_url = f"/servicePrincipals?$filter=appId eq '{client_id}'"
            existing_sp = self._make_request("GET", existing_sp_url)
            
            if existing_sp.get('value') and len(existing_sp['value']) > 0:
                self._log("ℹ️ Service principal already exists, using existing one", "info")
                service_principal = existing_sp['value'][0]
                sp_id = service_principal['id']
            else:
                # Create new service principal for OIDC app
                sp_payload = {
                    "appId": client_id,
                    "tags": ["WindowsAzureActiveDirectoryIntegratedApp"],  # OIDC tag
                    "appRoleAssignmentRequired": False
                }
                
                self._log("🔧 Creating OIDC service principal (enterprise application)")
                service_principal = self._make_request("POST", "/servicePrincipals", sp_payload)
                sp_id = service_principal['id']
                self._log(f"✅ OIDC service principal created with ID: {sp_id}", "success")
            
            # Add owners to service principal
            successful_sp_owners = self._add_owners_to_service_principal_oidc(sp_id, owner_emails)
            
            return {
                "id": sp_id,
                "app_id": client_id,
                "display_name": service_principal['displayName'],
                "owners_added": successful_sp_owners,
                "type": "OIDC Service Principal"
            }
            
        except Exception as e:
            self._log(f"❌ Error creating OIDC service principal: {str(e)}", "error")
            raise

    def _add_owners_to_service_principal_oidc(self, sp_id: str, owner_emails: List[str]) -> List[str]:
        """Add owners to OIDC service principal"""
        successful_owners = []
        
        for email in owner_emails:
            if not email or email.strip() == "":
                continue
                
            self._log(f"👤 Adding owner to OIDC service principal: {email}")
            user_id = self.get_user_id_by_email(email)
            
            if user_id:
                try:
                    # Check if user is already an owner
                    existing_owners_url = f"/servicePrincipals/{sp_id}/owners"
                    existing_owners = self._make_request("GET", existing_owners_url)
                    existing_owner_ids = [owner['id'] for owner in existing_owners.get('value', [])]
                    
                    if user_id not in existing_owner_ids:
                        owner_payload = {
                            "@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user_id}"
                        }
                        self._make_request("POST", f"/servicePrincipals/{sp_id}/owners/$ref", owner_payload)
                        self._log(f"✅ Added owner {email} to OIDC service principal", "success")
                        successful_owners.append(email)
                    else:
                        self._log(f"ℹ️ User {email} is already an owner of the OIDC service principal", "info")
                        successful_owners.append(email)
                        
                except Exception as e:
                    self._log(f"❌ Could not add owner {email} to OIDC service principal: {str(e)}", "error")
            else:
                self._log(f"⚠️ Skipping owner {email} - user not found in tenant", "error")
        
        return successful_owners

    def search_gallery_application(self, gallery_name: str) -> Optional[Dict[str, Any]]:
        """Search for application in Azure AD gallery"""
        try:
            self._log(f"🔍 Searching for '{gallery_name}' in Azure AD gallery...")
            
            # Search with exact match first
            url = f"/applicationTemplates?$filter=displayName eq '{gallery_name}'"
            result = self._make_request("GET", url)
            
            if result.get('value'):
                self._log(f"✅ Found exact match for '{gallery_name}' in gallery", "success")
                return result['value'][0]
            
            # Try case-insensitive search
            url = f"/applicationTemplates?$filter=tolower(displayName) eq '{gallery_name.lower()}'"
            result = self._make_request("GET", url)
            
            if result.get('value'):
                self._log(f"✅ Found case-insensitive match for '{gallery_name}' in gallery", "success")
                return result['value'][0]
            
            # Try contains search for partial matches
            url = f"/applicationTemplates?$filter=contains(tolower(displayName), '{gallery_name.lower()}')"
            result = self._make_request("GET", url)
            
            if result.get('value'):
                similar_apps = [app['displayName'] for app in result['value'][:5]]
                self._log(f"🔍 Found similar applications in gallery: {similar_apps}")
                self._log(f"Using first match: '{result['value'][0]['displayName']}'")
                return result['value'][0]  # Return first match
            
            self._log(f"❌ No applications found in gallery matching '{gallery_name}'", "error")
            return None
            
        except Exception as e:
            self._log(f"❌ Error searching gallery application: {str(e)}", "error")
            return None

    def create_gallery_application(self, template_id: str, display_name: str, owner_emails: List[str]) -> Dict[str, Any]:
        """Create application from gallery template"""
        try:
            # Instantiate gallery application
            instantiate_payload = {
                "displayName": display_name
            }
            
            self._log(f"🔧 Creating gallery application with display name: '{display_name}' from template: {template_id}")
            result = self._make_request("POST", f"/applicationTemplates/{template_id}/instantiate", instantiate_payload)
            
            # Wait for the application to be created and propagated
            self._log("⏳ Waiting for gallery application to be fully created...")
            time.sleep(10)
            
            # Get the created service principal ID
            sp_info = result.get('servicePrincipal', {})
            app_info = result.get('application', {})
            
            sp_id = sp_info.get('id')
            app_id = app_info.get('id')
            
            self._log(f"✅ Gallery application created successfully:", "success")
            self._log(f"   - Display Name: {display_name}")
            self._log(f"   - Service Principal ID: {sp_id}")
            self._log(f"   - Application ID: {app_id}")
            
            successful_sp_owners = []
            successful_app_owners = []
            
            if sp_id:
                # Add owners to service principal
                successful_sp_owners = self._add_owners_to_service_principal(sp_id, owner_emails)
            
            if app_id:
                # Add owners to application
                successful_app_owners = self._add_owners_to_app_registration(app_id, owner_emails)
            
            return {
                "service_principal": {**sp_info, "owners_added": successful_sp_owners, "type": "Gallery SAML Application"},
                "application": {**app_info, "owners_added": successful_app_owners} if app_info else None,
                "created_from_gallery": True,
                "template_id": template_id,
                "display_name": display_name
            }
            
        except Exception as e:
            self._log(f"❌ Error creating gallery application: {str(e)}", "error")
            raise

    def create_enterprise_application(self, 
                                name: str, 
                                owner_emails: List[str], 
                                gallery_mode: bool = True,
                                gallery_name: str = None) -> Dict[str, Any]:
        """Create SAML enterprise application"""
        
        if gallery_mode:
            # Use gallery_name for search, or fall back to name if not provided
            search_name = gallery_name if gallery_name else name
            
            self._log(f"🔍 Gallery Mode: Searching for '{search_name}' in Azure AD gallery...")
            gallery_app = self.search_gallery_application(search_name)
            
            if gallery_app:
                self._log(f"✅ Found '{gallery_app['displayName']}' in gallery (Template ID: {gallery_app['id']})")
                self._log(f"📝 Creating with display name: '{name}'")
                return self.create_gallery_application(gallery_app['id'], name, owner_emails)
            else:
                self._log(f"❌ '{search_name}' not found in gallery")
                self._log(f"🔄 Creating non-gallery SAML enterprise application")
        
        # Create simple non-gallery SAML enterprise application
        self._log("🔧 Creating non-gallery SAML enterprise application (no configuration needed)")
        return self.create_saml_enterprise_application(name, owner_emails)