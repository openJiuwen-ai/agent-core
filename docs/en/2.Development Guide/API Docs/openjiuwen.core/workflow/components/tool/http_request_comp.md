# HTTP Request Component for openJiuwen

The HTTP Request Component is a powerful workflow component that provides extensive HTTP client functionality similar to n8n's HTTP Request node. It allows you to make HTTP requests to external services and APIs as part of your agent workflows.

## Summary of Implementation

As of February 11, 2026, the HTTP Request Component has been successfully implemented with the following key features:

### Core Architecture
- Inherits from `ComponentComposable` and `ComponentExecutable` as required by the openJiuwen framework
- Fully compatible with the workflow execution engine
- Follows the same patterns as other components in the framework

### Implemented Features
- **HTTP Methods**: Full support for GET, POST, PUT, PATCH, DELETE, etc.
- **Authentication**: Basic Auth, Bearer Token, API Key authentication
- **Request Bodies**: JSON, Form data, Multipart form, Text, and Binary data support
- **Response Handling**: Auto-detect, JSON, Text, and Binary response processing
- **Advanced Options**: SSL control, Proxy support, Timeout configuration
- **Retry Mechanism**: Configurable retries with exponential backoff
- **Security**: URL validation, SSL verification, secure credential handling

### Files Created
- `openjiuwen/core/workflow/components/http/http_request_component.py` - Main implementation
- `openjiuwen/core/workflow/components/http/__init__.py` - Module exports
- Updated `openjiuwen/core/workflow/__init__.py` - Added HTTP component to exports

### Testing
- Basic functionality verified through import and instantiation tests
- Comprehensive configuration tests passed
- Example usage demonstrated with multiple scenarios

## Features

### HTTP Methods
- Supports all standard HTTP methods (GET, POST, PUT, PATCH, DELETE, etc.)
- Configurable request method per component instance

### Authentication
- **None**: No authentication
- **Basic Auth**: Username and password authentication
- **Bearer Token**: Token-based authentication
- **API Key**: Custom API key authentication in header, query, or body
- More authentication methods can be added (Digest, OAuth2, AWS Signature)

### Request Body Options
- **JSON**: Send JSON payloads with automatic serialization
- **Form Data**: Send URL-encoded form data
- **Multipart Form**: Send multipart form data (useful for file uploads)
- **Text**: Send plain text data
- **Binary**: Send binary data

### Response Handling
- **Auto-detect**: Automatically detect response format based on Content-Type
- **JSON**: Parse response as JSON
- **Text**: Treat response as plain text
- **Binary**: Handle response as binary data
- Configurable success/error status codes
- Response filtering and property extraction

### Advanced Options
- SSL certificate validation control
- Proxy support
- Request timeout configuration
- Redirect following control
- Compression settings
- Maximum body length limits

### Retry Mechanism
- Configurable retry attempts
- Selective retry on specific status codes (e.g., 429, 500, 502, 503, 504)
- Multiple backoff strategies (fixed, linear, exponential)
- Customizable retry delays

### Rate Limiting
- Configurable request rate limits
- Time-based rate limiting (per second, minute, hour)

## Usage Examples

### Basic GET Request
```python
from openjiuwen.core.workflow import (
    HTTPRequestComponent,
    HttpComponentConfig,
    HttpRequestParamConfig
)

# Create configuration for a simple GET request
config = HttpComponentConfig(
    request_params=HttpRequestParamConfig(
        url="https://api.example.com/data",
        method="GET",
        headers={
            "User-Agent": "openJiuwen HTTP Component",
            "Accept": "application/json"
        },
        timeout=30.0
    )
)

# Create the HTTP component
http_component = HTTPRequestComponent(config=config)
```

### POST Request with JSON Body
```python
from openjiuwen.core.workflow import HttpRequestBodyConfig, HttpContentType

config = HttpComponentConfig(
    request_params=HttpRequestParamConfig(
        url="https://api.example.com/users",
        method="POST",
        headers={"Content-Type": "application/json"},
        body=HttpRequestBodyConfig(
            content_type=HttpContentType.JSON,
            json_data={
                "name": "John Doe",
                "email": "john@example.com",
                "age": 30
            }
        ),
        response_handling=HttpResponseHandlingConfig(
            response_format="json",
            response_mode="full"
        )
    )
)

http_component = HTTPRequestComponent(config=config)
```

### Authenticated Request
```python
from openjiuwen.core.workflow import HttpAuthConfig, HttpAuthType

config = HttpComponentConfig(
    request_params=HttpRequestParamConfig(
        url="https://api.example.com/protected-endpoint",
        method="GET",
        authentication=HttpAuthConfig(
            type=HttpAuthType.BEARER,
            token="your-jwt-token-here"
        )
    )
)

http_component = HTTPRequestComponent(config=config)
```

### Request with Retry Logic
```python
from openjiuwen.core.workflow import HttpRetryConfig

config = HttpComponentConfig(
    request_params=HttpRequestParamConfig(
        url="https://api.example.com/reliable-endpoint",
        method="GET",
        retry_config=HttpRetryConfig(
            enabled=True,
            max_retries=3,
            retry_on_status_codes=[500, 502, 503, 504],
            retry_delay=2000,  # 2 seconds in milliseconds
            backoff_type="exponential"
        )
    )
)

http_component = HTTPRequestComponent(config=config)
```

## Integration with Workflows

The HTTP Request Component follows the openJiuwen component architecture and can be seamlessly integrated into workflows:

```python
config = HttpComponentConfig(
request_params=HttpRequestParamConfig(
    url="{{url}}",  # URL will be dynamically set from input named 'url'
        method="GET"
    ),
    advanced_options=HttpAdvancedOptionsConfig(ignore_ssl_issues=True)  # Disable SSL verification for testing
)
http_component = HTTPRequestComponent(config=config)
        # Set up the workflow connections
flow.set_start_comp("s", start_component, inputs_schema={"query": "${query}"})
flow.set_end_comp("e", end_component, inputs_schema={"output": "${http.output}"})
flow.add_workflow_comp("http", http_component, inputs_schema={"url": "${s.query}"})

# Add connections: start -> http -> end
flow.add_connection("s", "http")
flow.add_connection("http", "e")

# Create session context
context = create_workflow_session()

# Invoke the workflow with a test URL
result = await flow.invoke(inputs={"query": "https://httpbin.org/get?test=value"}, session=context)
```

## Error Handling

The component provides comprehensive error handling:
- Network timeouts
- Connection errors
- HTTP error status codes
- Invalid response formats
- Authentication failures

Errors are propagated through the openJiuwen error handling system with appropriate status codes.

## Security Considerations

- SSL/TLS validation is enabled by default
- Support for custom certificates
- Secure credential handling
- Protection against SSRF attacks through URL validation

## Performance Considerations

- Asynchronous request processing
- Connection pooling
- Configurable timeouts to prevent hanging requests
- Binary response size limits to prevent memory exhaustion