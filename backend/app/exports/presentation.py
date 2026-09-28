from enum import Enum
from typing import Any


PLATFORM_LABELS = {
    "youtube": "YouTube", "tiktok": "TikTok", "vk": "VK Видео",
    "rutube": "RUTUBE", "instagram": "Instagram", "dzen": "Дзен",
}
STATUS_LABELS = {
    "email_pending": "Почта не подтверждена", "active": "Активен",
    "suspended": "Приостановлен", "blocked": "Заблокирован", "deleted": "Удалён",
    "draft": "Черновик", "submitted": "Отправлено на проверку",
    "in_review": "На проверке", "pending_review": "На проверке",
    "pending": "Ожидает проверки", "changes_required": "Нужны исправления",
    "approved": "Одобрено", "accepted": "Принято", "rejected": "Отклонено",
    "inactive": "Неактивно", "re_review_required": "Нужна повторная проверка",
    "requested": "Заявка создана", "under_review": "На проверке", "paid": "Выплачено",
    "new": "Новое", "in_progress": "В работе",
    "waiting_blogger": "Ожидает ответа блогера", "resolved": "Решено", "closed": "Закрыто",
}
RECIPIENT_LABELS = {"individual": "Физическое лицо", "self_employed": "Самозанятый"}
ROLE_LABELS = {
    "blogger": "Блогер", "moderator": "Модератор", "manager": "Менеджер",
    "finance": "Финансы", "analyst": "Аналитик", "admin": "Администратор",
}
SOURCE_LABELS = {
    "manual": "Вручную", "youtube_api": "YouTube API",
    "tiktok_public": "Открытые данные TikTok", "vk_public": "Открытые данные VK Видео",
    "rutube_public": "Открытые данные RUTUBE",
    "instagram_public": "Открытые данные Instagram", "dzen_public": "Открытые данные Дзена",
}
AVAILABILITY_LABELS = {
    "unknown": "Не проверено", "available": "Доступно", "unavailable": "Недоступно",
}
RISK_LABELS = {
    "views_decreased": "Количество просмотров уменьшилось",
    "unusual_growth": "Необычно быстрый рост просмотров",
    "large_growth": "Резкий рост просмотров",
    "random_review": "Выбрано для выборочной проверки",
    "approximate_public_counter": "Данные из открытого счётчика площадки",
}
CATEGORY_LABELS = {
    "general": "Общий вопрос", "content": "Контент и публикации", "payment": "Выплаты",
    "technical": "Техническая проблема", "account_recovery": "Восстановление аккаунта",
}
OBJECT_LABELS = {
    "user": "Пользователь", "refresh_session": "Сессия пользователя",
    "creator_profile": "Профиль блогера", "profile": "Профиль блогера",
    "social_account": "Социальная площадка", "product": "Товар",
    "video_card": "Карточка ролика", "publication": "Публикация",
    "view_reading": "Показания просмотров", "rate_version": "Ставка оплаты",
    "calculation_period": "Расчётный период", "accrual_correction": "Корректировка начисления",
    "payout_details": "Платёжные реквизиты", "payout_request": "Заявка на выплату",
    "export_job": "Выгрузка данных", "support_ticket": "Обращение",
    "notification_template_version": "Шаблон уведомления",
    "legal_document": "Юридический документ", "legal_acceptance": "Согласие пользователя",
    "program_settings": "Настройки программы",
}
EVENT_LABELS = {
    "created": "Создано", "updated": "Изменено", "submitted": "Отправлено на проверку",
    "reviewed": "Проверено", "approved": "Одобрено", "rejected": "Отклонено",
    "deleted": "Удалено", "restored": "Восстановлено", "edited": "Исправлено",
    "corrected": "Скорректировано", "accepted": "Принято",
    "auto_accepted": "Принято автоматически",
    "social_account_created": "Социальная площадка добавлена",
    "social_account_restored": "Социальная площадка восстановлена",
    "social_account_updated": "Социальная площадка изменена",
    "social_account_deleted": "Социальная площадка удалена",
    "social_account_approved": "Социальная площадка одобрена",
    "social_account_rejected": "Социальная площадка отклонена",
    "profile_avatar_updated": "Аватар профиля изменён",
    "social_account_changed": "Социальная площадка изменена",
    "social_account_approve": "Социальная площадка одобрена",
    "publication_created": "Публикация создана",
    "publication_submitted": "Публикация отправлена на проверку",
    "publication_approve": "Публикация одобрена",
}
RESULT_LABELS = {"success": "Успешно", "failure": "Ошибка", "denied": "Отказано"}
BOOLEAN_LABELS = {"yes": "Да", "no": "Нет", "not_applicable": "Не требуется"}
JSON_KEY_LABELS = {
    "amount_kopecks": "Сумма, коп.", "channel": "Канал", "code": "Код",
    "content_sha256": "Контрольная сумма файла", "decision": "Решение",
    "document_type": "Тип документа", "email_identity": "Идентификатор почты",
    "export_type": "Тип выгрузки", "fields": "Изменённые поля", "filters": "Фильтры",
    "format": "Формат", "from_status": "Исходный статус", "new_value": "Новое значение",
    "old_value": "Предыдущее значение", "parse_status": "Статус обработки",
    "payment_due_date": "Срок оплаты", "period": "Период", "platform": "Площадка",
    "previous_session_id": "Предыдущая сессия", "previous_status": "Предыдущий статус",
    "product_resolved": "Товар определён", "product_source": "Источник товара",
    "publication_id": "ID публикации", "reason": "Причина", "request_number": "Номер заявки",
    "row_count": "Количество строк", "schema_version": "Версия схемы",
    "to_status": "Новый статус", "token_identity": "Идентификатор ссылки", "version": "Версия",
    "avatar": "Аватар", "social_account": "Социальная площадка", "source": "Источник",
}
JSON_VALUE_LABELS = {
    "email": "Электронная почта", "account_block_warning": "Предупреждение о блокировке аккаунта",
    "accept": "Принято", "approve": "Одобрено", "personal_data_consent": "Согласие на обработку данных",
    "program_terms": "Условия программы", "xlsx": "Excel", "csv": "CSV",
    "parsed": "Обработано", "catalog": "Каталог", "true": "Да", "false": "Нет",
    "invalid_credentials": "Неверная почта или пароль", "invalid_token": "Недействительная сессия",
    "bloggers": "Блогеры", "social_accounts": "Социальные аккаунты",
    "publications": "Публикации", "view_readings": "Показания просмотров",
    "moderation_history": "История модерации", "accruals": "Начисления",
    "payout_register": "Реестр выплат", "payout_history": "История выплат",
    "support_tickets": "Обращения", "audit_log": "Журнал безопасности",
}
AUDIT_ACTION_LABELS = {
    "auth.user_registered": "Пользователь зарегистрирован",
    "auth.login_succeeded": "Выполнен вход в аккаунт", "auth.login_failed": "Неудачная попытка входа",
    "auth.logout_succeeded": "Выполнен выход из аккаунта",
    "auth.email_verification_requested": "Запрошено подтверждение почты",
    "auth.email_verified": "Электронная почта подтверждена",
    "auth.password_reset_requested": "Запрошено восстановление пароля",
    "auth.password_reset_completed": "Пароль восстановлен",
    "auth.session_refreshed": "Сессия пользователя обновлена",
    "auth.refresh_reuse_detected": "Обнаружено повторное использование сессии",
    "creator.profile_created": "Создан профиль блогера", "creator.profile_updated": "Изменён профиль блогера",
    "creator.profile_submitted": "Профиль отправлен на проверку",
    "moderation.profile_reviewed": "Профиль блогера проверен",
    "creator.social_account_created": "Добавлена социальная площадка",
    "creator.social_account_restored": "Социальная площадка восстановлена",
    "creator.social_account_updated": "Социальная площадка изменена",
    "creator.social_account_deleted": "Социальная площадка удалена",
    "moderation.social_account_reviewed": "Социальная площадка проверена",
    "admin.user_bootstrapped": "Создан первый администратор",
    "admin.user_role_changed": "Изменена роль пользователя",
    "admin.user_blocked": "Пользователь заблокирован", "admin.user_unblocked": "Пользователь разблокирован",
    "admin.program_settings_updated": "Изменены настройки программы",
    "catalog.product_created": "Товар создан", "catalog.product_updated": "Товар изменён",
    "catalog.product_hidden": "Товар скрыт", "catalog.product_restored": "Товар восстановлен",
    "content.video_card_created": "Создана карточка ролика",
    "content.video_card_updated": "Карточка ролика изменена",
    "content.publication_created": "Публикация добавлена",
    "content.publication_updated": "Публикация изменена",
    "content.publication_deleted": "Публикация удалена",
    "content.publication_submitted": "Публикация отправлена на проверку",
    "moderation.publication_reviewed": "Публикация проверена",
    "content.publication_deactivated": "Публикация отключена",
    "content.publication_promo_issued": "Выдан промокод для публикации",
    "readings.created": "Добавлены показания просмотров",
    "readings.updated": "Показания просмотров изменены",
    "moderation.view_reading_reviewed": "Показания просмотров проверены",
    "moderation.view_reading_corrected": "Показания просмотров скорректированы",
    "billing.rate_created": "Создана ставка оплаты",
    "billing.recalculation_requested": "Запрошен перерасчёт периода",
    "billing.period_confirmed": "Расчётный период подтверждён",
    "billing.accrual_corrected": "Начисление скорректировано",
    "payout.details_updated": "Платёжные реквизиты изменены",
    "payout.requested": "Выплата запрошена", "payout.review_started": "Начата проверка выплаты",
    "payout.approved": "Выплата одобрена", "payout.rejected": "Выплата отклонена",
    "payout.paid": "Выплата проведена", "payout.receipt_recorded": "Чек выплаты получен",
    "payout.pii_anonymized": "Платёжные данные обезличены",
    "export.requested": "Запрошена выгрузка данных", "export.downloaded": "Выгрузка данных скачана",
    "support.ticket_created": "Создано обращение",
    "support.message_created": "Добавлено сообщение в обращение",
    "support.ticket_assigned": "Назначен исполнитель обращения",
    "support.status_changed": "Изменён статус обращения",
    "notification.template_updated": "Изменён шаблон уведомления",
    "lifecycle.account_suspended": "Аккаунт приостановлен из-за неактивности",
    "lifecycle.account_blocked": "Аккаунт заблокирован из-за неактивности",
    "lifecycle.recovery_approved": "Восстановление аккаунта одобрено",
    "lifecycle.recovery_rejected": "Восстановление аккаунта отклонено",
    "account.deletion_requested": "Запрошено удаление аккаунта",
    "account.deletion_cancelled": "Удаление аккаунта отменено",
    "account.deletion_completed": "Аккаунт удалён", "account.pii_anonymized": "Личные данные обезличены",
    "legal.document_published": "Опубликован юридический документ",
    "legal.document_accepted": "Юридический документ принят пользователем",
    "legal.personal_data_consent_withdrawn": "Согласие на обработку данных отозвано",
}


def _raw(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _mapped(value: Any, labels: dict[str, str]) -> Any:
    value = _raw(value)
    return labels.get(value, value)


def _localize_json(value: Any) -> Any:
    if isinstance(value, dict):
        return {JSON_KEY_LABELS.get(key, key): _localize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_localize_json(item) for item in value]
    value = _raw(value)
    if not isinstance(value, str):
        return value
    if value in JSON_VALUE_LABELS:
        return JSON_VALUE_LABELS[value]
    for labels in (STATUS_LABELS, RECIPIENT_LABELS, PLATFORM_LABELS, ROLE_LABELS,
                   RISK_LABELS, AVAILABILITY_LABELS, EVENT_LABELS, RESULT_LABELS):
        if value in labels:
            return labels[value]
    return value


def format_export_value(presentation: str, value: Any) -> Any:
    if value is None:
        return None
    labels_by_presentation = {
        "platform": PLATFORM_LABELS, "status": STATUS_LABELS, "recipient": RECIPIENT_LABELS,
        "role": ROLE_LABELS, "source": SOURCE_LABELS, "availability": AVAILABILITY_LABELS,
        "category": CATEGORY_LABELS, "object": OBJECT_LABELS, "event": EVENT_LABELS,
        "result": RESULT_LABELS, "boolean": BOOLEAN_LABELS, "audit_action": AUDIT_ACTION_LABELS,
    }
    if presentation in labels_by_presentation:
        return _mapped(value, labels_by_presentation[presentation])
    if presentation == "risks":
        values = value if isinstance(value, (list, tuple)) else [value]
        return "; ".join(str(_mapped(item, RISK_LABELS)) for item in values) or "Нет"
    if presentation == "json":
        return _localize_json(value)
    return _raw(value)
