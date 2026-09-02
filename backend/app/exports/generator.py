import csv
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from app.exports.models import ExportFormat, ExportType
from app.payouts.policy import MOSCOW


@dataclass(frozen=True)
class ColumnSpec:
    key: str
    title: str
    width: int
    kind: Literal["text", "money", "date"] = "text"


REGISTER_V1 = (
    ColumnSpec("request_number", "Номер заявки", 32),
    ColumnSpec("recipient_name", "Получатель", 32),
    ColumnSpec("recipient_type", "Тип получателя", 20),
    ColumnSpec("sbp_phone", "Телефон СБП", 18),
    ColumnSpec("bank_name", "Банк", 24),
    ColumnSpec("amount_rubles", "Сумма, руб.", 16, "money"),
    ColumnSpec("requested_on", "Дата заявки", 14, "date"),
    ColumnSpec("approved_on", "Дата одобрения", 16, "date"),
    ColumnSpec("payment_due_date", "Срок оплаты", 14, "date"),
    ColumnSpec("self_employment_verified", "Самозанятость проверена", 24),
    ColumnSpec("manager_comment", "Комментарий", 40),
    ColumnSpec("status", "Статус", 18),
)

HISTORY_V1 = REGISTER_V1 + (
    ColumnSpec("paid_on", "Дата оплаты", 14, "date"),
    ColumnSpec("payment_reference", "Номер операции", 28),
    ColumnSpec("rejected_on", "Дата отказа", 14, "date"),
    ColumnSpec("rejection_reason", "Причина отказа", 40),
    ColumnSpec("receipt_due_date", "Срок чека", 14, "date"),
    ColumnSpec("receipt_received_on", "Дата получения чека", 20, "date"),
    ColumnSpec("approved_by", "Одобрил", 30),
    ColumnSpec("paid_by", "Оплатил", 30),
    ColumnSpec("rejected_by", "Отклонил", 30),
)

_SCHEMAS = {
    (ExportType.PAYOUT_REGISTER, 1): ("Реестр выплат", REGISTER_V1),
    (ExportType.PAYOUT_HISTORY, 1): ("История выплат", HISTORY_V1),
}
_FORMULA_PREFIXES = ("=", "+", "-", "@")
_ILLEGAL_EXCEL_CHARACTERS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    value = getattr(value, "value", value)
    text = _ILLEGAL_EXCEL_CHARACTERS.sub("", str(value))
    return "'" + text if text.lstrip().startswith(_FORMULA_PREFIXES) else text


def moscow_date(value: datetime | date | None) -> date | None:
    if value is None or isinstance(value, date) and not isinstance(value, datetime):
        return value
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MOSCOW).date()


def _cell_value(row: dict[str, Any], column: ColumnSpec) -> Any:
    value = row.get(column.key)
    if column.kind == "text":
        return _safe_text(value)
    if column.kind == "date":
        return moscow_date(value)
    if value is None:
        return None
    return (Decimal(value) / Decimal(100)).quantize(Decimal("0.01"))


def schema_for(export_type: ExportType, schema_version: int):
    try:
        return _SCHEMAS[(export_type, schema_version)]
    except KeyError as error:
        raise ValueError("Unsupported export schema version") from error


def generate_export(
    *,
    export_type: ExportType,
    export_format: ExportFormat,
    schema_version: int,
    rows: list[dict[str, Any]],
    destination: Path,
) -> None:
    sheet_name, columns = schema_for(export_type, schema_version)
    if export_format == ExportFormat.CSV:
        _generate_csv(destination, columns, rows)
    else:
        _generate_xlsx(destination, sheet_name, columns, rows)


def _generate_csv(
    destination: Path,
    columns: tuple[ColumnSpec, ...],
    rows: list[dict[str, Any]],
) -> None:
    with destination.open("w", encoding="utf-8-sig", newline="") as output:
        writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
        writer.writerow(column.title for column in columns)
        for row in rows:
            writer.writerow(
                value.strftime("%d.%m.%Y")
                if isinstance(value := _cell_value(row, column), date)
                else format(value, ".2f").replace(".", ",")
                if isinstance(value, Decimal)
                else value
                for column in columns
            )


def _generate_xlsx(
    destination: Path,
    sheet_name: str,
    columns: tuple[ColumnSpec, ...],
    rows: list[dict[str, Any]],
) -> None:
    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    workbook = Workbook(write_only=True)
    worksheet = workbook.create_sheet(sheet_name)
    worksheet.freeze_panes = "A2"
    for index, column in enumerate(columns, start=1):
        worksheet.column_dimensions[get_column_letter(index)].width = column.width
    header = []
    for column in columns:
        cell = WriteOnlyCell(worksheet, value=column.title)
        cell.font = Font(bold=True)
        header.append(cell)
    worksheet.append(header)
    for source_row in rows:
        output_row = []
        for column in columns:
            value = _cell_value(source_row, column)
            cell = WriteOnlyCell(worksheet, value=value)
            if column.kind == "money":
                cell.number_format = "0.00"
            elif column.kind == "date":
                cell.number_format = "dd.mm.yyyy"
            output_row.append(cell)
        worksheet.append(output_row)
    worksheet.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{len(rows) + 1}"
    workbook.save(destination)
