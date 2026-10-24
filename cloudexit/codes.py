# utils/codes.py

SUCCESS = 0
UNEXPECTED = 1
CONFIG = 2
CREDENTIALS = 3
PERMISSIONS = 4
RESOURCE_INVENTORY = 5
COST_INVENTORY = 6
RISK_ASSESSMENT = 7
REPORT = 8
EGRESS = 9

# Upload to a platform (API v2)
# 10  the platform rejected the submitted data
# 11  the API key is missing, invalid or not allowed
# 12  the platform is unreachable or temporarily unavailable
# 13  a plan quota or rate limit was reached
# 14  the upload target was not found or is not accessible
# 15  the platform does not support this API version or feature
UPLOAD_REJECTED = 10
UPLOAD_AUTH = 11
UPLOAD_UNAVAILABLE = 12
UPLOAD_QUOTA = 13
UPLOAD_TARGET = 14
UPLOAD_UNSUPPORTED = 15

# Interrupted by the user (Ctrl+C)
INTERRUPTED = 130
