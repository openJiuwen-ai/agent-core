# openjiuwen.core.common.security.crypt_utils

Encryption/decryption abstraction layer providing an extensible crypt registry and a built-in AES-256-GCM implementation.

## class BaseCrypt

```python
class openjiuwen.core.common.security.crypt_utils.BaseCrypt
```

Abstract base class for encryption/decryption, defining a unified interface contract. All custom encryption implementations must inherit from this class.

### encrypt

```python
@abstractmethod
def encrypt(self, key: bytes, origin: str) -> str
```

Encrypt a plaintext string and return the ciphertext.

**Parameters**:

* **key** (bytes): Encryption key. The length is determined by the specific implementation (AES-GCM requires 32 bytes).
* **origin** (str): The plaintext string to encrypt.

**Returns**:

**str**, the encrypted ciphertext string.

### decrypt

```python
@abstractmethod
def decrypt(self, key: bytes, encrypt_str: str) -> str
```

Decrypt a ciphertext string and return the plaintext.

**Parameters**:

* **key** (bytes): Decryption key. Must match the key used during encryption.
* **encrypt_str** (str): The ciphertext string to decrypt.

**Returns**:

**str**, the decrypted plaintext string.

---

## class AesGcmCrypt

```python
class openjiuwen.core.common.security.crypt_utils.AesGcmCrypt(BaseCrypt)
```

Built-in AES-256-GCM encryption/decryption implementation using the singleton pattern (`metaclass=Singleton`). Based on the `pycryptodome` library, using a 32-byte key, 12-byte Nonce, and 16-byte Tag.

> **Notes**:
> - `AesGcmCrypt` is a thread-safe singleton with a globally unique instance. Upon first construction, it automatically registers itself in the `CryptUtils` registry under the name `"aes_gcm"`.
> - The key is not stored in the instance; it is passed by the caller on each `encrypt` / `decrypt` invocation.
> - The encryption output format is a concatenated hex string: `nonce_hex + tag_hex + ciphertext_hex`.
> - Each encryption uses a random Nonce, so the same plaintext and key will produce different ciphertexts.

### encrypt

```python
def encrypt(self, key: bytes, origin: str) -> str
```

Encrypt plaintext using AES-256-GCM.

**Parameters**:

* **key** (bytes): AES key, must be exactly 32 bytes. Otherwise, raises `BaseError` (COMMON_ENCRYPTION_ERROR).
* **origin** (str): The plaintext string to encrypt.

**Returns**:

**str**, a concatenated hexadecimal ciphertext string in the format `nonce_hex + tag_hex + ciphertext_hex`.

**Raises**:

* **BaseError** (COMMON_ENCRYPTION_ERROR): Raised when the key length is not 32 bytes.

### decrypt

```python
def decrypt(self, key: bytes, encrypt_str: str) -> str
```

Decrypt ciphertext using AES-256-GCM.

**Parameters**:

* **key** (bytes): AES key, must be exactly 32 bytes. Otherwise, raises `BaseError` (COMMON_ENCRYPTION_ERROR).
* **encrypt_str** (str): Ciphertext string in the format `nonce_hex + tag_hex + ciphertext_hex`, with a minimum length of 56 hex characters.

**Returns**:

**str**, the decrypted plaintext string.

**Raises**:

* **BaseError** (COMMON_ENCRYPTION_ERROR): Raised when the key length is not 32 bytes.
* **BaseError** (COMMON_DECRYPTION_ERROR): Raised when the ciphertext is too short, the Nonce length is abnormal, the Tag length is abnormal, or decryption verification fails.

**Example**:

```python
from openjiuwen.core.common.security.crypt_utils import AesGcmCrypt, CryptUtils

# Create singleton (automatically registers with CryptUtils on first construction)
crypt = AesGcmCrypt()

# Encrypt and decrypt
key = b"0123456789abcdef0123456789abcdef"  # 32-byte key
ciphertext = crypt.encrypt(key, "Hello, World!")
plaintext = crypt.decrypt(key, ciphertext)
print(plaintext)  # "Hello, World!"

# Retrieve the same instance via CryptUtils registry
same_crypt = CryptUtils.get_crypt("aes_gcm")
print(same_crypt is crypt)  # True
```

---

## class CryptUtils

```python
class openjiuwen.core.common.security.crypt_utils.CryptUtils
```

Encryption/decryption utility facade class that manages the crypt registry (Key-Value pattern). Provides registration, unregistration, and lookup of crypt instances. Uses a thread-safe `_registry_lock` to protect the global registry.

**Class Attributes**:

| Attribute | Type | Description |
|-----------|------|-------------|
| **AES_GCM_CRYPT_NAME** | str | Registration name for the built-in AES-GCM crypt, value is `"aes_gcm"` |

### register_crypt

```python
@staticmethod
def register_crypt(name: str, crypt: BaseCrypt) -> None
```

Register a named encryption/decryption instance in the global registry. Registers an instance directly (not a factory), since Crypt instances are typically stateful.

**Parameters**:

* **name** (str): Unique name identifier for the crypt instance.
* **crypt** (BaseCrypt): The crypt instance, must be a subclass instance of `BaseCrypt`.

**Raises**:

* **BaseError** (COMMON_ENCRYPTION_ERROR): Raised when `crypt` is not a `BaseCrypt` instance.

### unregister_crypt

```python
@staticmethod
def unregister_crypt(name: str) -> None
```

Remove a named encryption/decryption instance from the global registry. Silently ignores names that do not exist.

**Parameters**:

* **name** (str): The name of the crypt instance to remove.

### get_crypt

```python
@staticmethod
def get_crypt(name: str) -> Optional[BaseCrypt]
```

Retrieve a crypt instance by name.

**Parameters**:

* **name** (str): The name identifier of the crypt instance.

**Returns**:

**Optional[BaseCrypt]**, the corresponding crypt instance; returns `None` if not registered.

**Example**:

```python
from openjiuwen.core.common.security.crypt_utils import CryptUtils, AesGcmCrypt, BaseCrypt

# Method 1: Use built-in AES-GCM (auto-registered)
aes_crypt = AesGcmCrypt()  # Automatically registered as "aes_gcm" on construction

# Method 2: Register a custom encryption implementation
class Sm4Crypt(BaseCrypt):
    def encrypt(self, key: bytes, origin: str) -> str:
        # SM4 encryption implementation
        ...

    def decrypt(self, key: bytes, encrypt_str: str) -> str:
        # SM4 decryption implementation
        ...

sm4 = Sm4Crypt()
CryptUtils.register_crypt("sm4", sm4)

# Retrieve crypt instance by name
crypt = CryptUtils.get_crypt("sm4")
if crypt:
    ciphertext = crypt.encrypt(key, "sensitive data")

# Unregister a crypt instance
CryptUtils.unregister_crypt("sm4")
```

---

## Integration with BaseMemoryManager

The `encrypt_memory_if_needed` and `decrypt_memory_if_needed` static methods in `BaseMemoryManager` are upper-level consumers of `CryptUtils`, automatically performing encryption/decryption during memory storage/retrieval.

### Encryption Flow

```
encrypt_memory_if_needed(key, plaintext) ->
  plaintext is empty -> return plaintext directly
  key is empty -> return plaintext directly
  CryptUtils.get_crypt("aes_gcm") is None -> return plaintext directly
  crypt.encrypt(key, plaintext) -> return ciphertext string
  Encryption exception -> log warning -> return plaintext
```

### Decryption Flow

```
decrypt_memory_if_needed(key, ciphertext) ->
  ciphertext is empty -> return ciphertext directly
  key is empty -> return ciphertext directly
  CryptUtils.get_crypt("aes_gcm") is None -> return ciphertext directly
  crypt.decrypt(key, ciphertext) -> return plaintext string
  Decryption exception -> log warning -> return ciphertext
```
