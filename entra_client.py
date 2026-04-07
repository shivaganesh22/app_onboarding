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
        print(f"[{log_type.upper()}] {message}")
    
    def _get_access_token(self) -> str:
        """Get access token for Microsoft Graph API"""
        try:
            token = self.credential.get_token("https://graph.microsoft.com/.default")
            return token.token
        except Exception as e:
            self._log(f"Failed to get access token: {str(e)}", "error")
            raise
    
    def _make_request(self, method: str, url: str, data: Optional[Dict] = None, use_beta: bool = False) -> Dict:
        """Make HTTP request to Microsoft Graph API"""
        headers = {
            'Authorization': f'Bearer {self._get_access_token()}',
            'Content-Type': 'application/json'
        }
        
        endpoint = self.beta_endpoint if use_beta else self.graph_endpoint
        full_url = f"{endpoint}{url}"
        
        try:
            response = requests.request(method, full_url, headers=headers, json=data, timeout=30)
            
            if response.status_code not in [200, 201, 204]:
                error_detail = ""
                try:
                    error_json = response.json()
                    error_detail = json.dumps(error_json, indent=2)
                except:
                    error_detail = response.text
                    
                raise Exception(f"Request failed: {response.status_code} - {error_detail}")
            
            return response.json() if response.content else {}
        except requests.exceptions.Timeout:
            raise Exception("Request timed out after 30 seconds")
        except requests.exceptions.RequestException as e:
            raise Exception(f"Request failed: {str(e)}")
    
    def get_user_id_by_email(self, email: str) -> Optional[str]:
        """Get user ID by email address"""
        try:
            email = email.strip()
            if not email:
                return None
            
            # Method 1: Direct lookup
            try:
                url = f"/users/{email}"
                user = self._make_request("GET", url)
                return user.get('id')
            except:
                pass
            
            # Method 2: Filter search
            try:
                url = f"/users?$filter=mail eq '{email}' or userPrincipalName eq '{email}'"
                result = self._make_request("GET", url)
                if result.get('value') and len(result['value']) > 0:
                    return result['value'][0]['id']
            except:
                pass
            
            # Method 3: External user format
            try:
                external_format = email.replace('@', '_') + f"#EXT#@{self.tenant_id}.onmicrosoft.com"
                url = f"/users/{external_format}"
                user = self._make_request("GET", url)
                return user.get('id')
            except:
                pass
            
            return None
            
        except Exception as e:
            self._log(f"Error finding user {email}: {str(e)}", "error")
            return None

    def _wait_for_application(self, app_id: str, max_retries: int = 15, delay: int = 2) -> bool:
        """
        Wait for application registration to be fully available for owner operations
        """
        self._log(f"⏳ Waiting for application registration to be fully ready...", "info")
        
        for attempt in range(max_retries):
            try:
                # Try to read the application
                url = f"/applications/{app_id}"
                app = self._make_request("GET", url)
                
                # Try to read owners (this will fail if app isn't ready)
                owners_url = f"/applications/{app_id}/owners"
                owners = self._make_request("GET", owners_url)
                
                self._log(f"✅ Application registration is ready (attempt {attempt + 1})", "success")
                return True
                
            except Exception as e:
                if attempt < max_retries - 1:
                    self._log(f"⏳ Application not ready yet (attempt {attempt + 1}/{max_retries}), waiting {delay}s...", "info")
                    time.sleep(delay)
                else:
                    self._log(f"⚠️  Application may not be fully ready after {max_retries} attempts", "warning")
                    return False
        
        return False

    def _wait_for_service_principal(self, sp_id: str, max_retries: int = 20, delay: int = 2) -> bool:
        """
        Wait for service principal to be fully available
        Increased retries for better reliability
        """
        self._log(f"⏳ Waiting for service principal to be fully ready...", "info")
        
        for attempt in range(max_retries):
            try:
                # Try to read the service principal
                url = f"/servicePrincipals/{sp_id}"
                sp = self._make_request("GET", url)
                
                # Try to read owners (this will fail if SP isn't ready)
                owners_url = f"/servicePrincipals/{sp_id}/owners"
                owners = self._make_request("GET", owners_url)
                
                # Additional check: try to add a test reference to verify write access
                # (we won't actually add anything, just check if the endpoint is writable)
                
                self._log(f"✅ Service principal is ready (attempt {attempt + 1})", "success")
                
                # Add extra wait after first successful read for consistency
                if attempt < 3:
                    time.sleep(2)
                
                return True
                
            except Exception as e:
                if attempt < max_retries - 1:
                    # Progressive delay for first few attempts
                    actual_delay = delay if attempt < 5 else delay + 1
                    self._log(f"⏳ Service principal not ready yet (attempt {attempt + 1}/{max_retries}), waiting {actual_delay}s...", "info")
                    time.sleep(actual_delay)
                else:
                    self._log(f"⚠️  Service principal may not be fully ready after {max_retries} attempts", "warning")
                    # Even if not fully ready, give it one more chance with extra wait
                    time.sleep(5)
                    return False
        
        return False
    def _add_owner_to_application_with_retry(self, app_id: str, user_id: str, user_email: str, max_retries: int = 3) -> bool:
        """
        Add owner to application registration with retry logic for race conditions
        """
        for attempt in range(max_retries):
            try:
                # Check if user is already an owner
                owners_url = f"/applications/{app_id}/owners"
                existing_owners = self._make_request("GET", owners_url)
                existing_owner_ids = [owner['id'] for owner in existing_owners.get('value', [])]
                
                if user_id in existing_owner_ids:
                    self._log(f"ℹ️  {user_email} is already an owner", "info")
                    return True
                
                # Add owner
                owner_payload = {
                    "@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user_id}"
                }
                self._make_request("POST", f"/applications/{app_id}/owners/$ref", owner_payload)
                self._log(f"✅ Added {user_email} as owner", "success")
                return True
                
            except Exception as e:
                error_msg = str(e)
                
                # Handle "already exists" as success
                if "already exist" in error_msg.lower() or "already a member" in error_msg.lower():
                    self._log(f"ℹ️  {user_email} is already an owner", "info")
                    return True
                
                # Handle "resource not found" - means app not ready yet
                if "Request_ResourceNotFound" in error_msg or "does not exist" in error_msg:
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 2
                        self._log(f"⏳ Application not ready for owner {user_email}, waiting {wait_time}s... (attempt {attempt + 1}/{max_retries})", "info")
                        time.sleep(wait_time)
                        continue
                    else:
                        self._log(f"❌ Failed to add owner {user_email}: Application not available", "error")
                        return False
                
                # Other errors
                self._log(f"❌ Failed to add owner {user_email}: {error_msg}", "error")
                return False
        
        return False

    def _add_owner_to_service_principal_with_retry(self, sp_id: str, user_id: str, user_email: str, max_retries: int = 5) -> bool:
        """
        Add owner to service principal with retry logic for race conditions
        Increased retries and smarter waiting for newly created service principals
        """
        for attempt in range(max_retries):
            try:
                # Check if user is already an owner
                owners_url = f"/servicePrincipals/{sp_id}/owners"
                existing_owners = self._make_request("GET", owners_url)
                existing_owner_ids = [owner['id'] for owner in existing_owners.get('value', [])]
                
                if user_id in existing_owner_ids:
                    self._log(f"ℹ️  {user_email} is already an owner", "info")
                    return True
                
                # Add owner
                owner_payload = {
                    "@odata.id": f"https://graph.microsoft.com/v1.0/directoryObjects/{user_id}"
                }
                self._make_request("POST", f"/servicePrincipals/{sp_id}/owners/$ref", owner_payload)
                self._log(f"✅ Added {user_email} as owner", "success")
                return True
                
            except Exception as e:
                error_msg = str(e)
                
                # Handle "already exists" as success
                if "already exist" in error_msg.lower() or "already a member" in error_msg.lower():
                    self._log(f"ℹ️  {user_email} is already an owner", "info")
                    return True
                
                # Handle "resource not found" - means SP not ready yet
                if "Request_ResourceNotFound" in error_msg or "does not exist" in error_msg:
                    if attempt < max_retries - 1:
                        # Progressive backoff: wait longer with each attempt
                        wait_time = (attempt + 1) * 3  # 3s, 6s, 9s, 12s, 15s
                        self._log(f"⏳ Service principal not ready for owner {user_email}, waiting {wait_time}s... (attempt {attempt + 1}/{max_retries})", "info")
                        time.sleep(wait_time)
                        continue
                    else:
                        self._log(f"❌ Failed to add owner {user_email}: Service principal not available after {max_retries} attempts", "error")
                        return False
                
                # Other errors - also retry with backoff
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    self._log(f"⚠️  Error adding owner {user_email}, retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})", "warning")
                    time.sleep(wait_time)
                    continue
                
                self._log(f"❌ Failed to add owner {user_email}: {error_msg}", "error")
                return False
        
        return False
    def _add_owners_to_application(self, app_id: str, owner_emails: List[str], app_name: str = "") -> List[str]:
        """Add owners to application registration"""
        self._log(f"👥 Adding owners to App Registration{' (' + app_name + ')' if app_name else ''}...", "info")
        
        # Wait for application to be fully ready
        self._wait_for_application(app_id)
        
        successful_owners = []
        
        for email in owner_emails:
            email = email.strip()
            if not email:
                continue
            
            self._log(f"🔍 Looking up user: {email}", "info")
            user_id = self.get_user_id_by_email(email)
            
            if user_id:
                self._log(f"✅ Found user: {email} (ID: {user_id})", "success")
                if self._add_owner_to_application_with_retry(app_id, user_id, email):
                    successful_owners.append(email)
            else:
                self._log(f"⚠️  User not found in tenant: {email}", "warning")
        
        if successful_owners:
            self._log(f"✅ Successfully added {len(successful_owners)} owner(s) to App Registration", "success")
        else:
            self._log(f"⚠️  No owners were added to App Registration", "warning")
        
        return successful_owners

    def _add_owners_to_service_principal(self, sp_id: str, owner_emails: List[str], app_name: str = "") -> List[str]:
        """Add owners to service principal (enterprise application)"""
        self._log(f"👥 Adding owners to Enterprise Application{' (' + app_name + ')' if app_name else ''}...", "info")
        
        # Wait for service principal to be fully ready
        is_ready = self._wait_for_service_principal(sp_id)
        
        # Add extra stabilization time for newly created service principals
        if is_ready:
            self._log(f"⏳ Allowing extra stabilization time...", "info")
            time.sleep(3)
        
        successful_owners = []
        
        for email in owner_emails:
            email = email.strip()
            if not email:
                continue
            
            self._log(f"🔍 Looking up user: {email}", "info")
            user_id = self.get_user_id_by_email(email)
            
            if user_id:
                self._log(f"✅ Found user: {email} (ID: {user_id})", "success")
                if self._add_owner_to_service_principal_with_retry(sp_id, user_id, email):
                    successful_owners.append(email)
            else:
                self._log(f"⚠️  User not found in tenant: {email}", "warning")
        
        if successful_owners:
            self._log(f"✅ Successfully added {len(successful_owners)} owner(s) to Enterprise Application", "success")
        else:
            self._log(f"⚠️  No owners were added to Enterprise Application", "warning")
        
        return successful_owners
    def create_saml_enterprise_application(self, name: str, owner_emails: List[str]) -> Dict[str, Any]:
        """
        Create a non-gallery SAML Enterprise Application
        Just like portal: Enterprise Applications > New > Create your own application (Non-gallery)
        Only creates the enterprise app and adds owners - NO configuration
        """
        try:
            self._log(f"🛡️  Creating non-gallery SAML Enterprise Application: {name}", "info")
            
            # Use the Azure AD SAML Toolkit template (what portal uses)
            NON_GALLERY_TEMPLATE_ID = "8adf8e6e-67b2-4cf2-a259-e3dc5476c621"
            
            instantiate_payload = {
                "displayName": name
            }
            
            self._log("🔧 Creating enterprise application...", "info")
            result = self._make_request("POST", f"/applicationTemplates/{NON_GALLERY_TEMPLATE_ID}/instantiate", instantiate_payload)
            
            sp_info = result.get('servicePrincipal', {})
            sp_id = sp_info.get('id')
            app_id = sp_info.get('appId')
            
            self._log(f"✅ Enterprise Application created successfully!", "success")
            self._log(f"   Service Principal ID: {sp_id}", "info")
            self._log(f"   Application ID: {app_id}", "info")
            
            # Add owners ONLY to service principal (not to backing app registration)
            sp_owners = []
            if sp_id and owner_emails:
                sp_owners = self._add_owners_to_service_principal(sp_id, owner_emails, name)
            
            return {
                "service_principal": {
                    "id": sp_id,
                    "app_id": app_id,
                    "display_name": name,
                    "owners_added": sp_owners,
                    "type": "Non-Gallery SAML Enterprise Application"
                },
                "created_from_gallery": False,
                "display_name": name
            }
            
        except Exception as e:
            self._log(f"❌ Error creating SAML Enterprise Application: {str(e)}", "error")
            raise

    def create_app_registration(self, 
                              name: str, 
                              owner_emails: List[str], 
                              tenant_type: str = "single", 
                              redirect_urls: List[str] = None) -> Dict[str, Any]:
        """
        Create OIDC app registration with specified parameters
        """
        try:
            # Map tenant type
            if tenant_type.lower() in ["multi-tenant", "multiple", "multitenant"]:
                supported_account_types = "AzureADMultipleOrgs"
                self._log(f"🌐 Setting up Multi-Tenant application", "info")
            else:
                supported_account_types = "AzureADMyOrg"
                self._log(f"🏢 Setting up Single-Tenant application", "info")
            
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
                }
            }
            
            self._log(f"🔧 Creating OIDC app registration: {name}", "info")
            app_registration = self._make_request("POST", "/applications", app_payload)
            
            app_obj_id = app_registration['id']
            client_id = app_registration['appId']
            actual_signin_audience = app_registration.get('signInAudience', 'Unknown')
            
            self._log(f"✅ OIDC app registration created!", "success")
            self._log(f"   Application Object ID: {app_obj_id}", "info")
            self._log(f"   Application (Client) ID: {client_id}", "info")
            self._log(f"   Sign-in Audience: {actual_signin_audience}", "info")
            
            # Add owners to app registration (with wait for propagation built-in)
            app_owners = []
            if owner_emails:
                app_owners = self._add_owners_to_application(app_obj_id, owner_emails, name)
            
            # Create service principal (enterprise application)
            service_principal = self._create_service_principal_with_retry(client_id, owner_emails, name)
            
            return {
                "app_registration": {
                    "id": app_obj_id,
                    "client_id": client_id,
                    "name": name,
                    "tenant_type": tenant_type,
                    "signin_audience": actual_signin_audience,
                    "redirect_urls": web_redirect_uris,
                    "owners_added": app_owners,
                    "type": "OIDC App Registration"
                },
                "service_principal": service_principal
            }
            
        except Exception as e:
            self._log(f"❌ Error creating OIDC app registration: {str(e)}", "error")
            raise

    def _create_service_principal_with_retry(self, client_id: str, owner_emails: List[str], app_name: str = "", max_retries: int = 5) -> Dict[str, Any]:
        """Create service principal with retry logic"""
        
        for attempt in range(max_retries):
            try:
                return self._create_service_principal(client_id, owner_emails, app_name)
            except Exception as e:
                if "NoBackingApplicationObject" in str(e) and attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 3
                    self._log(f"⏳ Service principal creation failed, waiting {wait_time}s... (attempt {attempt + 1}/{max_retries})", "info")
                    time.sleep(wait_time)
                    continue
                else:
                    raise e
        
        raise Exception(f"Failed to create service principal after {max_retries} attempts")

    def _create_service_principal(self, client_id: str, owner_emails: List[str], app_name: str = "") -> Dict[str, Any]:
        """Create service principal for the OIDC app registration"""
        try:
            # Check if service principal already exists
            existing_sp_url = f"/servicePrincipals?$filter=appId eq '{client_id}'"
            existing_sp = self._make_request("GET", existing_sp_url)
            
            if existing_sp.get('value') and len(existing_sp['value']) > 0:
                self._log("ℹ️  Service principal already exists", "info")
                service_principal = existing_sp['value'][0]
                sp_id = service_principal['id']
            else:
                # Create new service principal
                sp_payload = {
                    "appId": client_id,
                    "tags": ["WindowsAzureActiveDirectoryIntegratedApp"]
                }
                
                self._log("🔧 Creating service principal...", "info")
                service_principal = self._make_request("POST", "/servicePrincipals", sp_payload)
                sp_id = service_principal['id']
                self._log(f"✅ Service principal created: {sp_id}", "success")
            
            # Add owners to service principal
            sp_owners = []
            if owner_emails:
                sp_owners = self._add_owners_to_service_principal(sp_id, owner_emails, app_name)
            
            return {
                "id": sp_id,
                "app_id": client_id,
                "display_name": service_principal.get('displayName', app_name),
                "owners_added": sp_owners,
                "type": "OIDC Service Principal"
            }
            
        except Exception as e:
            self._log(f"❌ Error creating service principal: {str(e)}", "error")
            raise

    def search_gallery_application(self, gallery_name: str) -> Optional[Dict[str, Any]]:
        """Search for application in Azure AD gallery"""
        try:
            self._log(f"🔍 Searching for '{gallery_name}' in Azure AD gallery...", "info")
            
            # Exact match
            url = f"/applicationTemplates?$filter=displayName eq '{gallery_name}'"
            result = self._make_request("GET", url)
            
            if result.get('value'):
                self._log(f"✅ Found exact match in gallery", "success")
                return result['value'][0]
            
            # Case-insensitive search
            url = f"/applicationTemplates?$filter=tolower(displayName) eq '{gallery_name.lower()}'"
            result = self._make_request("GET", url)
            
            if result.get('value'):
                self._log(f"✅ Found case-insensitive match in gallery", "success")
                return result['value'][0]
            
            # Contains search
            url = f"/applicationTemplates?$filter=contains(tolower(displayName), '{gallery_name.lower()}')"
            result = self._make_request("GET", url)
            
            if result.get('value'):
                similar_apps = [app['displayName'] for app in result['value'][:5]]
                self._log(f"🔍 Found similar applications: {', '.join(similar_apps)}", "info")
                return result['value'][0]
            
            self._log(f"❌ No applications found matching '{gallery_name}'", "error")
            return None
            
        except Exception as e:
            self._log(f"❌ Error searching gallery: {str(e)}", "error")
            return None

    def create_gallery_application(self, template_id: str, display_name: str, owner_emails: List[str]) -> Dict[str, Any]:
        """Create application from gallery template"""
        try:
            instantiate_payload = {
                "displayName": display_name
            }
            
            self._log(f"🔧 Creating gallery application: {display_name}", "info")
            result = self._make_request("POST", f"/applicationTemplates/{template_id}/instantiate", instantiate_payload)
            
            sp_info = result.get('servicePrincipal', {})
            sp_id = sp_info.get('id')
            app_id = sp_info.get('appId')
            
            self._log(f"✅ Gallery application created successfully!", "success")
            self._log(f"   Service Principal ID: {sp_id}", "info")
            self._log(f"   Application ID: {app_id}", "info")
            
            # Add owners ONLY to service principal (enterprise application)
            sp_owners = []
            if sp_id and owner_emails:
                sp_owners = self._add_owners_to_service_principal(sp_id, owner_emails, display_name)
            
            return {
                "service_principal": {
                    "id": sp_id,
                    "app_id": app_id,
                    "display_name": display_name,
                    "owners_added": sp_owners,
                    "type": "Gallery SAML Application"
                },
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
            search_name = gallery_name if gallery_name else name
            
            self._log(f"🔍 Gallery Mode: Searching for '{search_name}'...", "info")
            gallery_app = self.search_gallery_application(search_name)
            
            if gallery_app:
                self._log(f"✅ Found '{gallery_app['displayName']}' in gallery", "success")
                return self.create_gallery_application(gallery_app['id'], name, owner_emails)
            else:
                self._log(f"❌ '{search_name}' not found in gallery", "warning")
                self._log(f"🔄 Creating non-gallery SAML application instead", "info")
        
        # Create non-gallery SAML application
        return self.create_saml_enterprise_application(name, owner_emails)