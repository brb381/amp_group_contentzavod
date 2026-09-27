"""Publish Russian notification templates.

Revision ID: 0036_russian_notifications
Revises: 0035_two_hour_view_collection
"""

import json
import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "0036_russian_notifications"
down_revision: Union[str, Sequence[str], None] = "0035_two_hour_view_collection"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TEMPLATES = {
    "account_suspension_warning": ("Скоро аккаунт будет приостановлен", "До приостановки аккаунта из-за отсутствия активности осталось $days дн. Дата приостановки: $deadline.", ["days", "deadline"]),
    "account_suspended": ("Аккаунт приостановлен", "Аккаунт приостановлен из-за отсутствия активности. Для возобновления работы отправьте обращение в поддержку.", []),
    "account_block_warning": ("Скоро аккаунт будет заблокирован", "До блокировки приостановленного аккаунта осталось $days дн. Дата блокировки: $deadline.", ["days", "deadline"]),
    "account_fully_blocked": ("Аккаунт заблокирован", "Аккаунт заблокирован. На балансе остались невыплаченные средства. Обратитесь в поддержку.", ["balance_kopecks"]),
    "account_recovery_approved": ("Доступ к аккаунту восстановлен", "Аккаунт снова активен. Публикации необходимо повторно отправить на проверку.", []),
    "account_recovery_rejected": ("Восстановление аккаунта отклонено", "Запрос на восстановление отклонён. Причина указана в обращении в поддержку.", []),
    "registration_created": ("Регистрация завершена", "Подтвердите электронную почту, чтобы активировать аккаунт.", []),
    "email_verified": ("Почта подтверждена", "Адрес электронной почты успешно подтверждён.", []),
    "profile_status_changed": ("Статус профиля изменён", "Результат проверки профиля доступен в личном кабинете.", ["status"]),
    "publication_submitted": ("Новая публикация на проверке", "Блогер отправил публикацию на модерацию.", ["publication_id"]),
    "publication_status_changed": ("Статус публикации изменён", "Результат проверки публикации доступен в её карточке.", ["publication_id", "status"]),
    "reading_status_changed": ("Показание просмотров проверено", "Результат проверки показания доступен в разделе просмотров.", ["reading_id", "status"]),
    "calculation_confirmed": ("Расчёт за период подтверждён", "Расчёт за период $period подтверждён. Начисление доступно в разделе выплат.", ["amount_kopecks", "period"]),
    "payout_status_changed": ("Статус выплаты изменён", "По заявке $request_number есть обновление. Откройте заявку, чтобы посмотреть подробности.", ["request_number", "status"]),
    "support_ticket_created": ("Новое обращение $ticket_number", "Создано обращение: $subject.", ["ticket_number", "subject"]),
    "support_blogger_message": ("Новый ответ в обращении $ticket_number", "Блогер ответил в обращении «$subject».", ["ticket_number", "subject"]),
    "support_staff_message": ("Ответ поддержки в обращении $ticket_number", "В обращении «$subject» появился новый ответ поддержки.", ["status", "subject", "ticket_number"]),
    "support_ticket_assigned": ("Вам назначено обращение $ticket_number", "Вы назначены ответственным за обращение «$subject».", ["ticket_number", "subject"]),
    "support_status_changed": ("Статус обращения $ticket_number изменён", "В обращении «$subject» изменился статус. Откройте обращение, чтобы посмотреть подробности.", ["status", "subject", "ticket_number"]),
}


def _template_id(code: str, channel: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"amp:notification:{code}:{channel}:ru:2")


def upgrade() -> None:
    connection = op.get_bind()
    deactivate = sa.text(
        "UPDATE notification_template_versions SET is_active = false "
        "WHERE code = :code AND channel = :channel AND version = 1 AND is_active = true"
    )
    insert = sa.text(
        "INSERT INTO notification_template_versions "
        "(id, code, channel, version, title_template, subject_template, body_template, "
        "allowed_variables, is_active, created_by_user_id) VALUES "
        "(:id, :code, :channel, 2, :title, :subject, :body, :variables, true, NULL)"
    )
    for code, (heading, body, variables) in TEMPLATES.items():
        for channel in ("in_app", "email"):
            result = connection.execute(deactivate, {"code": code, "channel": channel})
            if result.rowcount != 1:
                continue
            values = {
                "id": str(_template_id(code, channel)),
                "code": code,
                "channel": channel,
                "title": heading if channel == "in_app" else None,
                "subject": heading if channel == "email" else None,
                "body": body,
                "variables": json.dumps(variables),
            }
            if connection.dialect.name == "postgresql":
                statement = sa.text(str(insert).replace(":id", "CAST(:id AS uuid)").replace(":variables", "CAST(:variables AS jsonb)"))
                connection.execute(statement, values)
            else:
                connection.execute(insert, values)


def downgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        op.execute("ALTER TABLE notification_template_versions DISABLE TRIGGER trg_notification_template_version_guard")
    for code in TEMPLATES:
        for channel in ("in_app", "email"):
            connection.execute(sa.text("DELETE FROM notification_template_versions WHERE id = :id"), {"id": _template_id(code, channel)})
            connection.execute(
                sa.text("UPDATE notification_template_versions SET is_active = true WHERE code = :code AND channel = :channel AND version = 1"),
                {"code": code, "channel": channel},
            )
    if connection.dialect.name == "postgresql":
        op.execute("ALTER TABLE notification_template_versions ENABLE TRIGGER trg_notification_template_version_guard")
