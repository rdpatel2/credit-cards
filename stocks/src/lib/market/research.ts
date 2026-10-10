export const metricIds = [
  "trailingEps",
  "forwardEps",
  "trailingPE",
  "forwardPE",
  "pegRatio",
  "priceToBook",
  "priceToSales",
  "marketCap",
  "enterpriseValue",
  "enterpriseToEbitda",
  "dividendYield",
  "beta",
  "profitMargins",
  "operatingMargins",
  "returnOnEquity",
  "revenueGrowth",
  "earningsGrowth",
  "totalRevenue",
  "freeCashflow",
  "debtToEquity",
] as const;
export type MetricId = (typeof metricIds)[number];
export const metrics: Record<
  MetricId,
  {
    label: string;
    format: "money" | "multiple" | "percent" | "number" | "compact";
    description: string;
  }
> = {
  trailingEps: {
    label: "EPS (TTM)",
    format: "money",
    description: "Diluted earnings per share over the trailing twelve months.",
  },
  forwardEps: {
    label: "Forward EPS",
    format: "money",
    description:
      "Provider's forward earnings-per-share estimate; forecast, not reported earnings.",
  },
  trailingPE: {
    label: "P/E (TTM)",
    format: "multiple",
    description:
      "Price divided by trailing earnings per share. Not meaningful for nonpositive earnings.",
  },
  forwardPE: {
    label: "Forward P/E",
    format: "multiple",
    description: "Price divided by estimated forward earnings per share.",
  },
  pegRatio: {
    label: "PEG ratio",
    format: "multiple",
    description: "Provider's price/earnings-to-growth ratio, where available.",
  },
  priceToBook: {
    label: "Price / book",
    format: "multiple",
    description: "Share price relative to book value per share.",
  },
  priceToSales: {
    label: "Price / sales (TTM)",
    format: "multiple",
    description: "Market capitalization divided by trailing revenue.",
  },
  marketCap: {
    label: "Market cap",
    format: "compact",
    description: "Current equity market value in the trading currency.",
  },
  enterpriseValue: {
    label: "Enterprise value",
    format: "compact",
    description:
      "Provider's enterprise valuation, including debt and net of cash.",
  },
  enterpriseToEbitda: {
    label: "EV / EBITDA",
    format: "multiple",
    description: "Enterprise value relative to EBITDA.",
  },
  dividendYield: {
    label: "Dividend yield",
    format: "percent",
    description: "Provider's indicated annual dividend yield.",
  },
  beta: {
    label: "Beta",
    format: "number",
    description: "Provider's measure of stock sensitivity to the market.",
  },
  profitMargins: {
    label: "Profit margin",
    format: "percent",
    description: "Net income relative to revenue.",
  },
  operatingMargins: {
    label: "Operating margin",
    format: "percent",
    description: "Operating income relative to revenue.",
  },
  returnOnEquity: {
    label: "Return on equity",
    format: "percent",
    description: "Net income relative to shareholder equity.",
  },
  revenueGrowth: {
    label: "Revenue growth",
    format: "percent",
    description: "Provider's year-over-year quarterly revenue growth rate.",
  },
  earningsGrowth: {
    label: "Earnings growth",
    format: "percent",
    description: "Provider's year-over-year quarterly earnings growth rate.",
  },
  totalRevenue: {
    label: "Revenue (TTM)",
    format: "compact",
    description:
      "Trailing twelve-month revenue in the financial reporting currency.",
  },
  freeCashflow: {
    label: "Free cash flow",
    format: "compact",
    description:
      "Provider's free cash flow in the financial reporting currency.",
  },
  debtToEquity: {
    label: "Debt / equity",
    format: "percent",
    description: "Total debt relative to equity, displayed as a percentage.",
  },
};
export const defaultMetrics: MetricId[] = [
  "trailingEps",
  "forwardEps",
  "trailingPE",
  "forwardPE",
  "marketCap",
  "priceToBook",
  "dividendYield",
  "profitMargins",
];
export type Fundamentals = {
  symbol: string;
  currency: string;
  financialCurrency: string;
  values: Record<MetricId, number | null>;
  asOf: string;
};
export const sectorIds = [
  "all",
  "technology",
  "financials",
  "healthcare",
  "energy",
  "consumer-discretionary",
  "consumer-staples",
  "industrials",
  "materials",
  "real-estate",
  "utilities",
  "communication-services",
  "macro",
] as const;
export type Sector = (typeof sectorIds)[number];
export const sectorLabels: Record<Sector, string> = {
  all: "All news",
  technology: "Technology",
  financials: "Financials",
  healthcare: "Healthcare",
  energy: "Energy",
  "consumer-discretionary": "Consumer discretionary",
  "consumer-staples": "Consumer staples",
  industrials: "Industrials",
  materials: "Materials",
  "real-estate": "Real estate",
  utilities: "Utilities",
  "communication-services": "Communication services",
  macro: "Market & economy",
};
const sectorTerms: Record<Exclude<Sector, "all" | "macro">, RegExp> = {
  technology:
    /\b(apple|aapl|microsoft|msft|nvidia|nvda|amd|semiconductors?|chips?|software|artificial intelligence|ai|technology|tech|cybersecurity|oracle|palantir|micron)\b/i,
  financials:
    /\b(banks?|banking|jpmorgan|goldman|visa|mastercard|insurance|insurers?|fintech|blackrock|credit card|wells fargo|citigroup)\b/i,
  healthcare:
    /\b(healthcare|health care|pharma|biotech|fda|drug|drugs|pfizer|eli lilly|hospitals?|medical|medicare|novo nordisk)\b/i,
  energy:
    /\b(oil|opec|crude|petroleum|exxon|chevron|natural gas|energy stocks|refiner|refinery)\b/i,
  "consumer-discretionary":
    /\b(amazon|amzn|tesla|tsla|automakers?|electric vehicles?|retail|retailer|nike|restaurants?|hotels?|home depot|consumer spending)\b/i,
  "consumer-staples":
    /\b(walmart|costco|coca.cola|pepsico|procter|grocer|groceries|food|beverages?|tobacco|staples)\b/i,
  industrials:
    /\b(boeing|airbus|airlines?|defense|aerospace|caterpillar|manufacturing|industrial|railroads?|shipping|logistics)\b/i,
  materials:
    /\b(mining|miners?|copper|steel|aluminum|aluminium|lithium|chemicals?|materials stocks|gold miners)\b/i,
  "real-estate":
    /\b(real estate|reit|reits|property|commercial buildings?|housing|mortgage|homebuilders?)\b/i,
  utilities:
    /\b(utilities|utility|electricity|power grid|electric power|duke energy|nextera)\b/i,
  "communication-services":
    /\b(alphabet|google|googl|meta|facebook|netflix|disney|telecom|streaming|advertising|verizon|t.mobile|comcast)\b/i,
};
export function classifySectors(text: string): Exclude<Sector, "all">[] {
  const matched = Object.entries(sectorTerms)
    .filter(([, pattern]) => pattern.test(text))
    .map(([sector]) => sector as Exclude<Sector, "all">);
  return matched.length ? matched : ["macro"];
}
