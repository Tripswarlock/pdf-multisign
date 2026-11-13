"""Verify a CMS produced on Windows through the Linux CryptoPro backend."""
import sys

from pdfsign import crypto

content = open("/home/helpdesk/win-content.bin", "rb").read()
signature = open("/home/helpdesk/win-signature.sig", "rb").read()

print("content bytes:", len(content), "cms bytes:", len(signature))
ok, message = crypto.verify_detached(signature, content)
print("VERIFY:", ok)
print("MESSAGE:", message)

tampered = content[:-1] + bytes([content[-1] ^ 0xFF])
ok_bad, message_bad = crypto.verify_detached(signature, tampered)
print("TAMPERED VERIFY:", ok_bad)
print("TAMPERED MESSAGE:", message_bad)

sys.exit(0 if ok and not ok_bad else 1)
