# openjiuwen.core.common.security.crypt_utils

加解密抽象层，提供可扩展的加密器注册表和内置 AES-256-GCM 实现。

## class BaseCrypt

```python
class openjiuwen.core.common.security.crypt_utils.BaseCrypt
```

加解密器抽象基类，定义统一的加解密接口契约。所有自定义加解密实现均需继承此类。

### encrypt

```python
@abstractmethod
def encrypt(self, key: bytes, origin: str) -> str
```

加密原始字符串，返回密文。

**参数**：

* **key** (bytes)：加密密钥，长度由具体实现决定（AES-GCM 要求 32 字节）。
* **origin** (str)：待加密的原始字符串。

**返回**：

**str**，加密后的密文字符串。

### decrypt

```python
@abstractmethod
def decrypt(self, key: bytes, encrypt_str: str) -> str
```

解密密文字符串，返回明文。

**参数**：

* **key** (bytes)：解密密钥，须与加密时使用的密钥一致。
* **encrypt_str** (str)：待解密的密文字符串。

**返回**：

**str**，解密后的原始明文字符串。

---

## class AesGcmCrypt

```python
class openjiuwen.core.common.security.crypt_utils.AesGcmCrypt(BaseCrypt)
```

内置 AES-256-GCM 加解密实现，采用单例模式（`metaclass=Singleton`）。基于 `pycryptodome` 库，使用 32 字节密钥、12 字节 Nonce、16 字节 Tag。

> **说明**：
> - `AesGcmCrypt` 为线程安全单例，全局唯一实例。首次构造时自动注册到 `CryptUtils` 注册表（名称为 `"aes_gcm"`）。
> - 密钥不在实例中存储，而是在每次 `encrypt` / `decrypt` 调用时由调用方传入。
> - 加密输出格式为 `nonce_hex + tag_hex + ciphertext_hex` 拼接字符串。
> - 每次加密使用随机 Nonce，因此相同明文和密钥会产生不同密文。

### encrypt

```python
def encrypt(self, key: bytes, origin: str) -> str
```

使用 AES-256-GCM 加密明文。

**参数**：

* **key** (bytes)：AES 密钥，必须恰好 32 字节，否则抛出 `BaseError`（COMMON_ENCRYPTION_ERROR）。
* **origin** (str)：待加密的原始字符串。

**返回**：

**str**，拼接的十六进制密文字符串，格式为 `nonce_hex + tag_hex + ciphertext_hex`。

**异常**：

* **BaseError**（COMMON_ENCRYPTION_ERROR）：密钥长度不为 32 字节时抛出。

### decrypt

```python
def decrypt(self, key: bytes, encrypt_str: str) -> str
```

使用 AES-256-GCM 解密密文。

**参数**：

* **key** (bytes)：AES 密钥，必须恰好 32 字节，否则抛出 `BaseError`（COMMON_ENCRYPTION_ERROR）。
* **encrypt_str** (str)：密文字符串，格式为 `nonce_hex + tag_hex + ciphertext_hex`，长度至少 56 个十六进制字符。

**返回**：

**str**，解密后的原始明文字符串。

**异常**：

* **BaseError**（COMMON_ENCRYPTION_ERROR）：密钥长度不为 32 字节时抛出。
* **BaseError**（COMMON_DECRYPTION_ERROR）：密文过短、Nonce 长度异常、Tag 长度异常或解密验证失败时抛出。

**样例**：

```python
from openjiuwen.core.common.security.crypt_utils import AesGcmCrypt, CryptUtils

# 创建单例（首次构造时自动注册到 CryptUtils）
crypt = AesGcmCrypt()

# 加解密
key = b"0123456789abcdef0123456789abcdef"  # 32 字节密钥
ciphertext = crypt.encrypt(key, "Hello, World!")
plaintext = crypt.decrypt(key, ciphertext)
print(plaintext)  # "Hello, World!"

# 通过 CryptUtils 注册表获取同一实例
same_crypt = CryptUtils.get_crypt("aes_gcm")
print(same_crypt is crypt)  # True
```

---

## class CryptUtils

```python
class openjiuwen.core.common.security.crypt_utils.CryptUtils
```

加解密工具门面类，管理加密器注册表（Key-Value 模式）。提供加密器的注册、注销、查找功能。使用线程安全的 `_registry_lock` 保护全局注册表。

**类属性**：

| 属性 | 类型 | 说明 |
|------|------|------|
| **AES_GCM_CRYPT_NAME** | str | 内置 AES-GCM 加密器的注册名称，值为 `"aes_gcm"` |

### register_crypt

```python
@staticmethod
def register_crypt(name: str, crypt: BaseCrypt) -> None
```

注册命名加解密实例到全局注册表。直接注册实例（非工厂），因为 Crypt 实例通常有状态。

**参数**：

* **name** (str)：加解密实例的唯一名称标识。
* **crypt** (BaseCrypt)：加解密实例，必须是 `BaseCrypt` 的子类实例。

**异常**：

* **BaseError**（COMMON_ENCRYPTION_ERROR）：`crypt` 不是 `BaseCrypt` 实例时抛出。

### unregister_crypt

```python
@staticmethod
def unregister_crypt(name: str) -> None
```

从全局注册表移除指定名称的加解密实例。若名称不存在则静默忽略。

**参数**：

* **name** (str)：要移除的加解密实例名称。

### get_crypt

```python
@staticmethod
def get_crypt(name: str) -> Optional[BaseCrypt]
```

按名称获取加解密实例。

**参数**：

* **name** (str)：加解密实例的名称标识。

**返回**：

**Optional[BaseCrypt]**，对应的加解密实例；未注册时返回 `None`。

**样例**：

```python
from openjiuwen.core.common.security.crypt_utils import CryptUtils, AesGcmCrypt, BaseCrypt

# 方式一：使用内置 AES-GCM（自动注册）
aes_crypt = AesGcmCrypt()  # 构造时自动注册为 "aes_gcm"

# 方式二：注册自定义加解密实现
class Sm4Crypt(BaseCrypt):
    def encrypt(self, key: bytes, origin: str) -> str:
        # SM4 加密实现
        ...

    def decrypt(self, key: bytes, encrypt_str: str) -> str:
        # SM4 解密实现
        ...

sm4 = Sm4Crypt()
CryptUtils.register_crypt("sm4", sm4)

# 按名称获取加解密实例
crypt = CryptUtils.get_crypt("sm4")
if crypt:
    ciphertext = crypt.encrypt(key, "sensitive data")

# 注销加解密实例
CryptUtils.unregister_crypt("sm4")
```

---

## 与 BaseMemoryManager 的集成

`BaseMemoryManager` 中的 `encrypt_memory_if_needed` 和 `decrypt_memory_if_needed` 静态方法是 `CryptUtils` 的上层消费者，在记忆存储/读取时自动执行加密/解密。

### 加密流程

```
encrypt_memory_if_needed(key, plaintext) ->
  plaintext 为空 -> 直接返回 plaintext
  key 为空 -> 直接返回 plaintext
  CryptUtils.get_crypt("aes_gcm") 为 None -> 直接返回 plaintext
  crypt.encrypt(key, plaintext) -> 返回密文字符串
  加密异常 -> 记录 warning 日志 -> 返回 plaintext
```

### 解密流程

```
decrypt_memory_if_needed(key, ciphertext) ->
  ciphertext 为空 -> 直接返回 ciphertext
  key 为空 -> 直接返回 ciphertext
  CryptUtils.get_crypt("aes_gcm") 为 None -> 直接返回 ciphertext
  crypt.decrypt(key, ciphertext) -> 返回明文字符串
  解密异常 -> 记录 warning 日志 -> 返回 ciphertext
```
