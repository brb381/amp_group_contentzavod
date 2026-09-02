DEFAULT_NOTIFICATION_TEMPLATES = {
    "account_suspension_warning": {
        "title": "Account inactivity warning",
        "body": "Your account will be suspended in $days days on $deadline",
        "variables": ["days", "deadline"],
    },
    "account_suspended": {
        "title": "Account suspended",
        "body": "Your account was suspended for inactivity. Submit a recovery ticket to continue",
        "variables": [],
    },
    "account_block_warning": {
        "title": "Account blocking warning",
        "body": "Your suspended account will be blocked in $days days on $deadline",
        "variables": ["days", "deadline"],
    },
    "account_fully_blocked": {
        "title": "Account blocked",
        "body": "Your account was blocked. Unclaimed balance: $balance_kopecks kopecks",
        "variables": ["balance_kopecks"],
    },
    "account_recovery_approved": {
        "title": "Account recovery approved",
        "body": "Your account is active again. Publications require a new moderation review",
        "variables": [],
    },
    "account_recovery_rejected": {
        "title": "Account recovery rejected",
        "body": "Your recovery request was rejected. Review the reason in the support ticket",
        "variables": [],
    },
    "registration_created": {
        "title": "Registration created",
        "body": "Confirm your email address to activate the account",
        "variables": [],
    },
    "email_verified": {
        "title": "Email confirmed",
        "body": "Your email address has been confirmed",
        "variables": [],
    },
    "profile_status_changed": {
        "title": "Profile status changed",
        "body": "Your profile status is now $status",
        "variables": ["status"],
    },
    "publication_submitted": {
        "title": "Publication submitted",
        "body": "Publication $publication_id is ready for review",
        "variables": ["publication_id"],
    },
    "publication_status_changed": {
        "title": "Publication status changed",
        "body": "Publication $publication_id is now $status",
        "variables": ["publication_id", "status"],
    },
    "reading_status_changed": {
        "title": "View reading reviewed",
        "body": "Reading $reading_id is now $status",
        "variables": ["reading_id", "status"],
    },
    "calculation_confirmed": {
        "title": "Monthly calculation confirmed",
        "body": "Calculation for $period was confirmed: $amount_kopecks kopecks",
        "variables": ["amount_kopecks", "period"],
    },
    "payout_status_changed": {
        "title": "Payout status changed",
        "body": "Payout $request_number is now $status",
        "variables": ["request_number", "status"],
    },
    "support_ticket_created": {
        "title": "New support ticket $ticket_number",
        "body": "A support ticket was created: $subject",
        "variables": ["ticket_number", "subject"],
    },
    "support_blogger_message": {
        "title": "New reply in $ticket_number",
        "body": "The blogger replied to: $subject",
        "variables": ["ticket_number", "subject"],
    },
    "support_staff_message": {
        "title": "Support replied in $ticket_number",
        "body": "There is a new support reply for: $subject. Current status: $status",
        "variables": ["status", "subject", "ticket_number"],
    },
    "support_ticket_assigned": {
        "title": "Ticket $ticket_number assigned",
        "body": "You are responsible for: $subject",
        "variables": ["ticket_number", "subject"],
    },
    "support_status_changed": {
        "title": "Ticket $ticket_number status changed",
        "body": "The status of $subject is now $status",
        "variables": ["status", "subject", "ticket_number"],
    },
}
