from google.oauth2.service_account import Credentials
import json
import os

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## SECRET ACCESS FUNCTION
## Reads a secret as a dictionary. A path to a JSON file on this computer is read from disk;
## a Secret Manager name (projects/.../secrets/.../versions/...) is fetched from GCP.
## Inputs: secret_name (a local JSON file path, or the name of the secret in Secret Manager)
## Outputs: A dictionary containing the secret values
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def get_secret(secret_name):
  if not secret_name.startswith("projects/"):
    try:
      with open(os.path.expanduser(secret_name), encoding = "utf-8") as handle:
        return json.load(handle)
    except Exception as e:
      raise Exception(f"Error in `get_secret`: could not read {secret_name}: {e}")

  try:
    # Only loaded for Secret Manager names, so reading a local file does not need it installed.
    from google.cloud import secretmanager
    secret_client = secretmanager.SecretManagerServiceClient()
    credential_return = secret_client.access_secret_version(name = secret_name)
    return json.loads(credential_return.payload.data.decode('UTF-8'))
  except Exception as e:
    raise Exception(f"Error in `get_secret`: {secret_name}: {e}")

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## CREDENTIALS ACCESS FUNCTION
## Function to access credentials from a service file and return them as a Credentials object
## Inputs: service_file (the service account file or dictionary containing the credentials)
## Outputs: A Credentials object
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def get_credentials(service_file, scopes = ["https://www.googleapis.com/auth/cloud-platform", "https://www.googleapis.com/auth/bigquery", "https://www.googleapis.com/auth/drive", "https://www.googleapis.com/auth/spreadsheets"]):
  # Gets credentials from a service file
  try:
    return Credentials.from_service_account_info(service_file, scopes = scopes)
  except Exception as e:
    raise Exception(f"Error in `get_credentials`: {e}")

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## BQ RUNNER FUNCTION
## Function to run a BigQuery query using credentials from get_secret; credentials are handled in here
## Inputs: query (a string SQL query to execute); credentials_secret_name (a local JSON file path or the Secret Manager name holding the credentials to access BigQuery)
## Outputs: An iterable row object containing the query results
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def run_query(query, credentials_secret_name = 'projects/<GCP_PROJECT>/secrets/<SERVICE_ACCOUNT_SECRET>/versions/latest'):
  # Only loaded when a query runs: BigQuery pulls in pandas and pyarrow, which the packet scripts never need.
  from google.cloud import bigquery

  warehouse_credentials = get_secret(credentials_secret_name)

  credentials = get_credentials(warehouse_credentials)
  client = bigquery.Client(credentials = credentials, project = credentials.project_id)
  try:
    query_job = client.query(query)
    return query_job.result() # This returns an iterable row object
  except Exception as e:
    raise Exception(f"Error in `run_query`: {e}")