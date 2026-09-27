DEFAULT_NOTIFICATION_TEMPLATES = {
    "account_suspension_warning": {
        "title": "Скоро аккаунт будет приостановлен",
        "body": "До приостановки аккаунта из-за отсутствия активности осталось $days дн. Дата приостановки: $deadline.",
        "variables": ["days", "deadline"],
    },
    "account_suspended": {
        "title": "Аккаунт приостановлен",
        "body": "Аккаунт приостановлен из-за отсутствия активности. Для возобновления работы отправьте обращение в поддержку.",
        "variables": [],
    },
    "account_block_warning": {
        "title": "Скоро аккаунт будет заблокирован",
        "body": "До блокировки приостановленного аккаунта осталось $days дн. Дата блокировки: $deadline.",
        "variables": ["days", "deadline"],
    },
    "account_fully_blocked": {
        "title": "Аккаунт заблокирован",
        "body": "Аккаунт заблокирован. На балансе остались невыплаченные средства. Обратитесь в поддержку.",
        "variables": ["balance_kopecks"],
    },
    "account_recovery_approved": {
        "title": "Доступ к аккаунту восстановлен",
        "body": "Аккаунт снова активен. Публикации необходимо повторно отправить на проверку.",
        "variables": [],
    },
    "account_recovery_rejected": {
        "title": "Восстановление аккаунта отклонено",
        "body": "Запрос на восстановление отклонён. Причина указана в обращении в поддержку.",
        "variables": [],
    },
    "registration_created": {
        "title": "Регистрация завершена",
        "body": "Подтвердите электронную почту, чтобы активировать аккаунт.",
        "variables": [],
    },
    "email_verified": {
        "title": "Почта подтверждена",
        "body": "Адрес электронной почты успешно подтверждён.",
        "variables": [],
    },
    "profile_status_changed": {
        "title": "Статус профиля изменён",
        "body": "Результат проверки профиля доступен в личном кабинете.",
        "variables": ["status"],
    },
    "publication_submitted": {
        "title": "Новая публикация на проверке",
        "body": "Блогер отправил публикацию на модерацию.",
        "variables": ["publication_id"],
    },
    "publication_status_changed": {
        "title": "Статус публикации изменён",
        "body": "Результат проверки публикации доступен в её карточке.",
        "variables": ["publication_id", "status"],
    },
    "reading_status_changed": {
        "title": "Показание просмотров проверено",
        "body": "Результат проверки показания доступен в разделе просмотров.",
        "variables": ["reading_id", "status"],
    },
    "calculation_confirmed": {
        "title": "Расчёт за период подтверждён",
        "body": "Расчёт за период $period подтверждён. Начисление доступно в разделе выплат.",
        "variables": ["amount_kopecks", "period"],
    },
    "payout_status_changed": {
        "title": "Статус выплаты изменён",
        "body": "По заявке $request_number есть обновление. Откройте заявку, чтобы посмотреть подробности.",
        "variables": ["request_number", "status"],
    },
    "support_ticket_created": {
        "title": "Новое обращение $ticket_number",
        "body": "Создано обращение: $subject.",
        "variables": ["ticket_number", "subject"],
    },
    "support_blogger_message": {
        "title": "Новый ответ в обращении $ticket_number",
        "body": "Блогер ответил в обращении «$subject».",
        "variables": ["ticket_number", "subject"],
    },
    "support_staff_message": {
        "title": "Ответ поддержки в обращении $ticket_number",
        "body": "В обращении «$subject» появился новый ответ поддержки.",
        "variables": ["status", "subject", "ticket_number"],
    },
    "support_ticket_assigned": {
        "title": "Вам назначено обращение $ticket_number",
        "body": "Вы назначены ответственным за обращение «$subject».",
        "variables": ["ticket_number", "subject"],
    },
    "support_status_changed": {
        "title": "Статус обращения $ticket_number изменён",
        "body": "В обращении «$subject» изменился статус. Откройте обращение, чтобы посмотреть подробности.",
        "variables": ["status", "subject", "ticket_number"],
    },
}
