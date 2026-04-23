const fs = require("fs");
const {
  Document, Packer, Paragraph, TextRun, Table, TableRow, TableCell,
  Header, Footer, AlignmentType, HeadingLevel, BorderStyle, WidthType,
  ShadingType, PageNumber, PageBreak, LevelFormat, TabStopType, TabStopPosition,
} = require("docx");

// ── Colors ──
const NAVY = "1B3A5C";
const BLUE = "2E75B6";
const LIGHT_BLUE = "D5E8F0";
const LIGHT_GRAY = "F2F2F2";
const GREEN = "2E8B57";
const RED = "C0392B";
const DARK = "333333";
const MEDIUM = "555555";

// ── Helpers ──
const border = { style: BorderStyle.SINGLE, size: 1, color: "CCCCCC" };
const borders = { top: border, bottom: border, left: border, right: border };
const cellMargins = { top: 60, bottom: 60, left: 100, right: 100 };

function headerCell(text, width) {
  return new TableCell({
    borders,
    width: { size: width, type: WidthType.DXA },
    shading: { fill: NAVY, type: ShadingType.CLEAR },
    margins: cellMargins,
    verticalAlign: "center",
    children: [new Paragraph({ alignment: AlignmentType.CENTER, children: [
      new TextRun({ text, bold: true, color: "FFFFFF", font: "Arial", size: 20 }),
    ]})],
  });
}

function dataCell(text, width, opts = {}) {
  const color = opts.color || DARK;
  const align = opts.align || AlignmentType.CENTER;
  const bold = opts.bold || false;
  const fill = opts.fill || undefined;
  const shading = fill ? { fill, type: ShadingType.CLEAR } : undefined;
  return new TableCell({
    borders,
    width: { size: width, type: WidthType.DXA },
    shading,
    margins: cellMargins,
    children: [new Paragraph({ alignment: align, children: [
      new TextRun({ text, color, font: "Arial", size: 19, bold }),
    ]})],
  });
}

function sectionTitle(text) {
  return new Paragraph({
    heading: HeadingLevel.HEADING_2,
    spacing: { before: 300, after: 150 },
    children: [new TextRun({ text, font: "Arial", size: 26, bold: true, color: NAVY })],
  });
}

function bodyText(text) {
  return new Paragraph({
    spacing: { after: 120 },
    children: [new TextRun({ text, font: "Arial", size: 21, color: DARK })],
  });
}

function boldLabel(label, value) {
  return new Paragraph({
    spacing: { after: 80 },
    children: [
      new TextRun({ text: label, font: "Arial", size: 21, bold: true, color: NAVY }),
      new TextRun({ text: value, font: "Arial", size: 21, color: DARK }),
    ],
  });
}

// ── Table widths ──
const TABLE_W = 9360;
const COL3 = [3120, 3120, 3120];
const COL4 = [2340, 2340, 2340, 2340];
const COL6 = [1800, 1200, 1200, 1200, 1560, 2400];
const COL7 = [1200, 1000, 1000, 1000, 1200, 1560, 2400];

// ════════════════════════════════════════════════════════════════════════
// PAGE 1: Agent Architecture Overview
// ════════════════════════════════════════════════════════════════════════
const page1 = [
  // Title
  new Paragraph({
    spacing: { after: 60 },
    children: [new TextRun({ text: "POLYMARKET TRADING BOT", font: "Arial", size: 36, bold: true, color: NAVY })],
  }),
  new Paragraph({
    spacing: { after: 200 },
    border: { bottom: { style: BorderStyle.SINGLE, size: 6, color: BLUE, space: 1 } },
    children: [new TextRun({ text: "Multi-Agent Orchestration Report  |  21 Mart 2026", font: "Arial", size: 22, color: MEDIUM })],
  }),

  sectionTitle("1. Sistem Mimarisi"),
  bodyText("Bot, 60 saniyelik dongulerle calisan bir multi-agent orchestration sistemi kullanir. Her dongude 3 bagimsiz agent koordineli calisarak trade kararlari uretir. Hybrid iletisim modeli: Research ve Signal agent paralel calisir, sonuclari birlestirilir, ardindan Reviewer agent sirali olarak her sinyali degerlendirir."),

  sectionTitle("2. Agent Gorev Dagilimi"),

  // Agent table
  new Table({
    width: { size: TABLE_W, type: WidthType.DXA },
    columnWidths: COL3,
    rows: [
      new TableRow({ children: [
        headerCell("ResearchAgent", COL3[0]),
        headerCell("SignalAgent", COL3[1]),
        headerCell("ReviewerAgent", COL3[2]),
      ]}),
      new TableRow({ children: [
        dataCell("Piyasa durumu, whale aktivitesi, smart money, regime tespiti, on-chain veriler, sentiment analizi", COL3[0], { align: AlignmentType.LEFT }),
        dataCell("6-model Bayesian pipeline: Bayesian + Edge + Spread + Stoikov + Kelly + Monte Carlo", COL3[1], { align: AlignmentType.LEFT }),
        dataCell("Claude API ile her trade onerisi APPROVE / VETO / REDUCE. API yoksa rule-based fallback", COL3[2], { align: AlignmentType.LEFT }),
      ]}),
      new TableRow({ children: [
        dataCell("Timeout: 25s | Paralel", COL3[0], { fill: LIGHT_GRAY }),
        dataCell("Timeout: 20s | Paralel", COL3[1], { fill: LIGHT_GRAY }),
        dataCell("Timeout: 30s | Sirali", COL3[2], { fill: LIGHT_GRAY }),
      ]}),
    ],
  }),

  new Paragraph({ spacing: { before: 200 } }),
  sectionTitle("3. 30 Dakikalik Dongu Akisi"),
  bodyText("Her 60 saniyede bir dongu calisir. 30 dakikada yaklasik 30 dongu tamamlanir:"),

  new Table({
    width: { size: TABLE_W, type: WidthType.DXA },
    columnWidths: [1800, 2000, 5560],
    rows: [
      new TableRow({ children: [
        headerCell("Faz", 1800),
        headerCell("Sure", 2000),
        headerCell("Islem", 5560),
      ]}),
      new TableRow({ children: [
        dataCell("PHASE 1", 1800, { bold: true, color: BLUE }),
        dataCell("~3-5s", 2000),
        dataCell("ResearchAgent + SignalAgent PARALEL calisir. Spot veri, whale, smart trader, 6-model sinyal uretimi", 5560, { align: AlignmentType.LEFT }),
      ]}),
      new TableRow({ children: [
        dataCell("PHASE 2", 1800, { bold: true, color: BLUE }),
        dataCell("~0.1s", 2000),
        dataCell("Sinyaller research context ile zenginlestirilir. Confluence score (0-1) ve risk flag hesaplanir", 5560, { align: AlignmentType.LEFT }),
      ]}),
      new TableRow({ children: [
        dataCell("PHASE 3", 1800, { bold: true, color: BLUE }),
        dataCell("~2-5s", 2000),
        dataCell("ReviewerAgent her sinyali degerlendirir: APPROVE / VETO / REDUCE. Claude API veya rule-based", 5560, { align: AlignmentType.LEFT }),
      ]}),
      new TableRow({ children: [
        dataCell("EXECUTE", 1800, { bold: true, color: GREEN }),
        dataCell("~1-2s", 2000),
        dataCell("Onaylanan sinyaller LiveGate (11 nokta kontrol) sonrasi Polymarket CLOB API ile execute edilir", 5560, { align: AlignmentType.LEFT }),
      ]}),
    ],
  }),

  new Paragraph({ spacing: { before: 200 } }),
  sectionTitle("4. Yeni Ozellikler"),
  boldLabel("Confluence Score: ", "Bagimsiz sinyallerin (edge, whale, smart money, regime, volume) ne kadar ayni yonde oldugunu 0-1 arasi olcer. >0.7 yuksek guven, <0.4 dusuk guven."),
  boldLabel("Risk Flag Sistemi: ", "COUNTER_REGIME, WHALE_OPPOSITION, RSI_OVERBOUGHT/OVERSOLD, REGIME_OVEREXTENDED, LOW_VOLUME, THIN_EDGE otomatik tespit edilir."),
  boldLabel("Reviewer Veto: ", "NO direction + edge <0.15 otomatik VETO. 3+ risk flag REDUCE (%50). RSI extreme + dusuk confluence VETO."),
];

// ════════════════════════════════════════════════════════════════════════
// PAGE 2: Trade Performance Analysis
// ════════════════════════════════════════════════════════════════════════
const page2 = [
  new Paragraph({ children: [new PageBreak()] }),

  sectionTitle("5. Trade Performans Ozeti"),
  bodyText("431 kapanan pozisyon uzerinden toplam performans analizi. Baslangic sermayesi $500 USDC."),

  // Summary KPIs
  new Table({
    width: { size: TABLE_W, type: WidthType.DXA },
    columnWidths: COL4,
    rows: [
      new TableRow({ children: [
        headerCell("Toplam Trade", COL4[0]),
        headerCell("Win Rate", COL4[1]),
        headerCell("Net PnL", COL4[2]),
        headerCell("Mevcut Capital", COL4[3]),
      ]}),
      new TableRow({ children: [
        dataCell("429", COL4[0], { bold: true }),
        dataCell("%59.1 (233W/161L)", COL4[1], { bold: true, color: GREEN }),
        dataCell("+$305.65", COL4[2], { bold: true, color: GREEN }),
        dataCell("$0.31", COL4[3], { bold: true, color: RED }),
      ]}),
    ],
  }),

  new Paragraph({ spacing: { before: 80 } }),

  new Table({
    width: { size: TABLE_W, type: WidthType.DXA },
    columnWidths: COL4,
    rows: [
      new TableRow({ children: [
        headerCell("Ort. Win", COL4[0]),
        headerCell("Ort. Loss", COL4[1]),
        headerCell("Win PnL", COL4[2]),
        headerCell("Loss PnL", COL4[3]),
      ]}),
      new TableRow({ children: [
        dataCell("+$3.42", COL4[0], { color: GREEN }),
        dataCell("-$3.06", COL4[1], { color: RED }),
        dataCell("+$797.83", COL4[2], { color: GREEN }),
        dataCell("-$492.18", COL4[3], { color: RED }),
      ]}),
    ],
  }),

  new Paragraph({ spacing: { before: 200 } }),
  sectionTitle("6. Coin Bazli Analiz"),

  new Table({
    width: { size: TABLE_W, type: WidthType.DXA },
    columnWidths: [1600, 1200, 1200, 1200, 1560, 2600],
    rows: [
      new TableRow({ children: [
        headerCell("Coin", 1600),
        headerCell("Win", 1200),
        headerCell("Loss", 1200),
        headerCell("WR%", 1200),
        headerCell("Net PnL", 1560),
        headerCell("Durum", 2600),
      ]}),
      ...([
        ["Bitcoin", "58", "43", "57%", "+$89.29", "En cok islem, istikrarli"],
        ["XRP", "43", "29", "60%", "+$61.77", "Iyi WR, guvenilir"],
        ["Ethereum", "47", "37", "56%", "+$54.61", "Hacim yuksek, orta WR"],
        ["Solana", "44", "36", "55%", "+$38.92", "Orta performans"],
        ["Hyperliquid", "19", "5", "79%", "+$34.72", "En yuksek WR, az islem"],
        ["Dogecoin", "18", "8", "69%", "+$24.51", "Iyi WR, dusuk hacim"],
        ["BNB", "4", "3", "57%", "+$1.83", "Az veri, belirsiz"],
      ].map((row, i) => new TableRow({ children: [
        dataCell(row[0], 1600, { bold: true, align: AlignmentType.LEFT, fill: i % 2 ? LIGHT_GRAY : undefined }),
        dataCell(row[1], 1200, { color: GREEN, fill: i % 2 ? LIGHT_GRAY : undefined }),
        dataCell(row[2], 1200, { color: RED, fill: i % 2 ? LIGHT_GRAY : undefined }),
        dataCell(row[3], 1200, { fill: i % 2 ? LIGHT_GRAY : undefined }),
        dataCell(row[4], 1560, { color: GREEN, fill: i % 2 ? LIGHT_GRAY : undefined }),
        dataCell(row[5], 2600, { align: AlignmentType.LEFT, fill: i % 2 ? LIGHT_GRAY : undefined }),
      ]})),
    ],
  }),

  new Paragraph({ spacing: { before: 200 } }),
  sectionTitle("7. Zaman Dilimi Analizi"),

  new Table({
    width: { size: TABLE_W, type: WidthType.DXA },
    columnWidths: [1800, 1500, 1500, 1500, 1560, 1500],
    rows: [
      new TableRow({ children: [
        headerCell("Timeframe", 1800),
        headerCell("Win", 1500),
        headerCell("Loss", 1500),
        headerCell("WR%", 1500),
        headerCell("Net PnL", 1560),
        headerCell("Sinyal", 1500),
      ]}),
      new TableRow({ children: [
        dataCell("5 dakika", 1800, { bold: true }),
        dataCell("191", 1500, { color: GREEN }),
        dataCell("102", 1500, { color: RED }),
        dataCell("65%", 1500, { bold: true, color: GREEN }),
        dataCell("+$346.97", 1560, { color: GREEN, bold: true }),
        dataCell("GOLD", 1500, { color: GREEN, bold: true }),
      ]}),
      new TableRow({ children: [
        dataCell("15 dakika", 1800, { bold: true, fill: LIGHT_GRAY }),
        dataCell("42", 1500, { color: GREEN, fill: LIGHT_GRAY }),
        dataCell("56", 1500, { color: RED, fill: LIGHT_GRAY }),
        dataCell("43%", 1500, { bold: true, color: RED, fill: LIGHT_GRAY }),
        dataCell("-$35.32", 1560, { color: RED, bold: true, fill: LIGHT_GRAY }),
        dataCell("ZAYIF", 1500, { color: RED, bold: true, fill: LIGHT_GRAY }),
      ]}),
      new TableRow({ children: [
        dataCell("4 saat", 1800, { bold: true }),
        dataCell("0", 1500),
        dataCell("3", 1500, { color: RED }),
        dataCell("0%", 1500, { color: RED }),
        dataCell("-$6.00", 1560, { color: RED }),
        dataCell("DEVRE DISI", 1500, { color: RED }),
      ]}),
    ],
  }),

  new Paragraph({ spacing: { before: 150 } }),
  boldLabel("Kritik Bulgu: ", "5 dakikalik marketler tum karlilik kaynagi (+$347). 15 dakika net zarar (-$35). 4 saat tamamen basarisiz. Subagent reviewer bu veriyi kullanarak 15m/4h trade lerde otomatik REDUCE veya VETO uygulayabilir."),
];

// ════════════════════════════════════════════════════════════════════════
// PAGE 3: Integration Test Results + Recommendations
// ════════════════════════════════════════════════════════════════════════
const page3 = [
  new Paragraph({ children: [new PageBreak()] }),

  sectionTitle("8. Entegrasyon Test Sonuclari"),
  bodyText("Subagent sistemi 8 kapsamli entegrasyon testinden basariyla gecti:"),

  new Table({
    width: { size: TABLE_W, type: WidthType.DXA },
    columnWidths: [800, 3560, 2500, 2500],
    rows: [
      new TableRow({ children: [
        headerCell("#", 800),
        headerCell("Test", 3560),
        headerCell("Sonuc", 2500),
        headerCell("Detay", 2500),
      ]}),
      ...([
        ["1", "Base Agent Lifecycle", "PASS", "IDLE > RUNNING > COMPLETED/FAIL/TIMEOUT"],
        ["2", "ResearchResult Data Structures", "PASS", "16 alan, aggregate sentiment OK"],
        ["3", "Confluence Score ve Risk Flags", "PASS", "Aligned=0.91, Counter=0.42, 6 flag"],
        ["4", "Reviewer Rule-Based Logic", "PASS", "APPROVE/VETO/REDUCE dogru karar"],
        ["5", "Batch Result Filtering", "PASS", "2/3 onaylandi (VETO filtresi OK)"],
        ["6", "Full Coordinator Pipeline", "PASS", "2 sinyal > 2 onay, 2296ms pipeline"],
        ["7", "Empty Candidates Handler", "PASS", "0 aday > 0ms, graceful skip"],
        ["8", "Review Summary Format", "PASS", "306 char, tum alanlar mevcut"],
      ].map((row, i) => new TableRow({ children: [
        dataCell(row[0], 800, { fill: i % 2 ? LIGHT_GRAY : undefined }),
        dataCell(row[1], 3560, { align: AlignmentType.LEFT, fill: i % 2 ? LIGHT_GRAY : undefined }),
        dataCell(row[2], 2500, { color: GREEN, bold: true, fill: i % 2 ? LIGHT_GRAY : undefined }),
        dataCell(row[3], 2500, { align: AlignmentType.LEFT, fill: i % 2 ? LIGHT_GRAY : undefined }),
      ]})),
    ],
  }),

  new Paragraph({ spacing: { before: 200 } }),
  sectionTitle("9. Reviewer Agent Karar Matrisi"),
  bodyText("Asagidaki tablo reviewer agent in hangi durumlarda hangi karari verdigini gosterir:"),

  new Table({
    width: { size: TABLE_W, type: WidthType.DXA },
    columnWidths: [3000, 1500, 4860],
    rows: [
      new TableRow({ children: [
        headerCell("Kosul", 3000),
        headerCell("Karar", 1500),
        headerCell("Gerekcesi", 4860),
      ]}),
      ...([
        ["YES + edge >0.08 + dusuk risk", "APPROVE", "Standard YES onay, tarihsel %72 WR destekliyor"],
        ["NO + edge <0.15", "VETO", "NO tarihsel %33 WR, dusuk edge ile zarar riski cok yuksek"],
        ["Regime >0.75 + counter-trade", "VETO", "Asiri uzanmis rejimde ters pozisyon bounce riski"],
        ["Whale muhalefet + ince edge", "VETO", "Buyuk oyuncular aksi yonde, bilgi dezavantaji"],
        ["3+ risk flag", "REDUCE 50%", "Birden fazla uyari, pozisyon kucultme ile risk azaltma"],
        ["Confluence <0.4", "REDUCE 30%", "Dusuk sinyal uyumu, temkinli pozisyon"],
        ["RSI extreme + dusuk confluence", "VETO", "Asiri alim/satim + zayif sinyal = geri donus olasi"],
      ].map((row, i) => new TableRow({ children: [
        dataCell(row[0], 3000, { align: AlignmentType.LEFT, fill: i % 2 ? LIGHT_GRAY : undefined }),
        dataCell(row[1], 1500, {
          bold: true,
          color: row[1] === "APPROVE" ? GREEN : row[1].startsWith("VETO") ? RED : "D4A017",
          fill: i % 2 ? LIGHT_GRAY : undefined,
        }),
        dataCell(row[2], 4860, { align: AlignmentType.LEFT, fill: i % 2 ? LIGHT_GRAY : undefined }),
      ]})),
    ],
  }),

  new Paragraph({ spacing: { before: 200 } }),
  sectionTitle("10. Oneriler ve Sonraki Adimlar"),
  boldLabel("5m Odaklanma: ", "Tum karlilik 5 dakikalik marketlerden geliyor (+$347). 15m marketlerde REDUCE, 4h marketlerde VETO default olmali."),
  boldLabel("Reviewer API Aktivasyonu: ", "ANTHROPIC_API_KEY env degiskeni ile Claude API aktif edildiginde reviewer daha nuansli kararlar verebilir. Ozellikle borderline case lerde (edge 0.08-0.12 arasi)."),
  boldLabel("Confluence Threshold: ", "Minimum confluence 0.5 olarak ayarlanmali. 0.5 alti sinyaller otomatik VETO veya REDUCE ile filtrelenmeli."),
  boldLabel("Hyperliquid Firsati: ", "%79 WR ile en basarili coin. Daha fazla Hyperliquid marketi taranmali."),

  new Paragraph({ spacing: { before: 200 } }),
  new Paragraph({
    border: { top: { style: BorderStyle.SINGLE, size: 4, color: BLUE, space: 1 } },
    spacing: { before: 100 },
    children: [
      new TextRun({ text: "Rapor Tarihi: ", font: "Arial", size: 18, color: MEDIUM }),
      new TextRun({ text: "21 Mart 2026  |  ", font: "Arial", size: 18, color: MEDIUM }),
      new TextRun({ text: "Sistem: Multi-Agent Orchestration v2  |  ", font: "Arial", size: 18, color: MEDIUM }),
      new TextRun({ text: "429 Trade Analizi", font: "Arial", size: 18, bold: true, color: NAVY }),
    ],
  }),
];

// ════════════════════════════════════════════════════════════════════════
// BUILD DOCUMENT
// ════════════════════════════════════════════════════════════════════════
const doc = new Document({
  styles: {
    default: { document: { run: { font: "Arial", size: 22 } } },
    paragraphStyles: [
      { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 32, bold: true, font: "Arial", color: NAVY },
        paragraph: { spacing: { before: 240, after: 240 }, outlineLevel: 0 } },
      { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
        run: { size: 26, bold: true, font: "Arial", color: NAVY },
        paragraph: { spacing: { before: 180, after: 120 }, outlineLevel: 1 } },
    ],
  },
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 },
        margin: { top: 1080, right: 1260, bottom: 1080, left: 1260 },
      },
    },
    headers: {
      default: new Header({ children: [new Paragraph({
        spacing: { after: 40 },
        border: { bottom: { style: BorderStyle.SINGLE, size: 2, color: BLUE, space: 1 } },
        children: [
          new TextRun({ text: "Polymarket Bot  |  Multi-Agent Report", font: "Arial", size: 16, color: MEDIUM }),
          new TextRun({ text: "\t21 Mart 2026", font: "Arial", size: 16, color: MEDIUM }),
        ],
        tabStops: [{ type: TabStopType.RIGHT, position: TabStopPosition.MAX }],
      })] }),
    },
    footers: {
      default: new Footer({ children: [new Paragraph({
        alignment: AlignmentType.CENTER,
        children: [
          new TextRun({ text: "Sayfa ", font: "Arial", size: 16, color: MEDIUM }),
          new TextRun({ children: [PageNumber.CURRENT], font: "Arial", size: 16, color: MEDIUM }),
          new TextRun({ text: " / 3", font: "Arial", size: 16, color: MEDIUM }),
        ],
      })] }),
    },
    children: [...page1, ...page2, ...page3],
  }],
});

Packer.toBuffer(doc).then(buffer => {
  const path = "/sessions/hopeful-fervent-cerf/mnt/Polymarket/Multi_Agent_Report.docx";
  fs.writeFileSync(path, buffer);
  console.log(`Document saved to ${path} (${buffer.length} bytes)`);
});
