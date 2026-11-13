from requests.adapters import HTTPAdapter
from urllib3.util import Retry
import requests

def request_with_retry(
    url,
    headers,
    auth,
    json_data = None,
    method = "GET",
    timeout = 30,
    total_retries = 5,
    backoff_factor = 2.0,
    status_forcelist = (400, 429, 500, 502, 503, 504),
    read_retries = None,
):
    """Generic HTTP Request engine with retry and exponential backoff for transient errors.

    Pass read_retries=0 for non-idempotent POSTs: a read timeout means the server may have already applied it.
    """
    method_upper = method.upper()

    retry_strategy = Retry(
        total = total_retries,
        connect = total_retries,
        read = total_retries if read_retries is None else read_retries,
        status = total_retries,
        backoff_factor = backoff_factor,
        status_forcelist = status_forcelist,
        allowed_methods = frozenset([method_upper]),
        respect_retry_after_header = True,
        raise_on_status = False,
    )

    adapter = HTTPAdapter(max_retries = retry_strategy)

    with requests.Session() as session:
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        
        response = session.request(
            method = method_upper,
            url = url, 
            headers = headers, 
            auth = auth, 
            json = json_data,
            timeout = timeout
        )

        if response.text:
            try:
                response.raise_for_status()
            except requests.exceptions.HTTPError as http_error:
                # APIs explain a rejected request only in the body, which raise_for_status drops.
                raise requests.exceptions.HTTPError(
                    f"{http_error} -- response body: {response.text[:2000]}",
                    response = response,
                ) from http_error
        else:
            response.raise_for_status()
        return response

def get_with_retry(url, headers, auth, **kwargs):
    """HTTP GET wrapper around request_with_retry."""
    return request_with_retry(
        method="GET",
        url=url,
        headers=headers,
        auth=auth,
        **kwargs
    )


def post_with_retry(url, headers, auth, json_data=None, **kwargs):
    """HTTP POST wrapper around request_with_retry supporting JSON payloads."""
    return request_with_retry(
        method="POST",
        url=url,
        headers=headers,
        auth=auth,
        json_data=json_data,
        **kwargs
    )

