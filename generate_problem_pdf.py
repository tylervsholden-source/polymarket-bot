from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.graphics.shapes import Drawing, Rect, Line, String, Group
import os

OUTPUT = "soru_19.pdf"

doc = SimpleDocTemplate(
    OUTPUT,
    pagesize=A4,
    rightMargin=2.5*cm,
    leftMargin=2.5*cm,
    topMargin=3*cm,
    bottomMargin=3*cm,
)

# -- Styles --
styles = getSampleStyleSheet()

normal = ParagraphStyle(
    "normal",
    fontName="Helvetica",
    fontSize=12,
    leading=18,
    alignment=TA_JUSTIFY,
)

bold_q = ParagraphStyle(
    "bold_q",
    fontName="Helvetica-Bold",
    fontSize=12,
    leading=18,
    alignment=TA_JUSTIFY,
)

question_num = ParagraphStyle(
    "question_num",
    fontName="Helvetica-Bold",
    fontSize=13,
    leading=20,
    spaceBefore=6,
)

choice_style = ParagraphStyle(
    "choice",
    fontName="Helvetica",
    fontSize=12,
    leading=20,
)

# -- Bookshelf Figure (simple drawing) --
def bookshelf_figure():
    d = Drawing(400, 160)

    # Outer frame
    d.add(Rect(20, 10, 360, 140, strokeColor=colors.black, fillColor=colors.white, strokeWidth=2))

    # Middle shelf board (4 cm thick, midway)
    d.add(Rect(20, 73, 360, 8, strokeColor=colors.black, fillColor=colors.darkgrey, strokeWidth=1))

    # === Top shelf: books lying horizontal ===
    book_w = 40
    book_h = 22
    top_y = 83
    for i in range(7):
        x = 28 + i * (book_w + 3)
        if x + book_w > 372:
            break
        d.add(Rect(x, top_y, book_w, book_h,
                   strokeColor=colors.black,
                   fillColor=colors.Color(0.6, 0.15, 0.15),
                   strokeWidth=1))

    # === Bottom shelf: books standing vertical ===
    bv_w = 18
    bv_h = 55
    bot_y = 14
    for i in range(14):
        x = 28 + i * (bv_w + 3)
        if x + bv_w > 372:
            break
        d.add(Rect(x, bot_y, bv_w, bv_h,
                   strokeColor=colors.black,
                   fillColor=colors.Color(0.6, 0.15, 0.15),
                   strokeWidth=1))

    # Dimension labels
    d.add(String(185, 0, "144 cm", fontName="Helvetica", fontSize=10, fillColor=colors.black))
    d.add(String(0, 75, "124 cm", fontName="Helvetica", fontSize=10, fillColor=colors.black))
    d.add(String(385, 74, "4 cm", fontName="Helvetica", fontSize=9, fillColor=colors.black))

    return d


story = []

# Question number + stem
story.append(Paragraph("Soru 19", question_num))
story.append(Spacer(1, 6))

story.append(Paragraph(
    "Aşağıdaki şekilde genişliği 144 cm, yüksekliği 124 cm olan eş iki raflı bir kitaplık verilmiştir.",
    normal
))

story.append(Spacer(1, 10))
story.append(bookshelf_figure())
story.append(Spacer(1, 10))

story.append(Paragraph(
    "Kitaplığın raflarından birine birbirine özdeş kitaplar yatay, diğerine ise dikey olacak şekilde "
    "uç uca ve üst üste aralarında boşluk kalmayacak şekilde yerleştirilecektir.",
    normal
))

story.append(Spacer(1, 10))

story.append(Paragraph(
    "Yerleştirilecek kitapların kenar uzunlukları santimetre cinsinden birbirinden farklı birer tam sayı "
    "olduğuna göre rafları tamamen dolduracak şekilde <b>en az kaç adet kitap kullanılmıştır?</b>",
    normal
))

story.append(Spacer(1, 18))

# Answer choices — 2 columns
choices = [
    ["A)  100", "B)  120"],
    ["C)  200", "D)  240"],
]

t = Table(choices, colWidths=[8*cm, 8*cm], rowHeights=[28, 28])
t.setStyle(TableStyle([
    ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
    ("FONTSIZE", (0, 0), (-1, -1), 12),
    ("VALIGN",   (0, 0), (-1, -1), "MIDDLE"),
    ("LEFTPADDING", (0,0), (-1,-1), 30),
]))
story.append(t)

doc.build(story)
print(f"PDF oluşturuldu: {os.path.abspath(OUTPUT)}")
