"""Folio headline sector tags; inferred from keywords, not publisher classifications."""
import re

SECTORS = {'all': 'All market news',
 'technology': 'Technology',
 'financials': 'Financials',
 'healthcare': 'Healthcare',
 'energy': 'Energy',
 'consumer-discretionary': 'Consumer discretionary',
 'consumer-staples': 'Consumer staples',
 'industrials': 'Industrials',
 'materials': 'Materials',
 'real-estate': 'Real estate',
 'utilities': 'Utilities',
 'communication-services': 'Communication services',
 'macro': 'Market and economy'}
PATTERNS = {'technology': '\\b(apple|aapl|microsoft|msft|nvidia|nvda|amd|semiconductors?|chips?|software|artificial '
               'intelligence|ai|technology|tech|cybersecurity|oracle|palantir|micron)\\b',
 'financials': '\\b(banks?|banking|jpmorgan|goldman|visa|mastercard|insurance|insurers?|fintech|blackrock|credit '
               'card|wells fargo|citigroup)\\b',
 'healthcare': '\\b(healthcare|health '
               'care|pharma|biotech|fda|drug|drugs|pfizer|eli '
               'lilly|hospitals?|medical|medicare|novo nordisk)\\b',
 'energy': '\\b(oil|opec|crude|petroleum|exxon|chevron|natural gas|energy '
           'stocks|refiner|refinery)\\b',
 'consumer-discretionary': '\\b(amazon|amzn|tesla|tsla|automakers?|electric '
                           'vehicles?|retail|retailer|nike|restaurants?|hotels?|home '
                           'depot|consumer spending)\\b',
 'consumer-staples': '\\b(walmart|costco|coca.cola|pepsico|procter|grocer|groceries|food|beverages?|tobacco|staples)\\b',
 'industrials': '\\b(boeing|airbus|airlines?|defense|aerospace|caterpillar|manufacturing|industrial|railroads?|shipping|logistics)\\b',
 'materials': '\\b(mining|miners?|copper|steel|aluminum|aluminium|lithium|chemicals?|materials '
              'stocks|gold miners)\\b',
 'real-estate': '\\b(real estate|reit|reits|property|commercial '
                'buildings?|housing|mortgage|homebuilders?)\\b',
 'utilities': '\\b(utilities|utility|electricity|power grid|electric '
              'power|duke energy|nextera)\\b',
 'communication-services': '\\b(alphabet|google|googl|meta|facebook|netflix|disney|telecom|streaming|advertising|verizon|t.mobile|comcast)\\b'}

def sectors(title):
    matches = [key for key, pattern in PATTERNS.items() if re.search(pattern, title, re.I)]
    return matches or ["macro"]
