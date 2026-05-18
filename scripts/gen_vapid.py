"""Generate a VAPID keypair for Web Push.

Run once and paste the output into your .env / Coolify secrets:

    python scripts/gen_vapid.py
"""
import base64

from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization


def main():
    key = ec.generate_private_key(ec.SECP256R1())

    # Private key as base64url-encoded DER bytes (single line, env-var friendly)
    priv_der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    priv_b64 = base64.urlsafe_b64encode(priv_der).rstrip(b"=").decode()

    # Public key as base64url-encoded uncompressed point (65 bytes)
    pub_bytes = key.public_key().public_bytes(
        serialization.Encoding.X962,
        serialization.PublicFormat.UncompressedPoint,
    )
    pub_b64 = base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()

    print(f"VAPID_PUBLIC_KEY={pub_b64}")
    print(f"VAPID_PRIVATE_KEY={priv_b64}")
    print()
    print("# Paste both lines into your .env file (or Coolify secrets).")
    print("# IMPORTANT: rotating these keys invalidates all existing push subscriptions.")
    print("# If you rotate, truncate the push_subscriptions table so users re-subscribe.")


if __name__ == "__main__":
    main()
