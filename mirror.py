import requests
import os
import json
import msal


global BASE_DIR, metadata_path

def fetch_files_recursively(access_token, folder_id='root'):
    headers = {"Authorization": f"Bearer {access_token}"}
    endpoint = f"https://graph.microsoft.com/v1.0/me/drive/items/{folder_id}/children" # HERE IS 'me' AS A USER

    all_items = []
    response = requests.get(endpoint, headers=headers)
    response.raise_for_status()

    items = response.json().get('value', [])
    for item in items:
        all_items.append(item)
        if 'folder' in item:  # it's a folder
            all_items.extend(fetch_files_recursively(access_token, item['id']))
    return all_items


def get_local_path(item):
    # Use 'parentReference.path' to reconstruct folder hierarchy
    # Example: "/drive/root:/Documents/Work"
    path = item['parentReference']['path'].replace('/drive/root:', '')
    local_path = os.path.join(BASE_DIR, path.strip('/'), item['name'])
    return local_path


def ensure_local_folder(path):
    if not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


def download_file(item):
    if 'file' not in item:
        return  # skip folders
    local_path = get_local_path(item)
    ensure_local_folder(os.path.dirname(local_path))

    download_url = item['@microsoft.graph.downloadUrl']
    r = requests.get(download_url)
    with open(local_path, 'wb') as f:
        f.write(r.content)
    print(f"Downloaded: {local_path}")


def save_metadata(items):
    with open(metadata_path, 'w') as f:
        json.dump(items, f, indent=4)


def sync_user_drive(access_token, user_id):
    all_items = fetch_files_recursively(access_token)

    metadata = {}  # load from metadata.json if exists
    new_metadata = {}

    for item in all_items:
        local_path = get_local_path(item)
        if item['id'] not in metadata or metadata[item['id']]['lastModifiedDateTime'] != item['lastModifiedDateTime']:
            download_file(item)
        new_metadata[item['id']] = {
            "name": item['name'],
            "path": local_path,
            "lastModifiedDateTime": item['lastModifiedDateTime'],
            # Add more fields if needed
        }

    save_metadata(new_metadata)


with open('app_credentials.json', 'r') as file:
    credentials = json.load(file)

# Azure AD app details
CLIENT_ID = credentials['CLIENT_ID']
TENANT_ID = credentials['TENANT_ID']
SCOPES = credentials['SCOPES'] # permissions
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"

# Create a public client application
app = msal.PublicClientApplication(client_id=CLIENT_ID, authority=AUTHORITY)

# Device code flow
flow = app.initiate_device_flow(scopes=SCOPES)
print(flow['message'])  # Follow the instructions in the terminal

result = app.acquire_token_by_device_flow(flow)  # Blocks until authenticated

try:
    access_token = result['access_token']
    print("Authentication successful")
except Exception as e:
    print("Authentication failed")
    access_token = None

USER_ID = result['id_token_claims']['name']
BASE_DIR = f"/opt/thalamind/{USER_ID}/onedrive"
metadata_path = os.path.join(BASE_DIR, "metadata.json")

sync_user_drive(access_token=access_token, user_id=USER_ID)
