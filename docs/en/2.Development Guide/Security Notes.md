# Security Notes

openJiuwen has the capability to integrate external systems: it can interoperate with large language model services and third-party plugin services, enabling developers to build intelligent agent applications that are both intelligent and practical—fully leveraging the core strengths of large language models while seamlessly interacting with external resources.

## Security Risk Description

- **Secure Transmission and Authentication**: Communication between the openJiuwen framework and external services (starting with `http`) lacks identity authentication and secure channels, posing risks such as man-in-the-middle attacks, information leakage, and privilege escalation.

- **Server-Side Request Forgery**: Application services integrated with the openJiuwen framework may be exploited by attackers to induce the server to send unauthorized requests to specified addresses, steal internal resources, or attack intranet vulnerabilities.

- **Insecure Implementation of Custom Components**: The openJiuwen framework supports developer-defined custom components. During their development and implementation, unsafe operations by developers may introduce security risks.

## Best Practices

- **SSL Verification**: When calling large language model services and third-party plugin services, it is recommended to enable SSL verification to ensure communication security. At this time, you need to configure local certificate paths for `LLM_SSL_CERT`, `EMBEDDING_SSL_CERT` and `RESTFUL_SSL_CERT` respectively; for external services that provide `HTTPS` interfaces, if the certificate path is not configured, the framework will throw an exception prompting the user to configure a local certificate path. Unless in local self-verification scenarios, it is not recommended to disable SSL verification or call external services starting with `http`.

- **SSRF Protection**: When calling large language model services and third-party plugin services, it is recommended to enable SSRF protection. The framework will block access to local IPs and intranet addresses (such as the `10.0.0.0`-`10.255.255.255` range) to defend against server-side request forgery attacks. Only for local testing that needs to access sensitive addresses may you set `SSRF_PROTECT_ENABLED` to `false`. It must be enabled in production to prevent attackers from inducing the server to send unauthorized requests, steal resources, or attack the internal network.

- **Custom Components**: Developers should follow sound security design principles when implementing custom components: control access permissions according to the actual needs of the application, such as using read-only credentials; developers should assume that system access privileges or related credentials may be abused and build a multi-layered security defense system, rather than relying on any single defensive measure.