"""Read Cloud Run's attached identity before creating candidate storage/KMS clients."""
from contextlib import closing
from http.client import HTTPConnection, HTTPException
import os


HOST = "metadata.google.internal"
ROOT = "/computeMetadata/v1/"
MAX_FIELD = 256


def verify_attached_identity(deployment):
    # google-auth's Compute credentials must use the same platform metadata
    # server as this check when they subsequently request their access token.
    for variable in ("GCE_METADATA_HOST", "GCE_METADATA_ROOT", "GCE_METADATA_IP"):
        override = os.environ.get(variable)
        allowed = ("169.254.169.254",) if variable == "GCE_METADATA_IP" else (HOST, "169.254.169.254")
        if override and override not in allowed:
            raise ValueError("alternate metadata endpoint is not permitted")
    fields = (
        ("instance/service-accounts/default/email", deployment["service_account"]),
        ("project/project-id", deployment["project"]),
        ("project/numeric-project-id", deployment["project_number"]),
        ("instance/region", "projects/" + deployment["project_number"] + "/regions/asia-east1"),
    )
    for path, expected in fields:
        try:
            # A direct connection does not use proxy environment variables.
            # http.client never follows redirects or retries a failed request.
            with closing(HTTPConnection(HOST, timeout=3)) as conn:
                conn.request("GET", ROOT + path, headers={"Metadata-Flavor":"Google", "Accept-Encoding":"identity"})
                response = conn.getresponse()
                if (response.status != 200 or response.getheader("Metadata-Flavor") != "Google"
                        or response.getheader("Content-Encoding", "identity") != "identity"):
                    raise ValueError("unverified metadata reply")
                size = response.getheader("Content-Length")
                if size is not None and (not size.isascii() or not size.isdigit() or int(size) > MAX_FIELD):
                    raise ValueError("unbounded metadata reply")
                value = response.read(MAX_FIELD + 1)
                if (len(value) > MAX_FIELD or (size is not None and len(value) != int(size))
                        or value.decode("ascii").rstrip("\r\n") != expected):
                    raise ValueError("metadata differs")
        except (OSError, HTTPException, ValueError):
            # Do not propagate provider bodies, tokens, or unexpected identifiers.
            raise ValueError("attached Cloud Run " + path + " not confirmed") from None
