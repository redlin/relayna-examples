"""
generate_invoice.py — creates a realistic sample PDF invoice using reportlab.

Run this to generate a test PDF without needing a real invoice:

    python scripts/generate_invoice.py
    python scripts/generate_invoice.py --output my_invoice.pdf
    python scripts/generate_invoice.py --vendor "ACME Ltd" --total 4200.00

Then feed the PDF to the workflow:

    python main.py --invoice invoice.pdf
"""

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path


def generate_invoice(
    output_path: str = "invoice.pdf",
    vendor_name: str = "Acme Consulting Ltd",
    invoice_number: str = "INV-2026-0042",
    bill_to: str = "Relayna Corp\n123 AI Boulevard\nSan Francisco, CA 94107",
    line_items: list[dict] | None = None,
    tax_rate: float = 0.08,
) -> Path:
    """Generate a sample PDF invoice and return the path."""
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            SimpleDocTemplate,
            Table,
            TableStyle,
            Paragraph,
            Spacer,
            HRFlowable,
        )
    except ImportError:
        print("Error: reportlab is required. Install it with: pip install reportlab")
        sys.exit(1)

    if line_items is None:
        line_items = [
            {"description": "AI Integration Consulting (40 hrs)", "quantity": 40, "unit_price": 185.00},
            {"description": "LangGraph Workflow Design", "quantity": 1, "unit_price": 2400.00},
            {"description": "API Documentation & Training", "quantity": 8, "unit_price": 120.00},
            {"description": "Relayna Integration Setup", "quantity": 1, "unit_price": 850.00},
        ]

    # Calculate totals
    for item in line_items:
        item["amount"] = item["quantity"] * item["unit_price"]

    subtotal = sum(i["amount"] for i in line_items)
    tax = subtotal * tax_rate
    total = subtotal + tax

    invoice_date = date.today()
    due_date = invoice_date + timedelta(days=30)

    # ── Document setup ────────────────────────────────────────────────────────
    output = Path(output_path)
    doc = SimpleDocTemplate(
        str(output),
        pagesize=letter,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    story = []

    # ── Header ────────────────────────────────────────────────────────────────
    header_style = ParagraphStyle(
        "Header",
        parent=styles["Heading1"],
        fontSize=24,
        textColor=colors.HexColor("#1a1a2e"),
        spaceAfter=4,
    )
    sub_style = ParagraphStyle(
        "Sub",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#555555"),
    )
    label_style = ParagraphStyle(
        "Label",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#888888"),
        fontName="Helvetica-Bold",
    )
    value_style = ParagraphStyle(
        "Value",
        parent=styles["Normal"],
        fontSize=10,
        textColor=colors.HexColor("#1a1a2e"),
    )

    # Vendor name + INVOICE title side by side
    header_data = [
        [
            Paragraph(vendor_name, header_style),
            Paragraph("INVOICE", ParagraphStyle(
                "InvoiceTitle",
                parent=styles["Heading1"],
                fontSize=28,
                textColor=colors.HexColor("#4f46e5"),
                alignment=2,  # right align
            )),
        ]
    ]
    header_table = Table(header_data, colWidths=[3.5 * inch, 3.5 * inch])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(header_table)
    story.append(Spacer(1, 0.1 * inch))
    story.append(HRFlowable(width="100%", thickness=2, color=colors.HexColor("#4f46e5")))
    story.append(Spacer(1, 0.2 * inch))

    # Vendor address + invoice meta
    meta_data = [
        [
            Paragraph("123 Innovation Drive\nSuite 400\nAustin, TX 78701\ncontact@acmeconsulting.example", sub_style),
            Table(
                [
                    [Paragraph("Invoice #", label_style), Paragraph(invoice_number, value_style)],
                    [Paragraph("Invoice Date", label_style), Paragraph(invoice_date.strftime("%B %d, %Y"), value_style)],
                    [Paragraph("Due Date", label_style), Paragraph(due_date.strftime("%B %d, %Y"), value_style)],
                    [Paragraph("Currency", label_style), Paragraph("USD", value_style)],
                ],
                colWidths=[1.2 * inch, 2.2 * inch],
                style=TableStyle([
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 4),
                ]),
            ),
        ]
    ]
    meta_table = Table(meta_data, colWidths=[3.5 * inch, 3.5 * inch])
    meta_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 0.3 * inch))

    # Bill to
    story.append(Paragraph("BILL TO", label_style))
    story.append(Spacer(1, 0.05 * inch))
    story.append(Paragraph(bill_to.replace("\n", "<br/>"), value_style))
    story.append(Spacer(1, 0.3 * inch))

    # ── Line items table ──────────────────────────────────────────────────────
    table_data = [
        [
            Paragraph("Description", label_style),
            Paragraph("Qty", label_style),
            Paragraph("Unit Price", label_style),
            Paragraph("Amount", label_style),
        ]
    ]
    for item in line_items:
        table_data.append([
            Paragraph(item["description"], value_style),
            Paragraph(str(item["quantity"]), value_style),
            Paragraph(f"${item['unit_price']:,.2f}", value_style),
            Paragraph(f"${item['amount']:,.2f}", value_style),
        ])

    items_table = Table(
        table_data,
        colWidths=[3.5 * inch, 0.75 * inch, 1.2 * inch, 1.55 * inch],
    )
    items_table.setStyle(TableStyle([
        # Header row
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f4f6")),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("LINEBELOW", (0, 0), (-1, 0), 1, colors.HexColor("#d1d5db")),
        # Data rows
        ("TOPPADDING", (0, 1), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 1), (-1, -1), 6),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fafafa")]),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
        # Alignment
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(items_table)
    story.append(Spacer(1, 0.2 * inch))

    # ── Totals ────────────────────────────────────────────────────────────────
    totals_data = [
        ["Subtotal", f"${subtotal:,.2f}"],
        [f"Tax ({tax_rate:.0%})", f"${tax:,.2f}"],
        ["", ""],
        ["TOTAL DUE", f"${total:,.2f}"],
    ]
    totals_table = Table(
        totals_data,
        colWidths=[5.25 * inch, 1.75 * inch],
    )
    totals_table.setStyle(TableStyle([
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEABOVE", (0, 3), (-1, 3), 1.5, colors.HexColor("#4f46e5")),
        ("FONTNAME", (0, 3), (-1, 3), "Helvetica-Bold"),
        ("FONTSIZE", (0, 3), (-1, 3), 12),
        ("TEXTCOLOR", (0, 3), (-1, 3), colors.HexColor("#4f46e5")),
    ]))
    story.append(totals_table)
    story.append(Spacer(1, 0.4 * inch))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.HexColor("#d1d5db")))
    story.append(Spacer(1, 0.15 * inch))

    # ── Footer ────────────────────────────────────────────────────────────────
    footer_style = ParagraphStyle(
        "Footer",
        parent=styles["Normal"],
        fontSize=9,
        textColor=colors.HexColor("#6b7280"),
    )
    story.append(Paragraph(
        "Payment Terms: Net 30. Please make payment to Acme Consulting Ltd. "
        "Bank transfer details will be provided upon request. "
        "For questions, contact billing@acmeconsulting.example.",
        footer_style,
    ))

    # ── Build ─────────────────────────────────────────────────────────────────
    doc.build(story)
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a sample PDF invoice")
    parser.add_argument(
        "--output", "-o",
        default="invoice.pdf",
        help="Output PDF file path (default: invoice.pdf)",
    )
    parser.add_argument(
        "--vendor",
        default="Acme Consulting Ltd",
        help="Vendor/supplier name",
    )
    parser.add_argument(
        "--invoice-number",
        default="INV-2026-0042",
        help="Invoice number",
    )
    args = parser.parse_args()

    output = generate_invoice(
        output_path=args.output,
        vendor_name=args.vendor,
        invoice_number=args.invoice_number,
    )
    print(f"Invoice generated: {output.resolve()}")
    print(f"Run: python main.py --invoice {output}")


if __name__ == "__main__":
    main()
