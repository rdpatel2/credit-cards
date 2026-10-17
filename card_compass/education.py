"""Plain-language definitions shared by investment labels and help."""

TERMS = {
    'cost basis': 'What your remaining shares cost, including recorded purchase fees. It is not their current market value.',
    'average cost': 'The remaining cost basis divided by shares held. Recorded sales use FIFO: the oldest purchases are sold first.',
    'realized gain': 'Recorded sale proceeds minus the cost of the shares sold and sale fees. This is not an account return or a complete tax result.',
    'market value': 'Shares held multiplied by a usable public quote. Totals exclude demo, cached, unavailable and non-USD quotes; quotes may be delayed.',
    'analyst consensus': 'Counts of analysts expressing sell, hold or buy opinions. These are third-party opinions, not a recommendation from this app.',
    'demo data': 'Synthetic examples for exploring the app. They are not current prices or events and never value your personal holdings.',
    'fifo': 'First in, first out: recorded sales consume your oldest purchase lots first. This app does not submit orders to a broker.',
    'trailingEps': 'Earnings per share (EPS) over the trailing twelve months (TTM): reported profit allocated to each share.',
    'forwardEps': 'Expected earnings per share based on provider estimates. Estimates can change and are not guaranteed.',
    'trailingPE': 'Price divided by trailing earnings per share (P/E). Unavailable when earnings are not positive; it does not determine whether to buy.',
    'forwardPE': 'Price divided by estimated future earnings per share. Its usefulness depends on the estimate.',
    'pegRatio': 'The price-to-earnings ratio compared with an earnings-growth estimate. Provider methods and growth assumptions vary.',
    'priceToBook': 'Share price compared with accounting book value per share. Book value is not a sale price for the business.',
    'priceToSales': 'Company market value compared with revenue. It does not account for costs or profitability.',
    'marketCap': 'The market value of all outstanding shares, rather than the value of your own holding.',
    'enterpriseValue': 'A measure of business value that adjusts market capitalization for debt and cash.',
    'enterpriseToEbitda': 'Enterprise value divided by earnings before interest, taxes, depreciation and amortization. EBITDA is not cash flow.',
    'dividendYield': 'Annual dividends relative to share price. This is a provider metric, not dividends received in your account; payouts can change.',
    'beta': 'How a security has moved relative to a market benchmark. Historical beta does not predict future losses.',
    'profitMargins': 'Profit as a percentage of revenue, using the provider’s reported period.',
    'operatingMargins': 'Operating profit as a percentage of revenue, before some financing costs and taxes.',
    'returnOnEquity': 'Profit relative to accounting shareholder equity. It is not the return earned on your shares.',
    'revenueGrowth': 'Change in revenue for the comparison period supplied by the provider.',
    'earningsGrowth': 'Change in earnings for the comparison period supplied by the provider.',
    'totalRevenue': 'Sales reported over the trailing twelve months (TTM), in the company’s reporting currency.',
    'freeCashflow': 'Cash generated after capital spending, as supplied by the provider. Definitions and reporting periods can vary.',
    'debtToEquity': 'Debt compared with accounting shareholder equity. A displayed 100% means debt equals equity.',
}


def definition(label):
    normalized = label.casefold()
    for words, key in [('basis', 'cost basis'), ('average cost', 'average cost'),
                       ('realized', 'realized gain'), ('market value', 'market value'),
                       ('analyst', 'analyst consensus'), ('demo', 'demo data')]:
        if words in normalized:
            return TERMS[key]
    return ''
