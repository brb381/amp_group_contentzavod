import re
import string

from app.notifications.catalog import DEFAULT_NOTIFICATION_TEMPLATES


CYRILLIC = re.compile(r"[А-Яа-яЁё]")


def test_default_notification_templates_are_russian_and_valid() -> None:
    assert len(DEFAULT_NOTIFICATION_TEMPLATES) == 19
    for code, template in DEFAULT_NOTIFICATION_TEMPLATES.items():
        text = f"{template['title']} {template['body']}"
        identifiers = set(string.Template(text).get_identifiers())

        assert CYRILLIC.search(template["title"]), code
        assert CYRILLIC.search(template["body"]), code
        assert identifiers <= set(template["variables"]), code


def test_user_messages_do_not_expose_raw_status_codes() -> None:
    status_templates = {
        code: template
        for code, template in DEFAULT_NOTIFICATION_TEMPLATES.items()
        if "status" in template["variables"]
    }

    assert status_templates
    assert all("$status" not in template["title"] for template in status_templates.values())
    assert all("$status" not in template["body"] for template in status_templates.values())
