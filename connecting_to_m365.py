import msal
import requests
import json


with open('app_credentials.json', 'r') as file:
    credentials = json.load(file)

# Azure AD app details
CLIENT_ID = credentials['CLIENT_ID']
TENANT_ID = credentials['TENANT_ID']
SCOPES = credentials['SCOPES'] # permissions
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
z
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

headers = {"Authorization": f"Bearer {access_token}"}
graph_api_endpoint = "https://graph.microsoft.com/v1.0/me/drive/root/children"
response = requests.get(graph_api_endpoint, headers=headers)

with open("graph_response.json", "w") as f:
    json.dump(response.json(), f, indent=4)

files = response.json().get('value', [])
for file in files:
    print(file['name'], file['id'])



