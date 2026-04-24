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
    def assign_users_and_groups(self, sp_id: str, users: List[str], groups: List[str]) -> Dict[str, Any]:
        """Assign users and groups to a service principal (enterprise app)"""
        assigned_users = []
        assigned_groups = []

        # Dynamically find the correct appRoleId from the SP's actual app roles
        # The all-zeros UUID is NOT a real app role — we must use the actual enabled role
        app_role_id = self._get_default_app_role_id(sp_id)
        self._log(f"ℹ️ Using appRoleId: {app_role_id}", "info")

        for email in users:
            email = email.strip()
            if not email:
                continue
            user_id = self.get_user_id_by_email(email)
            if user_id:
                try:
                    payload = {"principalId": user_id, "resourceId": sp_id, "appRoleId": app_role_id}
                    self._make_request("POST", f"/servicePrincipals/{sp_id}/appRoleAssignments", payload)
                    self._log(f"✅ Assigned user {email}", "success")
                    assigned_users.append(email)
                except Exception as e:
                    if "already assigned" in str(e).lower():
                        assigned_users.append(email)
                    else:
                        self._log(f"⚠️ Could not assign user {email}: {str(e)}", "warning")
            else:
                self._log(f"⚠️ User not found: {email}", "warning")

        for group in groups:
            group = group.strip()
            if not group:
                continue
            try:
                group_id = None
                if len(group) == 36 and '-' in group:
                    group_id = group
                else:
                    res = self._make_request("GET", f"/groups?$filter=displayName eq '{group}'")
                    if res.get('value'):
                        group_id = res['value'][0]['id']
                if group_id:
                    payload = {"principalId": group_id, "resourceId": sp_id, "appRoleId": app_role_id}
                    self._make_request("POST", f"/servicePrincipals/{sp_id}/appRoleAssignments", payload)
                    self._log(f"✅ Assigned group {group}", "success")
                    assigned_groups.append(group)
                else:
                    self._log(f"⚠️ Group not found: {group}", "warning")
            except Exception as e:
                if "already assigned" in str(e).lower():
                    assigned_groups.append(group)
                else:
                    self._log(f"⚠️ Could not assign group {group}: {str(e)}", "warning")

        return {"users": assigned_users, "groups": assigned_groups}

    def _get_default_app_role_id(self, sp_id: str) -> str:
        """
        Return the ID of the first enabled app role on the service principal.
        If none exist, add one directly to the SP and return its ID.
        """
        import uuid
        try:
            sp_data = self._make_request("GET", f"/servicePrincipals/{sp_id}?$select=appRoles")
            sp_roles = sp_data.get('appRoles', [])
            enabled = [r for r in sp_roles if r.get('isEnabled', True) and r.get('id')]
            if enabled:
                role_id = enabled[0]['id']
                self._log(f"ℹ️ Found existing app role: {enabled[0].get('displayName', 'Unknown')} ({role_id})", "info")
                return role_id

            # To add an app role, it must be added to the backing Application, not the SP directly
            import time
            sp_info = self._make_request("GET", f"/servicePrincipals/{sp_id}?$select=appId")
            app_id = sp_info.get("appId")
            if not app_id:
                raise Exception("Could not retrieve appId from SP")
            
            app_resp = self._make_request("GET", f"/applications?$filter=appId eq '{app_id}'&$select=id,appRoles")
            if not app_resp.get('value'):
                raise Exception(f"Could not find backing Application for appId {app_id}")
                
            app_obj = app_resp['value'][0]
            app_obj_id = app_obj['id']
            app_roles = app_obj.get('appRoles', [])
            
            new_role_id = str(uuid.uuid4())
            new_role = {
                "allowedMemberTypes": ["User"],
                "description": "Default Access",
                "displayName": "Default Access",
                "id": new_role_id,
                "isEnabled": True,
                "value": "User"
            }
            
            self._make_request("PATCH", f"/applications/{app_obj_id}", {"appRoles": app_roles + [new_role]})
            self._log(f"✅ Default role added to Application: {new_role_id}", "success")
            time.sleep(3)  # Wait for sync to SP
            return new_role_id
        except Exception as e:
            self._log(f"⚠️ Could not determine app role ID: {str(e)}", "warning")
            return "00000000-0000-0000-0000-000000000000"

    def add_api_permissions(self, app_obj_id: str, permissions: List[str]) -> List[str]:
        """Add Microsoft Graph delegated API permissions to an app registration"""
        if not permissions:
            return []
        # Get MS Graph service principal
        try:
            ms_graph_sp_res = self._make_request("GET", "/servicePrincipals?$filter=appId eq '00000003-0000-0000-c000-000000000000'")
            ms_graph_sp = ms_graph_sp_res.get('value', [{}])[0] if ms_graph_sp_res.get('value') else {}
            graph_sp_id = ms_graph_sp.get('id', '')
            # Build case-insensitive lookup maps (lower(name) -> {id, original_name})
            oauth2_perms = {p['value']: p['id'] for p in ms_graph_sp.get('oauth2PermissionScopes', [])}
            app_role_perms = {p['value']: p['id'] for p in ms_graph_sp.get('appRoles', [])}
            oauth2_lower = {k.lower(): (k, v) for k, v in oauth2_perms.items()}
            app_role_lower = {k.lower(): (k, v) for k, v in app_role_perms.items()}
        except Exception as e:
            self._log(f"⚠️ Could not fetch MS Graph scopes: {e}", "warning")
            return []
        required_scopes = []
        added = []
        for perm in permissions:
            perm = perm.strip()
            if not perm:
                continue
            perm_lower = perm.lower()
            perm_nodot = perm_lower.replace('.', '')  # e.g. "user.readall" -> "userreadall"

            if perm in oauth2_perms:
                required_scopes.append({"id": oauth2_perms[perm], "type": "Scope"})
                added.append(perm)
            elif perm in app_role_perms:
                required_scopes.append({"id": app_role_perms[perm], "type": "Role"})
                added.append(perm)
            elif perm_lower in oauth2_lower:
                orig_name, perm_id = oauth2_lower[perm_lower]
                required_scopes.append({"id": perm_id, "type": "Scope"})
                added.append(orig_name)
                self._log(f"ℹ️ Permission '{perm}' matched as '{orig_name}' (delegated)", "info")
            elif perm_lower in app_role_lower:
                orig_name, perm_id = app_role_lower[perm_lower]
                required_scopes.append({"id": perm_id, "type": "Role"})
                added.append(orig_name)
                self._log(f"ℹ️ Permission '{perm}' matched as '{orig_name}' (application)", "info")
            else:
                # Fuzzy fallback: match by stripping dots (e.g. "user.readall" -> "User.Read.All")
                fuzzy_match = None
                for canon_lower, (orig_name, perm_id) in oauth2_lower.items():
                    if canon_lower.replace('.', '') == perm_nodot:
                        fuzzy_match = ('Scope', orig_name, perm_id)
                        break
                if not fuzzy_match:
                    for canon_lower, (orig_name, perm_id) in app_role_lower.items():
                        if canon_lower.replace('.', '') == perm_nodot:
                            fuzzy_match = ('Role', orig_name, perm_id)
                            break
                if fuzzy_match:
                    ptype, orig_name, perm_id = fuzzy_match
                    required_scopes.append({"id": perm_id, "type": ptype})
                    added.append(orig_name)
                    self._log(f"ℹ️ Permission '{perm}' fuzzy-matched as '{orig_name}' ({ptype})", "info")
                else:
                    self._log(f"⚠️ Permission not found in MS Graph: {perm}", "warning")
        if required_scopes:
            try:
                patch_payload = {"requiredResourceAccess": [{"resourceAppId": "00000003-0000-0000-c000-000000000000", "resourceAccess": required_scopes}]}
                self._make_request("PATCH", f"/applications/{app_obj_id}", patch_payload)
                self._log(f"✅ Added {len(added)} API permission(s): {', '.join(added)}", "success")
            except Exception as e:
                self._log(f"⚠️ Could not set API permissions: {e}", "warning")
        return added

    def configure_saml_sso(self, sp_id: str, entity_id: str, acs_url: str) -> bool:
        """Configure SAML SSO settings on a service principal — each step is independent."""
        if not entity_id and not acs_url:
            return False
        configured = False

        # Step 1: Set preferredSingleSignOnMode on the SP
        try:
            self._make_request("PATCH", f"/servicePrincipals/{sp_id}", {
                "preferredSingleSignOnMode": "saml",
                "samlSingleSignOnSettings": {"relayState": None}
            })
            configured = True
        except Exception as e:
            self._log(f"⚠️ Could not set SSO mode to SAML: {str(e)}", "warning")

        # Step 2: Fetch backing application
        app_obj_id = None
        try:
            sp_data = self._make_request("GET", f"/servicePrincipals/{sp_id}")
            app_id_val = sp_data.get('appId', '')
            if app_id_val:
                apps = self._make_request("GET", f"/applications?$filter=appId eq '{app_id_val}'")
                if apps.get('value'):
                    app_obj_id = apps['value'][0]['id']
        except Exception as e:
            self._log(f"⚠️ Could not fetch backing application: {str(e)}", "warning")

        if not app_obj_id:
            self._log("⚠️ Backing application not found — entity ID and ACS URL not configured", "warning")
            return configured

        # Step 3: Set Entity ID (separate patch so ACS URL still works if this fails)
        if entity_id:
            # Reject urn:spn: namespace (Graph doesn't allow it) — strip to bare value, prefix https://
            if entity_id.lower().startswith('urn:spn:'):
                bare = entity_id[8:]  # strip 'urn:spn:'
                entity_id = f"https://{bare}"
                self._log(f"ℹ️ urn:spn: prefix replaced with https://: {entity_id}", "info")
            elif not (entity_id.startswith('https://') or entity_id.startswith('http://')
                    or entity_id.startswith('api://') or entity_id.startswith('urn:')):
                entity_id = f"https://{entity_id}"
                self._log(f"ℹ️ Entity ID auto-prefixed: {entity_id}", "info")
            try:
                self._make_request("PATCH", f"/applications/{app_obj_id}", {"identifierUris": [entity_id]})
                self._log(f"✅ Entity ID configured: {entity_id}", "success")
                configured = True
            except Exception as e:
                self._log(f"⚠️ Entity ID ({entity_id}) could not be set: {str(e)}", "warning")

        # Step 4: Set ACS URL (separate patch)
        if acs_url:
            try:
                self._make_request("PATCH", f"/applications/{app_obj_id}", {"web": {"redirectUris": [acs_url]}})
                self._log(f"✅ ACS URL configured: {acs_url}", "success")
                configured = True
            except Exception as e:
                self._log(f"⚠️ ACS URL could not be set: {str(e)}", "warning")

        return configured



    def _ensure_default_app_role(self, sp_id: str) -> bool:
        """
        Ensure the default access app role (00000000-...) exists on the service principal.
        Checks SP's appRoles first (faster), then patches the backing application if needed.
        """
        DEFAULT_ROLE_ID = "00000000-0000-0000-0000-000000000000"
        try:
            # 1. Check the SP's appRoles directly (template apps often have it here already)
            sp_data = self._make_request("GET", f"/servicePrincipals/{sp_id}?$select=appRoles,appId")
            sp_roles = sp_data.get('appRoles', [])
            if any(r.get('id') == DEFAULT_ROLE_ID and r.get('isEnabled', True) for r in sp_roles):
                self._log("ℹ️ Default access role already present on SP", "info")
                return True

            app_id_val = sp_data.get('appId', '')
            if not app_id_val:
                return False

            # 2. Fetch backing application
            apps = self._make_request("GET", f"/applications?$filter=appId eq '{app_id_val}'&$select=id,appRoles")
            if not apps.get('value'):
                return False
            app_obj_id = apps['value'][0]['id']
            existing_roles = apps['value'][0].get('appRoles', [])

            if any(r.get('id') == DEFAULT_ROLE_ID for r in existing_roles):
                return True  # Already on application

            # 3. Filter existing to only those with valid non-empty UUIDs (avoid Graph rejecting empty id)
            valid_existing = [
                r for r in existing_roles
                if r.get('id') and len(str(r['id'])) == 36
            ]

            new_role = {
                "allowedMemberTypes": ["User"],
                "description": "Default Access",
                "displayName": "Default Access",
                "id": DEFAULT_ROLE_ID,
                "isEnabled": True,
                "value": "User"
            }
            self._make_request("PATCH", f"/applications/{app_obj_id}", {"appRoles": valid_existing + [new_role]})
            time.sleep(3)  # Wait for propagation to SP
            self._log("✅ Default access role added to application", "success")
            return True
        except Exception as e:
            self._log(f"⚠️ Could not add default app role: {str(e)}", "warning")
            return False

    def create_saml_enterprise_application(self, name: str, owner_emails: List[str], entity_id: str = '', acs_url: str = '', users: List[str] = None, groups: List[str] = None) -> Dict[str, Any]:
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
            
            # Configure SAML Entity ID and ACS URL
            if sp_id and (entity_id or acs_url):
                time.sleep(3)
                self.configure_saml_sso(sp_id, entity_id, acs_url)

            # Ensure default app role exists before assigning users/groups
            if sp_id and (users or groups):
                time.sleep(2)
                self._ensure_default_app_role(sp_id)
                time.sleep(2)
            
            # Assign users and groups
            assigned = {"users": [], "groups": []}
            if sp_id and (users or groups):
                self._log("👥 Assigning users and groups...", "info")
                assigned = self.assign_users_and_groups(sp_id, users or [], groups or [])
            
            # Federated metadata URL for SAML
            tenant_id_val = self.tenant_id if hasattr(self, 'tenant_id') else ''
            fed_metadata_url = ''
            if app_id and tenant_id_val:
                fed_metadata_url = (
                    f"https://login.microsoftonline.com/{tenant_id_val}/federationmetadata/2007-06/"
                    f"federationmetadata.xml?appid={app_id}"
                )

            return {
                "service_principal": {
                    "id": sp_id,
                    "app_id": app_id,
                    "display_name": name,
                    "owners_added": sp_owners,
                    "assigned_users": assigned.get("users", []),
                    "assigned_groups": assigned.get("groups", []),
                    "entity_id": entity_id,
                    "acs_url": acs_url,
                    "type": "Non-Gallery SAML Enterprise Application"
                },
                "federated_metadata_url": fed_metadata_url,
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
                              redirect_urls: List[str] = None,
                              api_permissions: List[str] = None,
                              users: List[str] = None,
                              groups: List[str] = None) -> Dict[str, Any]:
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

            # Add API permissions
            added_perms = []
            if api_permissions:
                self._log(f"🔑 Adding API permissions...", "info")
                added_perms = self.add_api_permissions(app_obj_id, api_permissions)

            # Assign users and groups to enterprise app
            assigned = {"users": [], "groups": []}
            if service_principal and (users or groups):
                self._log("👥 Assigning users and groups...", "info")
                assigned = self.assign_users_and_groups(service_principal.get('id', ''), users or [], groups or [])

            return {
                "app_registration": {
                    "id": app_obj_id,
                    "client_id": client_id,
                    "name": name,
                    "display_name": name,
                    "tenant_type": tenant_type,
                    "signin_audience": actual_signin_audience,
                    "redirect_urls": web_redirect_uris,
                    "owners_added": app_owners,
                    "api_permissions_added": added_perms,
                    "type": "OIDC App Registration"
                },
                "service_principal": service_principal,
                "assigned_users": assigned.get("users", []),
                "assigned_groups": assigned.get("groups", [])
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
                                gallery_name: str = None,
                                entity_id: str = '',
                                acs_url: str = '',
                                users: List[str] = None,
                                groups: List[str] = None) -> Dict[str, Any]:
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
        return self.create_saml_enterprise_application(name, owner_emails, entity_id=entity_id, acs_url=acs_url, users=users, groups=groups)