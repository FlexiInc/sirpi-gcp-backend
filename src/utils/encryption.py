"""
Encryption utilities for sensitive data storage.
Uses Fernet (AES-256) for symmetric encryption.
"""

import base64
import logging
from cryptography.fernet import Fernet
from src.core.config import settings

logger = logging.getLogger(__name__)


class EncryptionService:
    """Service for encrypting/decrypting sensitive data."""
    
    def __init__(self):
        """Initialize encryption service with master key."""
        self._cipher = None
    
    @property
    def cipher(self) -> Fernet:
        """Get or create Fernet cipher."""
        if self._cipher is None:
            # Get master key from environment
            master_key = getattr(settings, 'encryption_master_key', None)
            
            if not master_key:
                # Generate a key for development (DON'T DO THIS IN PRODUCTION!)
                logger.warning("No ENCRYPTION_MASTER_KEY set - generating temporary key")
                master_key = Fernet.generate_key().decode()
            
            # Ensure key is bytes
            if isinstance(master_key, str):
                master_key = master_key.encode()
            
            self._cipher = Fernet(master_key)
        
        return self._cipher
    
    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt plaintext string.
        
        Args:
            plaintext: Data to encrypt
            
        Returns:
            Encrypted string (base64 encoded)
        """
        if not plaintext:
            return ""
        
        try:
            encrypted_bytes = self.cipher.encrypt(plaintext.encode())
            return encrypted_bytes.decode()
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            raise ValueError("Failed to encrypt data")
    
    def decrypt(self, encrypted: str) -> str:
        """
        Decrypt encrypted string.
        
        Args:
            encrypted: Encrypted data (base64 encoded)
            
        Returns:
            Decrypted plaintext
        """
        if not encrypted:
            return ""
        
        try:
            decrypted_bytes = self.cipher.decrypt(encrypted.encode())
            return decrypted_bytes.decode()
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            raise ValueError("Failed to decrypt data")
    
    def encrypt_dict(self, data: dict) -> dict:
        """Encrypt all values in a dictionary."""
        return {k: self.encrypt(str(v)) for k, v in data.items()}
    
    def decrypt_dict(self, encrypted_data: dict) -> dict:
        """Decrypt all values in a dictionary."""
        return {k: self.decrypt(v) for k, v in encrypted_data.items()}


# Global singleton
_encryption_service: EncryptionService = None


def get_encryption_service() -> EncryptionService:
    """Get or create singleton encryption service."""
    global _encryption_service
    
    if _encryption_service is None:
        _encryption_service = EncryptionService()
    
    return _encryption_service


# Convenience functions
def encrypt_value(plaintext: str) -> str:
    """Encrypt a value."""
    return get_encryption_service().encrypt(plaintext)


def decrypt_value(encrypted: str) -> str:
    """Decrypt a value."""
    return get_encryption_service().decrypt(encrypted)
