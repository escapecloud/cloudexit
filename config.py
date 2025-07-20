# config.py
"""
Configuration for integrating the local 'cloudexit' tool with the ExitCloud Platform (exitcloud.io).
This enables assessment extension and secure result storage in your selected data region.

HOST:
  EU → "eu.exitcloud.io"
  US → "us.exitcloud.io"

KEY:
To generate a key:
  1. Log in to your regional portal (https://eu.exitcloud.io or https://us.exitcloud.io).
  2. Click your user profile (top right corner).
  3. Select 'Keys' from the menu.
  4. Click 'New Key' and copy the provided key.

Please do not modify CLI_VERSION; it is used for debugging purposes.
"""
CLI_VERSION = "v1.0.0"

HOST = ""
KEY = ""
