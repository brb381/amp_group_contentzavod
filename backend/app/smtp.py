import smtplib
from email.message import EmailMessage


def validate_smtp_transport(
    *, environment: str, host: str, from_email: str, use_tls: bool, use_ssl: bool,
    username: str | None, password: str | None,
) -> None:
    if bool(username) != bool(password):
        raise ValueError("SMTP_USERNAME and SMTP_PASSWORD must be set together")
    if use_tls and use_ssl:
        raise ValueError("SMTP_USE_TLS and SMTP_USE_SSL cannot both be enabled")
    if environment == "production":
        if host.lower() in {"mailpit", "localhost", "127.0.0.1"}:
            raise ValueError("Production SMTP_HOST must point to a real mail service")
        if not (use_tls or use_ssl):
            raise ValueError("Production SMTP must use STARTTLS or SMTP_SSL")
        if from_email.lower().endswith((".test", ".local")):
            raise ValueError("Production SMTP_FROM_EMAIL must use a real domain")


def send_smtp_message(
    message: EmailMessage,
    *,
    host: str,
    port: int,
    use_tls: bool,
    use_ssl: bool,
    username: str | None,
    password: str | None,
    timeout: int,
) -> None:
    client_type = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with client_type(host, port, timeout=timeout) as client:
        if use_tls:
            client.starttls()
        if username and password:
            client.login(username, password)
        client.send_message(message)
