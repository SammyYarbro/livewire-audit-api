"""
LiveWire Intelligence Solutions - AI Readiness Audit Report Generator
=====================================================================

Takes a JSON of audit findings and produces a fully-branded PDF report
ready to email to the client.

USAGE
-----
    python livewire_audit_generator.py findings.json
    python livewire_audit_generator.py findings.json --output ./reports/

PROGRAMMATIC USE
----------------
    from livewire_audit_generator import generate_report
    output_path = generate_report(findings_dict, output_dir="./reports")
"""

import json
import os
import sys
import argparse
import re
from datetime import datetime
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, PageBreak, Table, TableStyle, Flowable
)

# =============================================================================
# BRAND PALETTE
# =============================================================================
BRAND_BLACK = colors.HexColor('#0a0a0a')
BRAND_RED = colors.HexColor('#c8102e')
BRAND_RED_DARK = colors.HexColor('#8a0a1f')
BRAND_GRAY = colors.HexColor('#3a3a3a')
BRAND_LIGHT_GRAY = colors.HexColor('#f2f2f2')
BRAND_MID_GRAY = colors.HexColor('#888888')
BRAND_WHITE = colors.HexColor('#ffffff')
BRAND_GREEN = colors.HexColor('#1f7a3a')
BRAND_AMBER = colors.HexColor('#d97706')


# =============================================================================
# UTILITY: severity / score → color
# =============================================================================
def score_color(score):
    """Return color based on numeric score (0–100)."""
    try:
        s = int(score)
    except (ValueError, TypeError):
        return BRAND_GRAY
    if s >= 80:
        return BRAND_GREEN
    if s >= 60:
        return BRAND_AMBER
    return BRAND_RED


def severity_color(value):
    """Return color based on severity/status label."""
    if not value:
        return BRAND_GRAY
    v = str(value).lower().strip()
    if v in ('high', 'fail', 'missing', 'broken', 'critical', 'no'):
        return BRAND_RED
    if v in ('med', 'medium', 'warning', 'partial'):
        return BRAND_AMBER
    if v in ('low', 'pass', 'present', 'ok', 'yes', 'good'):
        return BRAND_GREEN
    return BRAND_GRAY


def slugify(text):
    """Convert a string into a safe filename component."""
    text = re.sub(r'[^a-zA-Z0-9]+', '_', str(text)).strip('_')
    return text or 'client'


# =============================================================================
# CUSTOM FLOWABLES
# =============================================================================
class ScoreBadge(Flowable):
    """Big score badge — colored by score value."""
    def __init__(self, score, label, width=1.4*inch, height=1.4*inch):
        Flowable.__init__(self)
        self.score = score
        self.label = label
        self.color = score_color(score)
        self.width = width
        self.height = height

    def draw(self):
        c = self.canv
        c.setFillColor(self.color)
        c.roundRect(0, 0, self.width, self.height, 8, fill=1, stroke=0)
        c.setFillColor(BRAND_WHITE)
        c.setFont("Helvetica-Bold", 36)
        c.drawCentredString(self.width/2, self.height/2 - 4, str(self.score))
        c.setFont("Helvetica-Bold", 9)
        c.drawCentredString(self.width/2, 14, str(self.label).upper())


class BrandedHeader(Flowable):
    """Red-block section divider."""
    def __init__(self, text, width=7*inch, height=0.5*inch):
        Flowable.__init__(self)
        self.text = text
        self.width = width
        self.height = height

    def draw(self):
        c = self.canv
        c.setFillColor(BRAND_RED)
        c.rect(0, 0, 0.15*inch, self.height, fill=1, stroke=0)
        c.setFillColor(BRAND_BLACK)
        c.setFont("Helvetica-Bold", 16)
        c.drawString(0.3*inch, self.height/2 - 5, self.text)


# =============================================================================
# PAGE CHROME
# =============================================================================
def _draw_cover(c, client):
    width, height = letter
    c.saveState()

    c.setFillColor(BRAND_BLACK)
    c.rect(0, 0, width, height, fill=1, stroke=0)

    c.setFillColor(BRAND_RED)
    c.rect(0, height - 0.75*inch, width, 0.15*inch, fill=1, stroke=0)

    # LW badge
    badge_x, badge_y = 0.75*inch, height - 2.2*inch
    c.setFillColor(BRAND_RED)
    c.roundRect(badge_x, badge_y, 1.1*inch, 1.1*inch, 6, fill=1, stroke=0)
    c.setFillColor(BRAND_WHITE)
    c.setFont("Helvetica-Bold", 42)
    c.drawCentredString(badge_x + 0.55*inch, badge_y + 0.32*inch, "LW")

    c.setFillColor(BRAND_WHITE)
    c.setFont("Helvetica-Bold", 22)
    c.drawString(2.1*inch, height - 1.6*inch, "LIVEWIRE")
    c.setFillColor(BRAND_RED)
    c.drawString(2.1*inch, height - 1.9*inch, "INTELLIGENCE SOLUTIONS")

    c.setFillColor(BRAND_WHITE)
    c.setFont("Helvetica-Bold", 48)
    c.drawCentredString(width/2, height - 4.2*inch, "AI READINESS")
    c.setFillColor(BRAND_RED)
    c.drawCentredString(width/2, height - 4.9*inch, "AUDIT REPORT")

    c.setStrokeColor(BRAND_RED)
    c.setLineWidth(2)
    c.line(2.5*inch, height - 5.4*inch, width - 2.5*inch, height - 5.4*inch)

    c.setFillColor(BRAND_MID_GRAY)
    c.setFont("Helvetica-Bold", 10)
    c.drawCentredString(width/2, height - 5.9*inch, "PREPARED FOR")

    c.setFillColor(BRAND_WHITE)
    c.setFont("Helvetica-Bold", 20)
    c.drawCentredString(width/2, height - 6.3*inch,
                        client.get('company_name', '[CLIENT COMPANY NAME]'))

    c.setFillColor(BRAND_MID_GRAY)
    c.setFont("Helvetica", 11)
    c.drawCentredString(width/2, height - 6.6*inch,
                        client.get('website', '[website]'))

    c.setFont("Helvetica", 10)
    date_str = client.get('report_date', datetime.now().strftime('%m/%d/%Y'))
    report_num = client.get('report_number', f"LWIS-{datetime.now().strftime('%Y%m%d')}")
    c.drawCentredString(width/2, height - 7.4*inch,
                        f"REPORT DATE: {date_str}   |   REPORT #: {report_num}")

    c.setFillColor(BRAND_RED)
    c.rect(0, 0.6*inch, width, 0.05*inch, fill=1, stroke=0)
    c.setFillColor(BRAND_WHITE)
    c.setFont("Helvetica-Bold", 9)
    c.drawCentredString(width/2, 0.35*inch,
                        "MCSE CREDENTIALED   |   MONTANA-BASED   |   LIVEWIREINTEL.COM")
    c.restoreState()


def _draw_chrome(c, doc):
    if doc.page == 1:
        return
    width, height = letter
    c.saveState()

    c.setFillColor(BRAND_RED)
    c.rect(0, height - 0.25*inch, width, 0.08*inch, fill=1, stroke=0)

    c.setFillColor(BRAND_BLACK)
    c.setFont("Helvetica-Bold", 9)
    c.drawString(0.75*inch, height - 0.5*inch, "LIVEWIRE INTELLIGENCE SOLUTIONS")
    c.setFillColor(BRAND_MID_GRAY)
    c.setFont("Helvetica", 9)
    c.drawRightString(width - 0.75*inch, height - 0.5*inch, "AI Readiness Audit")

    c.setStrokeColor(BRAND_LIGHT_GRAY)
    c.setLineWidth(0.5)
    c.line(0.75*inch, 0.6*inch, width - 0.75*inch, 0.6*inch)

    c.setFillColor(BRAND_MID_GRAY)
    c.setFont("Helvetica", 8)
    c.drawString(0.75*inch, 0.4*inch, "livewireintel.com  |  systems@livewireintel.com")
    c.drawRightString(width - 0.75*inch, 0.4*inch, f"Page {doc.page}")
    c.setFont("Helvetica-Oblique", 8)
    c.drawCentredString(width/2, 0.25*inch, "CONFIDENTIAL — Prepared for client review")
    c.restoreState()


# =============================================================================
# TABLE BUILDERS
# =============================================================================
def _styled_table(data, col_widths, header_color=BRAND_BLACK):
    t = Table(data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), header_color),
        ('TEXTCOLOR', (0,0), (-1,0), BRAND_WHITE),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9.5),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('GRID', (0,0), (-1,-1), 0.5, BRAND_LIGHT_GRAY),
        ('ROWBACKGROUNDS', (0,1), (-1,-1), [BRAND_WHITE, BRAND_LIGHT_GRAY]),
        ('LEFTPADDING', (0,0), (-1,-1), 8),
        ('RIGHTPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 7),
        ('BOTTOMPADDING', (0,0), (-1,-1), 7),
        ('TEXTCOLOR', (0,1), (-1,-1), BRAND_GRAY),
    ]))
    return t


def _color_cell(value):
    """Return colored bold paragraph for severity/status cells."""
    color = severity_color(value)
    return Paragraph(
        f'<font color="{color.hexval()}"><b>{value}</b></font>',
        ParagraphStyle('cell', fontName='Helvetica', fontSize=9.5, leading=12)
    )


# =============================================================================
# MAIN REPORT BUILDER
# =============================================================================
def generate_report(findings, output_dir="."):
    """Generate a branded PDF report from a findings dict.

    Returns absolute path to the generated PDF.
    """
    client = findings.get('client', {})
    company_slug = slugify(client.get('company_name', 'client'))
    date_slug = datetime.now().strftime('%Y%m%d')
    filename = f"LiveWire_Audit_{company_slug}_{date_slug}.pdf"
    output_path = os.path.join(output_dir, filename)
    os.makedirs(output_dir, exist_ok=True)

    doc = SimpleDocTemplate(
        output_path, pagesize=letter,
        leftMargin=0.75*inch, rightMargin=0.75*inch,
        topMargin=0.85*inch, bottomMargin=0.75*inch,
        title=f"AI Readiness Audit — {client.get('company_name', 'Client')}",
        author="LiveWire Intelligence Solutions",
    )

    styles = getSampleStyleSheet()
    body = ParagraphStyle('Body', parent=styles['Normal'],
        fontName='Helvetica', fontSize=10.5, leading=15,
        textColor=BRAND_GRAY, spaceAfter=8)
    h2 = ParagraphStyle('H2', parent=styles['Heading2'],
        fontName='Helvetica-Bold', fontSize=14, leading=18,
        textColor=BRAND_RED, spaceBefore=14, spaceAfter=6)
    h3 = ParagraphStyle('H3', parent=styles['Heading3'],
        fontName='Helvetica-Bold', fontSize=11, leading=14,
        textColor=BRAND_BLACK, spaceBefore=8, spaceAfter=3)
    bullet = ParagraphStyle('Bullet', parent=body,
        leftIndent=20, bulletIndent=8, spaceAfter=4)

    story = []
    story.append(PageBreak())  # forces cover page

    # ===== EXECUTIVE SUMMARY =====
    story.append(BrandedHeader("EXECUTIVE SUMMARY"))
    story.append(Spacer(1, 0.2*inch))

    company = client.get('company_name', 'this business')
    website = client.get('website', 'the website')
    story.append(Paragraph(
        f"This audit evaluates <b>{company}</b>'s current visibility across two distinct "
        "discovery channels: traditional search engines (Google, Bing) and AI assistants "
        "(ChatGPT, Claude, Gemini, Perplexity). As consumer behavior shifts toward "
        "conversational AI for recommendations and decision-making, businesses without "
        "AI-readable structured content are becoming invisible in the answers their "
        "customers receive.",
        body
    ))
    story.append(Paragraph(
        f"The findings, scoring, and recommendations below are based on a live technical "
        f"scan of <b>{website}</b> performed on "
        f"<b>{client.get('report_date', datetime.now().strftime('%B %d, %Y'))}</b>.",
        body
    ))

    # Score row
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph("<b>OVERALL READINESS SCORES</b>", h3))
    story.append(Spacer(1, 0.1*inch))

    scores = findings.get('scores', {})
    score_row = Table([[
        ScoreBadge(scores.get('ai_search', '—'), "AI Search"),
        ScoreBadge(scores.get('seo', '—'), "SEO"),
        ScoreBadge(scores.get('schema', '—'), "Schema"),
        ScoreBadge(scores.get('speed', '—'), "Speed"),
    ]], colWidths=[1.6*inch]*4)
    score_row.setStyle(TableStyle([
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
    ]))
    story.append(score_row)
    story.append(Spacer(1, 0.05*inch))
    story.append(Paragraph(
        "<i>Scores are out of 100. Anything below 60 indicates urgent remediation needed.</i>",
        ParagraphStyle('cap', parent=body, fontSize=9, alignment=TA_CENTER, textColor=BRAND_MID_GRAY)
    ))

    # Headline finding
    story.append(Spacer(1, 0.25*inch))
    headline = findings.get('headline_finding',
        'No headline finding provided. Please update the findings JSON.')
    callout = Table([[
        Paragraph(
            f"<b>HEADLINE FINDING</b><br/><br/>{headline}",
            ParagraphStyle('cb', parent=body, textColor=BRAND_WHITE, leading=15)
        )
    ]], colWidths=[6.5*inch])
    callout.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), BRAND_BLACK),
        ('LEFTPADDING', (0,0), (-1,-1), 16),
        ('RIGHTPADDING', (0,0), (-1,-1), 16),
        ('TOPPADDING', (0,0), (-1,-1), 14),
        ('BOTTOMPADDING', (0,0), (-1,-1), 14),
        ('LINEBEFORE', (0,0), (0,-1), 4, BRAND_RED),
    ]))
    story.append(callout)
    story.append(PageBreak())

    # ===== SECTION 1: AI VISIBILITY =====
    story.append(BrandedHeader("01  |  AI SEARCH VISIBILITY"))
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph(
        "How does your business appear when potential customers ask AI assistants for "
        "recommendations in your category? We tested live queries across the four major "
        "AI platforms.",
        body
    ))
    story.append(Paragraph("Live Query Test Results", h2))

    ai_rows = [["AI Platform", "Test Query", "Mentioned?", "Notes"]]
    for row in findings.get('ai_visibility', []):
        mentioned = "Yes" if row.get('mentioned') else "No"
        ai_rows.append([
            row.get('platform', ''),
            row.get('query', ''),
            _color_cell(mentioned),
            row.get('notes', ''),
        ])
    if len(ai_rows) == 1:
        ai_rows.append(["—", "No data provided", "—", "—"])
    story.append(_styled_table(ai_rows, [1.2*inch, 2.3*inch, 1.0*inch, 2.2*inch]))

    story.append(Paragraph("Why AI Search Matters For Your Business", h2))
    story.append(Paragraph(
        "Unlike Google, which shows ten results, AI assistants typically name "
        "<b>one to three businesses</b> in their answer. If you're not in that short list, "
        "you don't exist to that customer. AI assistants build their answers from "
        "structured data, citations, and authoritative third-party signals — not from "
        "how pretty your homepage looks.",
        body
    ))
    story.append(PageBreak())

    # ===== SECTION 2: SCHEMA =====
    story.append(BrandedHeader("02  |  SCHEMA & STRUCTURED DATA"))
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph(
        "Schema markup is the machine-readable layer that tells search engines and AI "
        "assistants <i>what your business actually is</i>. Without it, your site is "
        "essentially mute to AI.",
        body
    ))
    story.append(Paragraph("Current Schema Coverage", h2))

    schema_rows = [["Schema Type", "Present?", "Status", "Impact"]]
    for row in findings.get('schema_coverage', []):
        present = "Yes" if row.get('present') else "No"
        schema_rows.append([
            row.get('type', ''),
            _color_cell(present),
            _color_cell(row.get('status', '')),
            _color_cell(row.get('impact', '')),
        ])
    if len(schema_rows) == 1:
        schema_rows.append(["—", "—", "No data", "—"])
    story.append(_styled_table(
        schema_rows, [2.2*inch, 0.9*inch, 1.9*inch, 1.5*inch],
        header_color=BRAND_RED
    ))

    notes = findings.get('schema_notes', '')
    if notes:
        story.append(Spacer(1, 0.15*inch))
        story.append(Paragraph("Auditor Notes", h3))
        story.append(Paragraph(notes, body))
    story.append(PageBreak())

    # ===== SECTION 3: TRADITIONAL SEO =====
    story.append(BrandedHeader("03  |  TRADITIONAL SEO HEALTH"))
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph(
        "Even with AI search rising, Google still drives the majority of discovery for "
        "local businesses. These are the foundation issues affecting how Google sees, "
        "indexes, and ranks your site.",
        body
    ))
    story.append(Paragraph("On-Page Fundamentals", h2))

    seo_rows = [["Element", "Finding", "Severity"]]
    for row in findings.get('seo_findings', []):
        seo_rows.append([
            row.get('element', ''),
            row.get('finding', ''),
            _color_cell(row.get('severity', '')),
        ])
    if len(seo_rows) == 1:
        seo_rows.append(["—", "No data provided", "—"])
    story.append(_styled_table(seo_rows, [2.0*inch, 3.5*inch, 1.2*inch]))
    story.append(PageBreak())

    # ===== SECTION 4: PERFORMANCE =====
    story.append(BrandedHeader("04  |  TECHNICAL PERFORMANCE"))
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph(
        "Both Google and AI systems penalize slow, broken, or insecure sites. Performance "
        "isn't cosmetic — it's a ranking factor and a trust signal.",
        body
    ))

    perf_rows = [["Metric", "Your Score", "Target", "Status"]]
    for row in findings.get('performance', []):
        perf_rows.append([
            row.get('metric', ''),
            str(row.get('score', '')),
            row.get('target', ''),
            _color_cell(row.get('status', '')),
        ])
    if len(perf_rows) == 1:
        perf_rows.append(["—", "—", "—", "No data"])
    story.append(_styled_table(
        perf_rows, [2.6*inch, 1.3*inch, 1.3*inch, 1.5*inch],
        header_color=BRAND_RED
    ))
    story.append(PageBreak())

    # ===== SECTION 5: ACTION PLAN =====
    story.append(BrandedHeader("05  |  PRIORITY ACTION PLAN"))
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph(
        f"Below is the prioritized roadmap to bring <b>{company}</b> to full AI and search "
        "readiness. Items are sequenced by impact and effort.",
        body
    ))

    plan = findings.get('action_plan', {})
    tier_blocks = [
        ('CRITICAL — Fix in the first 30 days', BRAND_RED, plan.get('critical', [])),
        ('HIGH PRIORITY — Fix in the first 60 days', BRAND_BLACK, plan.get('high', [])),
        ('ONGOING — Quarterly maintenance', BRAND_GRAY, plan.get('ongoing', [])),
    ]
    for label, color, items in tier_blocks:
        story.append(Spacer(1, 0.1*inch))
        label_row = Table([[Paragraph(
            f"<b>{label}</b>",
            ParagraphStyle('lbl', parent=body, textColor=BRAND_WHITE,
                           fontName='Helvetica-Bold', fontSize=11)
        )]], colWidths=[6.5*inch])
        label_row.setStyle(TableStyle([
            ('BACKGROUND', (0,0), (-1,-1), color),
            ('LEFTPADDING', (0,0), (-1,-1), 12),
            ('TOPPADDING', (0,0), (-1,-1), 6),
            ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ]))
        story.append(label_row)
        story.append(Spacer(1, 0.08*inch))
        if items:
            for item in items:
                story.append(Paragraph(f"• {item}", bullet))
        else:
            story.append(Paragraph("• <i>No items in this tier.</i>", bullet))
    story.append(PageBreak())

    # ===== SECTION 6: HOW WE FIX THIS =====
    story.append(BrandedHeader("06  |  HOW WE FIX THIS"))
    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph(
        "LiveWire Intelligence Solutions offers three service tiers designed to bring "
        "your business to full AI and search readiness.",
        body
    ))

    tier_data = [
        [
            Paragraph("<b>STARTER</b><br/><font size=8 color='#888888'>One-time</font>",
                ParagraphStyle('t', parent=body, textColor=BRAND_WHITE, alignment=TA_CENTER, fontSize=14)),
            Paragraph("<b>GROWTH</b><br/><font size=8 color='#888888'>Most popular</font>",
                ParagraphStyle('t', parent=body, textColor=BRAND_WHITE, alignment=TA_CENTER, fontSize=14)),
            Paragraph("<b>ENTERPRISE</b><br/><font size=8 color='#888888'>Full management</font>",
                ParagraphStyle('t', parent=body, textColor=BRAND_WHITE, alignment=TA_CENTER, fontSize=14)),
        ],
        [
            Paragraph(
                "Complete schema markup<br/>On-page SEO fixes<br/>Google Business optimization"
                "<br/>AI-readiness baseline<br/>30-day support",
                ParagraphStyle('tb', parent=body, alignment=TA_CENTER, fontSize=9.5, leading=16)
            ),
            Paragraph(
                "Everything in Starter<br/>Content optimization<br/>Quarterly AI audits"
                "<br/>Technical performance tuning<br/>Monthly reporting<br/>90-day support",
                ParagraphStyle('tb', parent=body, alignment=TA_CENTER, fontSize=9.5, leading=16)
            ),
            Paragraph(
                "Everything in Growth<br/>Custom AI integrations<br/>Ongoing content strategy"
                "<br/>Competitor monitoring<br/>Priority support<br/>Dedicated POC",
                ParagraphStyle('tb', parent=body, alignment=TA_CENTER, fontSize=9.5, leading=16)
            ),
        ],
    ]
    tier_table = Table(tier_data, colWidths=[2.17*inch]*3, rowHeights=[0.7*inch, 2.0*inch])
    tier_table.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (0,0), BRAND_GRAY),
        ('BACKGROUND', (1,0), (1,0), BRAND_RED),
        ('BACKGROUND', (2,0), (2,0), BRAND_BLACK),
        ('BACKGROUND', (0,1), (-1,1), BRAND_LIGHT_GRAY),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE'),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('LEFTPADDING', (0,0), (-1,-1), 10),
        ('RIGHTPADDING', (0,0), (-1,-1), 10),
        ('TOPPADDING', (0,0), (-1,-1), 12),
        ('BOTTOMPADDING', (0,0), (-1,-1), 12),
        ('LINEAFTER', (0,0), (-2,-1), 1, BRAND_WHITE),
    ]))
    story.append(tier_table)

    story.append(Spacer(1, 0.3*inch))
    story.append(Paragraph("Your Recommended Next Step", h2))
    rec_tier = findings.get('recommended_tier', 'Growth')
    justification = findings.get('tier_justification',
        f"Based on the findings above, the {rec_tier} tier matches your current needs.")
    story.append(Paragraph(
        f"Based on the findings in this audit, our recommendation for <b>{company}</b> is "
        f"the <b>{rec_tier.upper()}</b> tier. {justification}",
        body
    ))

    # CTA / contact block
    story.append(Spacer(1, 0.3*inch))
    cta = Table([[
        Paragraph(
            "<b>BOOK YOUR FREE 15-MINUTE STRATEGY CALL</b><br/><br/>"
            "Your action plan above shows <i>what</i> to fix. The strategy call shows "
            "<i>how</i> — including the specific implementation order, your competitor "
            "gap, and an exact quote.<br/><br/>"
            "<font size=11>Sammy — Founder, LiveWire Intelligence Solutions</font><br/>"
            "<b>Email:</b> systems@livewireintel.com<br/>"
            "<b>Web:</b> livewireintel.com<br/>"
            "<b>Based in:</b> Billings, Montana — serving the 406 and beyond",
            ParagraphStyle('cb', parent=body, textColor=BRAND_WHITE, leading=18, fontSize=10.5)
        )
    ]], colWidths=[6.5*inch])
    cta.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,-1), BRAND_BLACK),
        ('LEFTPADDING', (0,0), (-1,-1), 18),
        ('RIGHTPADDING', (0,0), (-1,-1), 18),
        ('TOPPADDING', (0,0), (-1,-1), 18),
        ('BOTTOMPADDING', (0,0), (-1,-1), 18),
        ('LINEBEFORE', (0,0), (0,-1), 6, BRAND_RED),
    ]))
    story.append(cta)

    story.append(Spacer(1, 0.2*inch))
    story.append(Paragraph(
        "<i>This audit report is the confidential property of LiveWire Intelligence "
        "Solutions and the named recipient. Findings reflect a snapshot of current site "
        "performance at the time of the audit.</i>",
        ParagraphStyle('disc', parent=body, fontSize=8, textColor=BRAND_MID_GRAY, leading=11)
    ))

    # Build
    def first_page(c, d):
        _draw_cover(c, client)
    def later_pages(c, d):
        _draw_chrome(c, d)

    doc.build(story, onFirstPage=first_page, onLaterPages=later_pages)
    return os.path.abspath(output_path)


# =============================================================================
# CLI
# =============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Generate a branded AI Readiness Audit PDF from a findings JSON file."
    )
    parser.add_argument("findings_file", help="Path to findings JSON file")
    parser.add_argument("--output", "-o", default="./reports",
                        help="Output directory (default: ./reports)")
    args = parser.parse_args()

    with open(args.findings_file, 'r') as f:
        findings = json.load(f)

    path = generate_report(findings, output_dir=args.output)
    print(f"✓ Report generated: {path}")


if __name__ == "__main__":
    main()
